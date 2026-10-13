"""Stage 3: group pages into record spans. Pure rules, no model call (CLAUDE.md section 7)."""
from __future__ import annotations

import logging
import re

from .. import db
from ..models import PageSignals, RecordSpan, record_id_for

log = logging.getLogger(__name__)

FIRST_PAGE_REASON = "first page of the bundle"

# One pass over the fax header: date tokens (group `keep`) stay, page-number tokens (group
# `drop`) go. Dates come first in the alternation so "10/30/2025" is never read as "10/30".
_HEADER_TOKEN = re.compile(
    r"(?P<keep>"
    r"\b\d{1,4}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b"                      # 10/30/2025, 10/30/25
    r"|\b[A-Za-z]{3,9}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b"                # Dec/29/2025
    r")|(?P<drop>"
    r"\b(?:page|pg|p)\.?\s*\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?\b"      # PAGE 2/005, Page 2 of 6, Page 2
    r"|(?<![/\d])\b\d{1,4}\s*/\s*\d{1,4}\b(?!\s*/)"                  # 2/6
    r"|\b\d{1,4}\s+of\s+\d{1,4}\b"                                   # 2 of 6
    r")",
    re.IGNORECASE,
)
_PIPES = re.compile(r"\s*\|(?:\s*\|)*\s*")


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"                           # 2025-10-30
    r"|(?P<mon>[A-Za-z]{3,9})\s*/\s*(?P<d2>\d{1,2})\s*/\s*(?P<y2>\d{2,4})"  # Dec/29/2025
    r"|(?P<m3>\d{1,2})/(?P<d3>\d{1,2})/(?P<y3>\d{2,4})")                    # 10/30/2025
_TIME = re.compile(r"(?<!\d)(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ap>[AaPp][Mm])?")


def fax_id(header: str | None) -> str | None:
    """Canonical fax transmission id. Two pages with equal fax ids belong to the same fax.

    Preferred key: the fax timestamp as YYYYMMDD-HHMMSS. The model writes one printed
    timestamp in several formats across pages of one fax (10/30/2025 3:17:27 PM PDT,
    2025-10-30 3:17:27 PM, 2025-10-30T15:17:27-07:00), so the canonical key is what makes
    them equal. Without a date, falls back to the header text with page tokens removed."""
    if not header:
        return None
    m = _DATE.search(header)
    if m:
        if m.group("y"):
            y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
        elif m.group("mon"):
            mo = _MONTHS.get(m.group("mon")[:3].lower(), 0)
            d, y = int(m.group("d2")), int(m.group("y2"))
        else:
            mo, d, y = int(m.group("m3")), int(m.group("d3")), int(m.group("y3"))
        if mo and 1 <= d <= 31:
            if y < 100:
                y += 2000
            key = f"{y:04d}{mo:02d}{d:02d}"
            t = _TIME.search(header, m.end())
            if t:
                h, mi, sec = int(t.group("h")), int(t.group("mi")), int(t.group("s") or 0)
                ap = (t.group("ap") or "").lower()
                if ap == "pm" and h < 12:
                    h += 12
                if ap == "am" and h == 12:
                    h = 0
                key += f"-{h:02d}{mi:02d}{sec:02d}"
            return key
    text = _HEADER_TOKEN.sub(lambda m: m.group("keep") or "", header)
    text = _PIPES.sub(" | ", " ".join(text.split())).strip(" |")
    return text or None


# ---------- rules 1 to 8 for one page ----------

def _same_fax(cur: PageSignals, prev: PageSignals, cur_id: str | None, prev_id: str | None) -> bool:
    if cur.fax_page is None or prev.fax_page is None or cur.fax_page.k != prev.fax_page.k + 1:
        return False
    if cur_id is not None and cur_id == prev_id:
        return True
    # Fallback: the fax page counter runs on with the same total even though the header text
    # was transcribed differently (the model mangled the timestamp). Same transmission.
    return (cur_id is not None and prev_id is not None
            and cur.fax_page.n is not None and cur.fax_page.n == prev.fax_page.n)


def _label_continues(cur: PageSignals, prev: PageSignals) -> bool:
    return (cur.page_label is not None and prev.page_label is not None
            and cur.page_label.k == prev.page_label.k + 1
            and cur.doc_type == prev.doc_type)


def _dates_differ(cur: PageSignals, prev: PageSignals) -> bool:
    return bool(cur.date_of_service and prev.date_of_service
                and cur.date_of_service != prev.date_of_service)


def _fax_pos(sig: PageSignals) -> str:
    if sig.fax_page is None:
        return "?"
    n = sig.fax_page.n if sig.fax_page.n is not None else "N"
    return f"{sig.fax_page.k}/{n}"


