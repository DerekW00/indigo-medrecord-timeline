#!/usr/bin/env python
"""Score a processed document against its gold file and write eval/results/<doc-id>.json.

    python eval/run_eval.py --doc-id <id or prefix> [--filename record_1.pdf] [--gold path] [--judge]

Boundary, date, type and duplicate metrics use the repository's adopted gold file
(eval/gold/<stem>.json). Pass --gold to evaluate a separate draft.
The date rule check runs on every summary without gold.
--judge sends each summary with its transcription to the model (app/prompts/summary_judge.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db  # noqa: E402
from eval import metrics as M  # noqa: E402

# Human-adopted labels live in the repository's eval/gold directory.
GOLD_DIR = ROOT / "eval" / "gold"
RESULTS_DIR = ROOT / "eval" / "results"
PROMPT_PATH = ROOT / "app" / "prompts" / "summary_judge.md"
JUDGE_SYSTEM = ("You check a summary against the source transcription of a medical record. "
                "Output JSON only, matching the provided schema.")
JUDGE_USER = """You check a summary against the source transcription of a medical record. List every factual claim in the summary as a short string. Label each claim "supported" if the transcription states it, "unsupported" if the transcription contradicts it or does not contain it, or "unclear" if the transcription is illegible or ambiguous at that point. Output JSON: {"claims": [{"claim": str, "label": str, "evidence": str}]}.

Summary:
{summary}

