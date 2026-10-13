#!/usr/bin/env python
"""Dump a document's current predicted spans as a gold file for a human to correct.

    python scripts/export_gold.py --doc-id <id or prefix> [--out eval/gold/record_1.json] [--force]

Effective values are used (reviewer edits from the UI applied on top of the model values), so
corrections made in the timeline flow into the gold file. An existing gold file is never
overwritten without --force, because it may hold human labels.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc-id", required=True, help="document id (sha256 hex) or a unique prefix of it")
    ap.add_argument("--out", help="output path (default eval/gold/<filename stem>.json)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing gold file")
    args = ap.parse_args(argv)

    db.init_db()
    docs = db.list_documents()
    hits = [d for d in docs if d["id"] == args.doc_id] or [d for d in docs if d["id"].startswith(args.doc_id)]
    if len(hits) != 1:
        listing = "\n".join(f"  {d['id'][:12]}  {d['status']:<12} {d['filename']}" for d in docs) or "  (none)"
        sys.exit(f"{'no' if not hits else 'ambiguous'} document for --doc-id {args.doc_id!r}. Documents:\n{listing}")
    doc = hits[0]
    records = db.get_records(doc["id"])
    if not records:
        sys.exit(f"document {doc['id'][:12]} has no records yet (status {doc['status']}); run the pipeline first")
    overrides = db.get_overrides()

    width = max(2, len(str(len(records))))
    rows = []
    for i, r in enumerate(records, 1):
        ov = overrides.get(r["id"]) or {}
        rtype = ov.get("record_type") or r.get("record_type")
        date = ov["date_of_service"] if ov.get("date_of_service") is not None else r.get("date_of_service")
        notes = [r.get("boundary_reason") or ""]
        if r.get("partial_start"):
            notes.append("partial start")
        if r.get("status") != "done":
            notes.append(f"summary {r.get('status')}")
        rows.append({
            "id": f"r{i:0{width}d}", "start_page": r["start_page"], "end_page": r["end_page"],
            "date_of_service": date, "record_type": rtype,
            "accepted_types": [rtype] if rtype else [],
            "note": "; ".join(n for n in notes if n),
        })

    last = max(r["end_page"] for r in records)
    hi = doc.get("page_count") or last
    if last < hi:
        print(f"note: records cover pages 1-{last} of {hi}; labeled_pages set to [1, {last}]")
        hi = last
    gold = {
        "filename": doc["filename"],
        "labeled_pages": [1, hi],
        "generated": f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} by scripts/export_gold.py "
                     f"from predicted spans of {doc['id'][:12]}; a human must correct the wrong ones",
        "records": rows,
    }
    out = Path(args.out) if args.out else ROOT / "eval" / "gold" / f"{Path(doc['filename']).stem}.json"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists and may hold human labels; pass --force to overwrite it")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gold, indent=2) + "\n")
    print(f"wrote {out}: {len(rows)} records, pages 1-{hi}. Fix the wrong spans, then run "
          f"python eval/run_eval.py --doc-id {doc['id'][:12]}")


if __name__ == "__main__":
    main()
