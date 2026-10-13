"""Stage 4: one text call per record -> RecordSummary saved on the records row."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field

from .. import config, db, llm
from ..models import PageSignals, RecordSummary
from .extract import sha256_hex, simple_normalize, strip_fax_header
from .prompts_util import load_prompt

log = logging.getLogger("mrt.summarize")
MAX_CHARS, HEAD_CHARS, TAIL_CHARS = 40_000, 30_000, 8_000
_BIRTH_OR_INJURY = ("dob", "birth", "injury", "doi")
PARTIAL_NOTE = " This record starts mid-document (the bundle begins at fax page {k} of {n}); say so in flags."


@dataclass
class RecordInput:
    transcription: str
    content_hash: str
    doc_types: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    bad_dates: set[str] = field(default_factory=set)  # dob / date of injury seen on the pages
    date_counts: Counter = field(default_factory=Counter)  # page service date -> number of pages
    partial_note: str = ""


def cap_text(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:HEAD_CHARS] + "\n[... middle omitted ...]\n" + text[-TAIL_CHARS:]


def _unique(items) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


def _signals(page: dict | None) -> PageSignals | None:
    if not page or not page.get("signals_json"):
        return None
    try:
        return PageSignals.model_validate_json(page["signals_json"])
    except Exception:  # a bad row must not kill the record
        return None


def _partial_note(first: PageSignals | None) -> str:
    ref = first and (first.fax_page or first.page_label)
    if ref is None:
        return " This record starts mid-document (its first pages are missing); say so in flags."
    return PARTIAL_NOTE.format(k=ref.k, n=ref.n if ref.n is not None else "?")


def build_record_input(record: dict, pages: dict[int, dict]) -> RecordInput:
    """Concatenate the page transcriptions with markers, collect hints, hash the content."""
    parts, plain, signals_list = [], [], []
    for page_no in range(int(record["start_page"]), int(record["end_page"]) + 1):
        page = pages.get(page_no)
        signals = _signals(page)
        text = page["text"] if page and page.get("status") == "done" and page.get("text") else "[page not transcribed]"
        parts.append(f"=== Page {page_no} ===\n{text}")
        plain.append(strip_fax_header(text, signals.fax_header if signals else None))
        if signals:
            signals_list.append(signals)
    out = RecordInput(
        transcription=cap_text("\n\n".join(parts)),
        content_hash=sha256_hex(simple_normalize(" ".join(plain))),
        doc_types=_unique(s.doc_type for s in signals_list),
        dates=_unique(s.date_of_service for s in signals_list),
        providers=_unique(s.provider for s in signals_list),
        date_counts=Counter(s.date_of_service for s in signals_list if s.date_of_service),
    )
    for s in signals_list:
        for other in s.other_dates:
            if any(word in other.label.lower() for word in _BIRTH_OR_INJURY):
                out.bad_dates.add(other.date)
    if record.get("partial_start"):
        out.partial_note = _partial_note(signals_list[0] if signals_list else None)
    return out


def apply_date_rule(summary: RecordSummary, built: RecordInput, tag: str) -> RecordSummary:
    """The service date wins: if the model picked a dob or date of injury that no page reported as
    its service date, use the most common page service date instead. A visit that happened on the
    date of injury (an ER visit) keeps its date because the pages report it as the service date."""
    chosen = summary.date_of_service
    if not chosen or chosen not in built.bad_dates or chosen in built.dates:
        return summary
    candidates = [d for d in built.dates if d not in built.bad_dates]
    if not candidates:
        return summary
    best = max(candidates, key=lambda d: built.date_counts[d])  # ties go to the first page order
    log.info("%s date %s is a dob/date of injury; using page service date %s", tag, chosen, best)
    return summary.model_copy(update={"date_of_service": best, "date_confidence": "approximate"})


async def summarize_document(doc_id: str) -> int:
    """Summarize every record whose status is not done. Returns the failed record count."""
    doc = db.get_document(doc_id)
    if doc is None:
        raise ValueError(f"unknown document {doc_id}")
    records = db.get_records(doc_id)
    pages = {int(p["page_no"]): p for p in db.get_pages(doc_id)}
    todo = [r for r in records if r["status"] != "done"]
    progress = {"done": len(records) - len(todo)}
    db.set_document(doc_id, stage_done=progress["done"], stage_total=len(records))

    system, user_template = load_prompt("record_summary")
    sem = asyncio.Semaphore(max(1, config.CONCURRENCY))

    async def run_record(record: dict) -> None:
        tag = f"{doc_id[:8]} r{record['start_page']}-{record['end_page']}"
        async with sem:
            try:
                built = await asyncio.to_thread(build_record_input, record, pages)
                user = user_template.format(
                    filename=doc["filename"], start_page=record["start_page"], end_page=record["end_page"],
                    doc_types=", ".join(built.doc_types) or "none", dates=", ".join(built.dates) or "none",
                    providers=", ".join(built.providers) or "none", partial_note=built.partial_note,
                    transcription=built.transcription,
                )
                summary = await llm.text_json(system, user, RecordSummary, tag=tag)
                assert isinstance(summary, RecordSummary)
                summary = apply_date_rule(summary, built, tag)
                db.save_record_summary(record["id"], summary.model_dump(), built.content_hash)
                progress["done"] += 1
                db.set_document(doc_id, stage_done=progress["done"])
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                log.warning("%s summarize failed: %s", tag, message)
                db.save_record_failure(record["id"], message)

    await asyncio.gather(*(run_record(r) for r in todo))
    failed = sum(1 for r in db.get_records(doc_id) if r["status"] == "failed")
    log.info("%s summarize: %d done, %d failed, %d records", doc_id[:8], progress["done"], failed, len(records))
    return failed
