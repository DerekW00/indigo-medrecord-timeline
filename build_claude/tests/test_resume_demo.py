"""Real HTTP/worker recovery tests. Every model call is fake and counted.

Reusable server helper also supports the browser rehearsal. Scratch stores stay
under this checkout's ignored data/ directory and are removed after each test.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
BOOT = r'''
import asyncio, json, os
from pathlib import Path
from app import config, llm
assert config.FAKE_LLM, "Recovery harness must never call a real model"
vision, text = llm.vision_json, llm.text_json
calls = Path(os.environ["MRT_TEST_CALLS"])
failed = False
def record(kind, tag):
    with calls.open("a") as f:
        f.write(json.dumps({"kind": kind, "tag": tag}) + "\n")
async def vision_spy(*args, **kwargs):
    record("vision", kwargs["tag"])
    await asyncio.sleep(float(os.environ.get("MRT_TEST_DELAY", "0")))
    return await vision(*args, **kwargs)
async def text_spy(*args, **kwargs):
    global failed
    record("text", kwargs["tag"])
    if os.environ.get("MRT_TEST_FAIL_SUMMARY") == "1" and not failed:
        failed = True
        raise RuntimeError("synthetic summary failure")
    return await text(*args, **kwargs)
llm.vision_json, llm.text_json = vision_spy, text_spy
from app.__main__ import main
main()
'''


@contextmanager
def scratch(prefix):
    parent = Path(os.environ.get("MRT_TEST_SCRATCH_ROOT", str(ROOT / "data" / "task4-tests"))).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as name:
        yield Path(name)


def eventually(fn, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for test condition (no medical values logged)")


def synthetic_pdf(pages=12, label="Synthetic recovery fixture"):
    with pymupdf.open() as pdf:
        for n in range(1, pages + 1):
            page = pdf.new_page(width=300, height=400)
            page.insert_text((30, 40), f"{label}, page {n}")
        return pdf.tobytes()


class Server:
    def __init__(self, directory, port=None, **overrides):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port or 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.client = httpx.Client(base_url=self.url, timeout=15)
        self.env = {**os.environ, "MRT_DATA_DIR": str(directory / "store"),
                    "MRT_FAKE_LLM": "1", "OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-6-luna",
                    "MRT_CONCURRENCY": "1", "MRT_MAX_PAGES": "0", "MRT_FAIL_AT_PAGE": "0",
                    "MRT_RENDER_LONG_SIDE": "400", "MRT_PORT": str(self.port),
                    "MRT_TEST_CALLS": str(directory / "calls.jsonl"), **overrides}
        self.process = None
        self.log = None

    def start(self):
        self.log = (self.directory / "server.log").open("ab")
        self.process = subprocess.Popen([sys.executable, "-c", BOOT], cwd=ROOT,
                                        env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        def ready():
            assert self.process.poll() is None, "Test server exited; see scratch server.log"
            try:
                return self.client.get("/api/meta").status_code == 200
            except httpx.TransportError:
                return False
        eventually(ready)
        return self

    def stop(self, kill=False):
        if self.process and self.process.poll() is None:
            self.process.kill() if kill else self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.stop()
            self.client.close()
            raise

    def __exit__(self, *_):
        self.stop()
        self.client.close()

    def upload(self, pages=12):
        response = self.client.post("/api/documents", files={
            "files": ("synthetic.pdf", synthetic_pdf(pages), "application/pdf")})
        response.raise_for_status()
        return response.json()[0]["id"]

    def wait_status(self, doc_id, status):
        return eventually(lambda: next((d for d in self.client.get("/api/documents").json()
                                        if d["id"] == doc_id and d["status"] == status), None))

    def rows(self, sql, args=()):
        path = Path(self.env["MRT_DATA_DIR"]) / "app.db"
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, args)]

    def calls(self):
        path = Path(self.env["MRT_TEST_CALLS"])
        return Counter((r["kind"], r["tag"]) for r in
                       (json.loads(line) for line in path.read_text().splitlines())) if path.exists() else Counter()


def test_same_process_retry_keeps_four_saved_pages():
    with scratch("retry-") as directory, Server(directory, MRT_FAIL_AT_PAGE="5") as server:
        doc_id = server.upload()
        failed = server.wait_status(doc_id, "failed")
        assert failed["stage_done"] == 4 and failed["stage_total"] == 12
        before = server.rows("SELECT page_no,status,attempts FROM pages ORDER BY page_no")
        assert [r["page_no"] for r in before if r["status"] == "done"] == [1, 2, 3, 4]
        assert before[4]["status"] == "failed"
        assert all(r["status"] == "pending" for r in before[5:])
        pid = server.process.pid
        server.client.post(f"/api/documents/{doc_id}/retry").raise_for_status()
        server.wait_status(doc_id, "done")
        assert server.process.pid == pid
        after = server.rows("SELECT page_no,status,attempts FROM pages ORDER BY page_no")
        assert len(after) == 12 and all(r["status"] == "done" for r in after)
        assert after[:4] == before[:4]
        assert after[4]["attempts"] == 2
        vision_calls = {tag: n for (kind, tag), n in server.calls().items() if kind == "vision"}
        assert len(vision_calls) == 12 and set(vision_calls.values()) == {1}


def test_retry_does_not_repeat_completed_summaries():
    with scratch("summary-retry-") as directory, Server(directory, MRT_TEST_FAIL_SUMMARY="1") as server:
        doc_id = server.upload()
        server.wait_status(doc_id, "failed")
        done = server.rows("SELECT start_page,end_page FROM records WHERE status='done'")
        assert len(done) == 2
        calls_before = server.calls()
        server.client.post(f"/api/documents/{doc_id}/retry").raise_for_status()
        server.wait_status(doc_id, "done")
        calls_after = server.calls()
        for row in done:
            key = ("text", f"{doc_id[:8]} r{row['start_page']}-{row['end_page']}")
            assert calls_before[key] == calls_after[key] == 1
        assert sum(n for (kind, _), n in calls_after.items() if kind == "vision") == 12
        assert sum(n for (kind, _), n in calls_after.items() if kind == "text") == 4


def test_process_restart_resumes_interrupted_work():
    with scratch("restart-") as directory, Server(directory, MRT_TEST_DELAY="0.2") as server:
        doc_id = server.upload()
        eventually(lambda: len(server.rows("SELECT page_no FROM pages WHERE status='done'")) >= 4)
        server.stop(kill=True)
        saved = server.rows("SELECT page_no,attempts FROM pages WHERE status='done'")
        assert 4 <= len(saved) < 12
        calls_before = server.calls()
        server.start()
        server.wait_status(doc_id, "done")
        calls_after = server.calls()
        for row in saved:
            key = ("vision", f"{doc_id[:8]} p{row['page_no']}")
            assert calls_before[key] == calls_after[key] == 1
        assert len(server.rows("SELECT page_no FROM pages WHERE status='done'")) == 12
