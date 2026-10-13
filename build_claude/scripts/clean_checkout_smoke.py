"""Verify a committed checkout using its own uv environment and synthetic data only.

The parent uses only the standard library. The exercise runs from the clone with
the clone's interpreter; it cannot import the original app or use its credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def isolated_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MRT_", "OPENAI_", "PYTHON", "UV_"))
           and k not in ("VIRTUAL_ENV", "CONDA_PREFIX")}
    env.update(OPENAI_API_KEY="", OPENAI_MODEL="gpt-6-luna",
               MRT_FAKE_LLM="1", PYTHONNOUSERSITE="1")
    return env


def exercise():
    import httpx
    import pymupdf

    root = Path.cwd().resolve()
    require(Path(sys.executable).parent.parent == root / ".venv", "not using clone venv")
    data = root / "data" / "synthetic-smoke"
    data.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = isolated_env()
    env.update(MRT_DATA_DIR=str(data), MRT_CONCURRENCY="1", MRT_MAX_PAGES="0",
               MRT_FAIL_AT_PAGE="5", MRT_PORT=str(port))
    server = None
    log = (data / "server.log").open("w")
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False)
    checks = []

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def start():
        nonlocal server
        server = subprocess.Popen([sys.executable, "-m", "app"], cwd=root, env=env,
                                  stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(server.poll() is None, "server exited during startup")
            try:
                if request("GET", "/api/meta").json().get("fake_llm") is True:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("server readiness timeout")

    def stop():
        nonlocal server
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server = None

    def pdf(pages, label):
        with pymupdf.open() as document:
            for number in range(1, pages + 1):
                document.new_page().insert_text((72, 72), f"SYNTHETIC ONLY {label} page {number}")
            return document.tobytes()

    def wait_status(doc_id, expected):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            rows = request("GET", "/api/documents").json()
            row = next(d for d in rows if d["id"] == doc_id)
            if row["status"] == expected:
                return row
            if expected == "done" and row["status"] == "failed":
                raise RuntimeError(f"synthetic document failed: {row.get('error')}")
            time.sleep(0.1)
        raise RuntimeError(f"document did not reach {expected}")

    def rows(sql, doc_id):
        with sqlite3.connect(f"file:{data / 'app.db'}?mode=ro", uri=True) as conn:
            return conn.execute(sql, (doc_id,)).fetchall()

    try:
        start()
        require("text/html" in request("GET", "/").headers["content-type"], "missing UI")
        payload = pdf(4, "normal")
        docs = request("POST", "/api/documents", files=[
            ("files", ("synthetic-four.pdf", payload, "application/pdf")),
            ("files", ("synthetic-three.pdf", pdf(3, "second"), "application/pdf")),
        ]).json()
        require(len(docs) == 2, "multi-upload result count")
        for doc in docs:
            wait_status(doc["id"], "done")
        repeated = request("POST", "/api/documents", files={
            "files": ("synthetic-four.pdf", payload, "application/pdf")}).json()[0]
        require(repeated["id"] == docs[0]["id"] and repeated["existing"], "upload idempotency")
        checks.append("two uploads complete; identical upload reused")

        items = request("GET", "/api/timeline?include_duplicates=1").json()
        require(bool(items), "timeline empty")
        record = request("GET", f"/api/records/{items[0]['id']}").json()
        require(bool(record["pages"]), "record has no source pages")
        for page in record["pages"]:
            require(request("GET", page["image_url"]).content.startswith(b"\xff\xd8"), "invalid JPEG")
        path = f"/api/records/{record['id']}"
        edited = request("PATCH", path, json={"title": "Synthetic reviewer edit"}).json()
        require(edited["edited"] and edited["title"] == "Synthetic reviewer edit", "save failed")
        stop()
        start()
        persisted = request("GET", path).json()
        require(persisted["title"] == "Synthetic reviewer edit" and persisted["edited"], "restart lost edit")
        reset = request("DELETE", path + "/overrides").json()
        require(reset["title"] == record["title"] and not reset["edited"], "reset failed")
        checks.append("timeline/source JPEGs; edit/restart/persist/reset")

        retry_id = request("POST", "/api/documents", files={"files": (
            "synthetic-retry.pdf", pdf(12, "retry"), "application/pdf")}).json()[0]["id"]
        wait_status(retry_id, "failed")
        page_sql = "SELECT page_no,status,attempts,text_hash FROM pages WHERE doc_id=? ORDER BY page_no"
        before = rows(page_sql, retry_id)
        require(len(before) == 12 and [r[0] for r in before if r[1] == "done"] == [1, 2, 3, 4],
                "failure hook did not preserve exactly four completed pages")
        require(before[4][1] == "failed", "page five must fail")
        pid = server.pid
        request("POST", f"/api/documents/{retry_id}/retry")
        wait_status(retry_id, "done")
        after = rows(page_sql, retry_id)
        require(server.pid == pid and after[:4] == before[:4], "retry repeated completed pages or restarted")
        require(len(after) == 12 and all(r[1] == "done" for r in after), "retry incomplete")
        require(after[4][2] == 2 and all(r[2] == 1 for r in after[5:]), "retry attempt counts incorrect")
        # Regrouping refreshes boundary metadata timestamps even when a done
        # summary is reused. Compare the actual summary fields and content hash.
        record_sql = ("SELECT id,status,title,date_of_service,date_confidence,record_type,provider,"
                      "facility,summary,key_findings_json,flags_json,content_hash "
                      "FROM records WHERE doc_id=? ORDER BY id")
        summaries = rows(record_sql, retry_id)
        require(bool(summaries) and all(r[1] == "done" for r in summaries), "summaries incomplete")
        request("POST", f"/api/documents/{retry_id}/retry")
        wait_status(retry_id, "done")
        require(rows(page_sql, retry_id) == after, "completed retry changed pages")
        require(rows(record_sql, retry_id) == summaries, "completed retry changed summaries")
        checks.append("same-process retry: 4 saved -> 12 done; completed pages/summaries unchanged")
        print(json.dumps({"result": "PASS", "checks": checks}), flush=True)
    finally:
        stop()
        client.close()
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--exercise", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.exercise:
        exercise()
        return
    if not args.repo or not args.commit:
        parser.error("--repo and --commit are required")
    repo = args.repo.resolve()
    scratch = (args.scratch_root or repo / "build_claude/data/task5").resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    require(uv, "uv is required")
    env = isolated_env()
    sha = subprocess.check_output(["git", "rev-parse", args.commit + "^{commit}"], cwd=repo, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="clean-checkout-", dir=scratch) as temp:
        clone = Path(temp) / "repo"
        log_path = scratch / f"clean-checkout-{sha[:12]}.log"
        with log_path.open("w") as log:
            def run(command, cwd):
                result = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=240)
                log.flush()
                require(result.returncode == 0, f"{command[0]} failed ({result.returncode}); see {log_path}")
            run(["git", "clone", "--no-hardlinks", "--no-checkout", str(repo), str(clone)], scratch)
            run(["git", "checkout", "--detach", sha], clone)
            app = clone / "build_claude"
            require(not (app / ".env").exists() and not (clone / ".env").exists(), "checkout contains .env")
            run([uv, "venv", "--python", "3.12", ".venv"], app)
            python = str(app / ".venv/bin/python")
            run([uv, "pip", "install", "--python", python, "-r", "requirements.txt"], app)
            run([python, "-m", "scripts.clean_checkout_smoke", "--exercise"], app)
        print(json.dumps({"result": "PASS", "commit": sha, "log": str(log_path)}))


if __name__ == "__main__":
    main()
