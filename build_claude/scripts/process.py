"""Run the pipeline on one PDF without the server and print the record spans.

    python -m scripts.process samples/record_1.pdf [--max-pages N]

Uses the same store (MRT_DATA_DIR) as the web app, so the result shows up in the UI too.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cell(value, width: int) -> str:
    s = "" if value is None else str(value)
    return s if len(s) <= width else s[: width - 1] + "…"


def print_table(records: list[dict]) -> None:
    cols = (("pages", 9), ("partial", 7), ("boundary_reason", 44), ("date", 10), ("type", 28), ("title", 60))
    print("  ".join(name.ljust(w) for name, w in cols))
    print("  ".join("-" * w for _, w in cols))
    for r in records:
        row = (
            f"{r['start_page']}-{r['end_page']}",
            "yes" if r.get("partial_start") else "",
            r.get("boundary_reason"),
            r.get("date_of_service") or "undated",
            r.get("record_type") or r.get("status"),
            r.get("title") or (r.get("error") or ""),
        )
        print("  ".join(_cell(v, w).ljust(w) for v, (_, w) in zip(row, cols)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="path to a PDF bundle")
    ap.add_argument("--max-pages", type=int, default=None, help="process only the first N pages (dev)")
    args = ap.parse_args(argv)
    if args.max_pages is not None:
        os.environ["MRT_MAX_PAGES"] = str(args.max_pages)  # before app.config is imported

    sys.path.insert(0, str(ROOT))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from app import db, worker

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"error: {pdf} is not a file", file=sys.stderr)
        return 2
    db.init_db()
    doc_id = worker.register_pdf(pdf)
    db.reset_failed_pages(doc_id)  # a re-run resumes: done pages and records are skipped
    doc = db.get_document(doc_id)
    n = worker.effective_page_count(int(doc["page_count"]))
    cap = f" (processing the first {n})" if n < int(doc["page_count"]) else ""
    print(f"document {doc_id}  {doc['filename']}  {doc['page_count']} pages{cap}  status={doc['status']}")

    asyncio.run(worker.process_document(doc_id))

    doc = db.get_document(doc_id)
    records = db.get_records(doc_id)
    print()
    print_table(records)
    print()
    print(f"status={doc['status']}  records={len(records)}" + (f"  error={doc['error']}" if doc.get("error") else ""))
    return 0 if doc["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