def boundary_reason(cur: PageSignals, prev: PageSignals,
                    cur_id: str | None, prev_id: str | None) -> str | None:
    """Section 7 rules 1 to 8 for one page. Returns the reason when `cur` starts a new
    record, or None when it continues the record of `prev`."""
    same_fax = _same_fax(cur, prev, cur_id, prev_id)
    if same_fax or _label_continues(cur, prev):
        # Rule 3: inside one fax, or while page labels continue, only a confident new heading
        # with another type or date starts a record. A letterhead repeated on every page does not.
        if cur.starts_new_record and cur.boundary_confidence == "high":
            if cur.doc_type != prev.doc_type:
                detail = f"type {prev.doc_type} -> {cur.doc_type}"
            elif _dates_differ(cur, prev):
                detail = f"date {prev.date_of_service} -> {cur.date_of_service}"
            else:
                return None
            where = "inside the same fax" if same_fax else "while page labels continue"
            return f"new document {where} ({detail})"
        return None
    if cur.fax_page is not None and cur.fax_page.k == 1:                       # rule 4
        return f"fax restart {_fax_pos(cur)}"
    if cur_id is not None and prev_id is not None and cur_id != prev_id:       # rule 5
        return f"fax transmission changed (fax page {_fax_pos(cur)})"
    if cur.starts_new_record:                                                  # rule 6
        return f"new letterhead or form title ({cur.boundary_confidence} confidence)"
    if (cur.doc_type != prev.doc_type and cur.boundary_confidence != "low"     # rule 7
            and _dates_differ(cur, prev)):
        return (f"type and date changed ({prev.doc_type} {prev.date_of_service} -> "
                f"{cur.doc_type} {cur.date_of_service})")
    return None                                                                # rule 8


# ---------- post-processing ----------

def _is_cover(sig: PageSignals) -> bool:
    return sig.is_blank_or_cover or sig.doc_type == "cover_sheet"


def _attach_cover_pages(spans: list[list], signals: list[PageSignals]) -> None:
    """A cover sheet or blank page at the end of a span becomes the first page of the next
    span. Cover or blank pages at the very end of the bundle stay with the record before them.
    `spans` is a list of [start, end, reason], edited in place."""
    i = 0
    while i < len(spans) - 1:
        start, end, reason = spans[i]
        new_end = end
        while new_end >= start and _is_cover(signals[new_end - 1]):
            new_end -= 1
        if new_end == end:
            i += 1
            continue
        first_moved = new_end + 1
        moved = f"page {first_moved}" if first_moved == end else f"pages {first_moved}-{end}"
        nxt = spans[i + 1]
        nxt[0] = first_moved
        base = reason if new_end < start and i == 0 else nxt[2]
        nxt[2] = f"{base}; cover or blank {moved} attached in front"
        if new_end < start:
            spans.pop(i)        # the span held only cover or blank pages
        else:
            spans[i][1] = new_end
            i += 1
    if len(spans) > 1:
        start, end, _ = spans[-1]
        if all(_is_cover(signals[p - 1]) for p in range(start, end + 1)):
            spans.pop()
            spans[-1][1] = end


def _is_partial(signals: list[PageSignals], start: int, end: int) -> bool:
    """A record is partial when its first content page carries page label k > 1, or fax page
    k > 1 without a new heading. A cover sheet attached in front does not hide that."""
    first = next((signals[p - 1] for p in range(start, end + 1) if not _is_cover(signals[p - 1])),
                 signals[start - 1])
    if first.page_label is not None and first.page_label.k > 1:
        return True
    return first.fax_page is not None and first.fax_page.k > 1 and not first.starts_new_record


# ---------- public entry points ----------

def group_pages(signals: list[PageSignals]) -> list[RecordSpan]:
    """Pure: page signals in bundle order -> record spans (1-based, inclusive page positions)."""
    if not signals:
        return []
    ids = [fax_id(s.fax_header) for s in signals]
    spans: list[list] = [[1, 1, FIRST_PAGE_REASON]]
    for i in range(1, len(signals)):
        reason = boundary_reason(signals[i], signals[i - 1], ids[i], ids[i - 1])
        if reason is None:
            spans[-1][1] = i + 1
        else:
            spans.append([i + 1, i + 1, reason])
    _attach_cover_pages(spans, signals)
    return [RecordSpan(start_page=s, end_page=e, boundary_reason=r,
                       partial_start=_is_partial(signals, s, e)) for s, e, r in spans]


def fallback_signals(page_no: int) -> PageSignals:
    """Neutral signals for a page without an extraction result: never starts a record."""
    return PageSignals(transcription="", starts_new_record=False, boundary_confidence="low",
                       doc_type="other", gist=f"page {page_no}: no extraction result",
                       is_blank_or_cover=False)


def _signals_for(row: dict) -> PageSignals:
    if row.get("status") == "done" and row.get("signals_json"):
        try:
            return PageSignals.model_validate_json(row["signals_json"])
        except ValueError as exc:
            log.warning("group doc=%s page=%s: unreadable signals_json, using neutral fallback: %s",
                        row["doc_id"][:12], row["page_no"], exc)
    return fallback_signals(row["page_no"])


def group_document(doc_id: str) -> list[dict]:
    """Load the pages of a document, group them, and make the records table match.
    Record ids are deterministic (doc id + span), so unchanged spans keep their id."""
    pages = db.get_pages(doc_id)
    spans = group_pages([_signals_for(p) for p in pages])
    rows = []
    for span in spans:
        start = pages[span.start_page - 1]["page_no"]
        end = pages[span.end_page - 1]["page_no"]
        rows.append({
            "id": record_id_for(doc_id, start, end),
            "start_page": start,
            "end_page": end,
            "boundary_reason": span.boundary_reason,
            "partial_start": int(span.partial_start),
        })
    db.sync_records(doc_id, rows)
    log.info("group doc=%s pages=%d records=%d", doc_id[:12], len(pages), len(rows))
    return rows
