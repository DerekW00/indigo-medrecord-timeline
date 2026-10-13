"""Measure dedupe only in a fresh disposable store; source and gold remain read-only."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3


def copy_store(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be separate stores")
    if destination.exists():
        raise ValueError("destination must be fresh")
    if not (source / "app.db").is_file():
        raise ValueError("source database missing")
    destination.mkdir(parents=True)
    with sqlite3.connect((source / "app.db").as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination / "app.db") as dst:
            src.backup(dst)
            for doc_id, page_no, image_path in dst.execute("SELECT doc_id,page_no,image_path FROM pages").fetchall():
                origin = Path(image_path)
                if not origin.is_absolute():
                    candidates = [source / origin, source.parent.parent / origin,
                                  source / "pages" / doc_id / f"{page_no:04d}.jpg"]
                    origin = next((p for p in candidates if p.is_file()), origin)
                target = destination / "pages" / doc_id / f"{page_no:04d}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
                dst.execute("UPDATE pages SET image_path=? WHERE doc_id=? AND page_no=?",
                            (str(target), doc_id, page_no))
            dst.commit()
    if (source / "uploads").exists():
        shutil.copytree(source / "uploads", destination / "uploads")


def assert_negatives(records: list[dict], groups: dict) -> None:
    by_span = {(r["start_page"], r["end_page"]): r["id"] for r in records}
    for spans in [((28, 31), (76, 78)), ((151, 152), (157, 165))]:
        a, b = (by_span[s] for s in spans)
        assert not (a in groups and b in groups and groups[a]["group_id"] == groups[b]["group_id"]), spans


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--mode", choices=["exact", "confirmed"], required=True)
    parser.add_argument("--expect-hits", type=int, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    destination = args.data_dir.resolve()
    copy_store(args.source_data, destination)
    os.environ["MRT_DATA_DIR"] = str(destination)
    from app import db
    from app.pipeline import dedupe
    from eval import run_eval
    db.init_db()
    doc = run_eval.find_document(args.doc_id, None)
    before = {t: db.connect().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("documents", "pages", "records", "record_overrides")}
    evidence = []
    asyncio.run(dedupe.dedupe_all(exact_only=args.mode == "exact", max_confirmations=8, evidence=evidence))
    from app import config
    from app.pipeline.prompts_util import load_prompt
    import hashlib
    prompt = "\n".join(load_prompt("duplicate_confirm"))
    (destination / "confirmations.json").write_text(json.dumps({
        "model": config.OPENAI_MODEL, "fake": config.FAKE_LLM,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "candidates": evidence, "calls": sum("decision" in e or "error" in e for e in evidence),
        "accepted": sum(e["accepted"] for e in evidence),
    }, indent=2) + "\n")
    run_eval.main(["--doc-id", doc["id"], "--gold", str(args.gold),
                   "--out", str(destination / "eval.json")])
    report = json.loads((destination / "eval.json").read_text())
    metrics = report["boundaries"]["duplicates"]
    assert_negatives(db.get_records(doc["id"]), db.get_duplicate_groups())
    after = {t: db.connect().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert before == after, "source rows must be preserved"
    checks = {"mode": args.mode, "hits": metrics["hits"], "expected_hits": args.expect_hits,
              "false_pairs": metrics["false_pairs"], "negative_pairs_separate": True,
              "source_rows_preserved": before, "data_dir": str(destination)}
    (destination / "checks.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks))
    assert metrics["hits"] == args.expect_hits and metrics["false_pairs"] == 0, "duplicate acceptance gate unmet"


if __name__ == "__main__":
    main()