Transcription:
{transcription}"""


# ---------- loading ----------

def find_document(doc_id: str | None, filename: str | None) -> dict:
    docs = db.list_documents()
    hits: list[dict] = []
    if doc_id:
        hits = [d for d in docs if d["id"] == doc_id] or [d for d in docs if d["id"].startswith(doc_id)]
    elif filename:
        hits = [d for d in docs if d["filename"] == filename]
    if len(hits) == 1:
        return hits[0]
    listing = "\n".join(f"  {d['id'][:12]}  {d['status']:<12} {d['filename']}" for d in docs) or "  (none)"
    sys.exit(f"{'no' if not hits else 'ambiguous'} document for that --doc-id/--filename. Documents:\n{listing}")


def gold_path_for(filename: str, override: str | None) -> Path:
    if override:
        return Path(override)
    by_stem = GOLD_DIR / f"{Path(filename).stem}.json"
    by_name = GOLD_DIR / f"{filename}.json"
    return by_name if by_name.exists() and not by_stem.exists() else by_stem


def effective(rec: dict, override: dict | None, key: str):
    if override and override.get(key) is not None:
        return override[key]
    return rec.get(key)


def load_predictions(doc_id: str) -> tuple[list[dict], list[M.PredRecord], dict[str, str]]:
    records = db.get_records(doc_id)
    overrides = db.get_overrides()
    preds = [M.PredRecord(id=r["id"], start_page=r["start_page"], end_page=r["end_page"],
                          date_of_service=effective(r, overrides.get(r["id"]), "date_of_service"),
                          record_type=effective(r, overrides.get(r["id"]), "record_type"),
                          boundary_reason=r.get("boundary_reason") or "") for r in records]
    groups = {rid: row["group_id"] for rid, row in db.get_duplicate_groups().items()}
    return records, preds, groups


def page_texts(doc_id: str) -> dict[int, str]:
    """Transcription per page, with the fax header line (text printed on the page) on top."""
    out: dict[int, str] = {}
    for p in db.get_pages(doc_id):
        fax = None
        try:
            fax = json.loads(p.get("signals_json") or "{}").get("fax_header")
        except (TypeError, ValueError):
            pass
        out[p["page_no"]] = (f"{fax}\n" if fax else "") + (p.get("text") or "")
    return out


def record_transcription(rec: dict, texts: dict[int, str], cap: int = 40_000) -> str:
    parts = [f"=== Page {n} ===\n{texts.get(n, '')}" for n in range(rec["start_page"], rec["end_page"] + 1)]
    text = "\n\n".join(parts)
    if len(text) > cap:  # same cut as the summarizer, so the judge sees what the model saw
        text = text[:30_000] + "\n[... middle omitted ...]\n" + text[-8_000:]
    return text


# ---------- date rule check (no model) ----------

def rule_check(records: list[dict], texts: dict[int, str]) -> dict:
    items: list[dict] = []
    done = [r for r in records if r["status"] == "done"]
    for r in done:
        fields = {"date_of_service": r.get("date_of_service"), "summary": r.get("summary"),
                  "key_findings": json.loads(r.get("key_findings_json") or "[]")}
        for v in M.date_rule_check(fields, record_transcription(r, texts, cap=10**9)):
            items.append({"record_id": r["id"], "span": [r["start_page"], r["end_page"]], **v})
    return {"records_checked": len(done), "violations": len(items), "items": items}


# ---------- model judge ----------

def split_prompt(text: str) -> tuple[str, str]:
    if "# User" in text:
        system, user = text.split("# User", 1)
        return (system.replace("# System", "").strip() or JUDGE_SYSTEM), user.strip()
    return JUDGE_SYSTEM, text.strip()


def load_judge_prompt() -> tuple[str, str]:
    system, user = JUDGE_SYSTEM, JUDGE_USER
    try:
        from app.pipeline.prompts_util import load_prompt  # written by the pipeline agent
        loaded = load_prompt("summary_judge")
        if isinstance(loaded, dict):
            system, user = loaded.get("system") or JUDGE_SYSTEM, loaded.get("user") or ""
        elif isinstance(loaded, (tuple, list)) and len(loaded) == 2:
            system, user = loaded
        elif isinstance(loaded, str):
            system, user = split_prompt(loaded)
    except Exception:
        if PROMPT_PATH.exists():
            system, user = split_prompt(PROMPT_PATH.read_text())
    user = user.replace("{{summary}}", "{summary}").replace("{{transcription}}", "{transcription}")
    if "{summary}" not in user or "{transcription}" not in user:
        system, user = JUDGE_SYSTEM, JUDGE_USER
    return system, user


def fill_prompt(template: str, **values: str) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out.replace("{{", "{").replace("}}", "}")


async def judge_all(doc_id: str, records: list[dict], texts: dict[int, str]) -> dict:
    from app.llm import text_json
    from app.models import JudgeResult
    system, template = load_judge_prompt()
    sem = asyncio.Semaphore(config.CONCURRENCY)

    async def one(r: dict) -> dict:
        summary = r.get("summary") or ""
        findings = json.loads(r.get("key_findings_json") or "[]")
        if findings:
            summary += "\nKey findings:\n" + "\n".join(f"- {f}" for f in findings)
        user = fill_prompt(template, summary=summary, transcription=record_transcription(r, texts))
        row = {"record_id": r["id"], "span": [r["start_page"], r["end_page"]], "title": r.get("title")}
        async with sem:
            try:
                res = await text_json(system, user, JudgeResult,
                                      f"judge {doc_id[:8]} p{r['start_page']}-{r['end_page']}")
                claims = [c.model_dump() for c in res.claims]
            except Exception as e:  # LLMError after its own retries, or a parse problem
                row["error"] = f"{type(e).__name__}: {e}"[:500]
                claims = []
        row["claims"] = claims
        for label in ("supported", "unsupported", "unclear"):
            row[label] = sum(1 for c in claims if c["label"] == label)
        return row

    rows = await asyncio.gather(*(one(r) for r in records if r["status"] == "done"))
    total = sum(len(r["claims"]) for r in rows)
    supported = sum(r["supported"] for r in rows)
    return {"records_judged": len(rows), "errors": sum(1 for r in rows if r.get("error")),
            "claims": total, "supported": supported,
            "unsupported": sum(r["unsupported"] for r in rows), "unclear": sum(r["unclear"] for r in rows),
            "supported_rate": M.ratio(supported, total), "records": rows}


# ---------- report ----------

def fmt(x: float | None) -> str:
    return "  n/a" if x is None else f"{x:.3f}"


def print_report(out: dict) -> None:
    b = out.get("boundaries")
    print(f"\nEval {out['filename']}  doc {out['doc_id'][:12]}  status {out['document_status']}"
          f"  gold {out.get('gold_path') or '(none)'}")
    if b:
        s, sp, pg, f, d = b["starts"], b["spans"], b["pages"], b["fields"], b["duplicates"]
        lo, hi = b["labeled_pages"]
        print(f"pages {lo}-{hi}: {b['n_gold']} gold records, {b['n_pred']} predicted")
        print(f"{'start pages':<13} P {fmt(s['precision'])}  R {fmt(s['recall'])}  F1 {fmt(s['f1'])}"
              f"   tp {s['tp']} fp {s['fp']} fn {s['fn']}, {s['dropped_internal']} internal splits ignored")
        print(f"{'exact spans':<13} {sp['matched']}/{sp['total']} = {fmt(sp['rate'])}"
              f"   ({sp['exact']} exact, {sp['tiled']} tiled)")
        extra = f"   ({pg['uncovered_pages']} pages have no gold)" if pg["uncovered_pages"] else ""
        print(f"{'page assign':<13} {pg['hits']}/{pg['total']} = {fmt(pg['accuracy'])}{extra}")
        print(f"{'dates':<13} {f['date_hits']}/{f['n_matched']} = {fmt(f['date_accuracy'])} on matched spans")
        print(f"{'types':<13} {f['type_hits']}/{f['n_matched']} = {fmt(f['type_accuracy'])} on matched spans")
        print(f"{'duplicates':<13} {d['hits']}/{d['gold_pairs']} gold pairs found, {d['false_pairs']} false pairs")
    r = out["date_rule"]
    print(f"{'date rule':<13} {r['violations']} date(s) not on the pages, {r['records_checked']} summaries checked")
    j = out.get("judge")
    if j:
        print(f"{'judge':<13} {j['supported']}/{j['claims']} claims supported = {fmt(j['supported_rate'])}"
              f"   ({j['unsupported']} unsupported, {j['unclear']} unclear, {j['errors']} call errors)")

    if b:
        for m in s["missed_starts"]:
            print(f"  missed start  p{m['page']}  {m['gold_id']}  {m['note']}")
        for m in s["false_starts"]:
            print(f"  false start   p{m['page']}  {m['boundary_reason']}")
        for u in sp["unmatched"]:
            print(f"  span miss     {u['gold_id']} p{u['span'][0]}-{u['span'][1]}  {u['note']}")
        for row in f["rows"]:
            if not row["date_hit"]:
                print(f"  date miss     {row['gold_id']}  gold {row['gold_date']}  got {row['pred_date']}")
            if not row["type_hit"]:
                print(f"  type miss     {row['gold_id']}  accepted {row['accepted_types']}  got {row['pred_type']}")
        for p in d["pairs"]:
            if p["status"] != "hit":
                print(f"  dup {p['status']:<9} {p['pair'][0]} ~ {p['pair'][1]}  ({p['kind']})")
        for p in d["false_pair_list"]:
            print(f"  dup false     {p[0]} ~ {p[1]}")
        for w in d["warnings"]:
            print(f"  gold warning  {w}")
    for v in r["items"]:
        print(f"  date rule     {v['record_id'][:8]} p{v['span'][0]}-{v['span'][1]} {v['field']}: "
              f"'{v['text']}' ({v['date']}) is not on the pages")
    if j:
        for row in j["records"]:
            if row.get("error"):
                print(f"  judge error   {row['record_id'][:8]} p{row['span'][0]}-{row['span'][1]}  {row['error']}")
            for c in row["claims"]:
                if c["label"] == "unsupported":
                    print(f"  unsupported   {row['record_id'][:8]} p{row['span'][0]}-{row['span'][1]}: "
                          f"{c['claim']}  [{c.get('evidence', '')}]")
    print(f"\nwrote {out['results_path']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc-id", help="document id (sha256 hex) or a unique prefix of it")
    ap.add_argument("--filename", help="document filename; finds the document when --doc-id is absent")
    ap.add_argument("--gold", help="gold file path (default repository-root eval/gold/<filename stem>.json)")
    ap.add_argument("--judge", action="store_true", help="also judge every summary with the model")
    ap.add_argument("--out", help="results path (default eval/results/<doc-id>.json)")
    args = ap.parse_args(argv)
    if not args.doc_id and not args.filename:
        ap.error("give --doc-id or --filename")
    if args.judge and not config.FAKE_LLM and not config.OPENAI_API_KEY:
        ap.error("--judge needs OPENAI_API_KEY in the environment or .env (or MRT_FAKE_LLM=1 for a dry run)")

    db.init_db()
    doc = find_document(args.doc_id, args.filename)
    doc_id = doc["id"]
    records, preds, groups = load_predictions(doc_id)
    texts = page_texts(doc_id)
    if not records:
        print(f"warning: document {doc_id[:12]} has no records yet (status {doc['status']}); run the pipeline first")

    gold_path = gold_path_for(args.filename or doc["filename"], args.gold)
    boundaries = None
    if gold_path.exists():
        boundaries = M.evaluate(json.loads(gold_path.read_text()), preds, groups)
    else:
        print(f"warning: no gold file at {gold_path}; boundary metrics skipped. "
              f"Draft one with: python scripts/export_gold.py --doc-id {doc_id[:12]}")

    out = {
        "doc_id": doc_id, "filename": doc["filename"], "document_status": doc["status"],
        "gold_path": str(gold_path) if boundaries else None,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": config.OPENAI_MODEL, "fake_llm": config.FAKE_LLM,
        "boundaries": boundaries,
        "date_rule": rule_check(records, texts),
        "judge": asyncio.run(judge_all(doc_id, records, texts)) if args.judge else None,
    }
    results_path = Path(args.out) if args.out else RESULTS_DIR / f"{doc_id}.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    out["results_path"] = str(results_path)
    results_path.write_text(json.dumps(out, indent=2) + "\n")
    print_report(out)


if __name__ == "__main__":
    main()
