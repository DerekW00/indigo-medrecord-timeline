"""Stage 3: group pages into record spans. Pure rules, no model call.

Fax transmissions use canonical date-time stamps rather than exact header strings.
Continuing fax page counters tolerate header drift; a printed page label that
restarts inside one fax can begin a new record.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .. import db
from ..models import PageSignals, RecordSpan, record_id_for

log = logging.getLogger(__name__)

FIRST_PAGE_REASON = "first page of the bundle"

# ---------- fax header parsing ----------

# One pass over the fax header: date tokens (group `keep`) stay, page-number tokens (group
# `drop`) go. Dates come first in the alternation so "10/30/2025" is never read as "10/30".
_HEADER_TOKEN = re.compile(
    r"(?P<keep>"
    r"\b\d{1,4}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b"                      # 10/30/2025, 10/30/25
    r"|\b[A-Za-z]{3,9}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b"                # Dec/29/2025
    r")|(?P<drop>"
    r"\b(?:page|pg|p)\.?\s*\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?\b"      # PAGE 2/005, Page 2 of 6, Page 2
    r"|\b(?:page|pg)\b\.?(?!\s*\d)"                                  # bare PAGE, number cut off
    r"|(?<![/\d])\b\d{1,4}\s*/\s*\d{1,4}\b(?!\s*/)"                  # 2/6
    r"|\b\d{1,4}\s+of\s+\d{1,4}\b"                                   # 2 of 6
    r")",
    re.IGNORECASE,
)
_PIPES = re.compile(r"\s*\|(?:\s*\|)*\s*")
_RECEIVED_STAMP = re.compile(r"^\s*received\s+by\b", re.IGNORECASE)

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MONTH_NAME = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
# The model copies one fax stamp in several spellings: "10/30/2025 3:17:27 PM PDT",
# "2025-10-30 3:17:27 PM", "2025-10-30T15:17:27-07:00", "01 Apr 2025 11:22:56 AM", "Dec/29/2025".
_DATE = re.compile(
    r"(?<![0-9A-Za-z])(?:"
    r"(?P<y1>\d{4})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})"
    r"|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{4}|\d{2})"
    r"|(?P<mon3>" + _MONTH_NAME + r")[/\s]+(?P<d3>\d{1,2}),?[/\s]+(?P<y3>\d{4})"
    r"|(?P<d4>\d{1,2})\s+(?P<mon4>" + _MONTH_NAME + r"),?\s+(?P<y4>\d{4})"
    r")(?!\d)",
    re.IGNORECASE,
)
_TIME = re.compile(r"(?<![\d:+\-])(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.\d+)?)?"
                   r"(?:\s*(?P<ap>[AaPp])\.?[Mm]\b)?")


@dataclass(frozen=True)
class _Stamp:
    """The date-time printed by the fax server, parsed from the header."""
    date: str                      # YYYY-MM-DD
    hour: int | None               # 24h when `exact`; as printed otherwise
    minute: str | None
    second: str | None
    exact: bool                    # AM/PM seen, or an unambiguous 24h value; "3:46" alone is not

    def canonical(self) -> str:
        digits = self.date.replace("-", "")
        if self.hour is None:
            return digits
        return f"{digits}-{self.hour:02d}{self.minute}{self.second or '00'}"

    def matches(self, other: _Stamp) -> bool:
        if self.date != other.date:
            return False
        if self.hour is None or other.hour is None:
            return True                                  # a date-only stamp matches that day's pages
        if self.minute != other.minute:
            return False
        if self.exact and other.exact:
            return self.hour == other.hour
        return self.hour % 12 == other.hour % 12         # "3:46" (cut off) vs "3:46:47 PM"


def _header_segments(header: str) -> list[str]:
    """The ' | '-joined parts of the header, without the TPA receipt stamp ('Received by TPA
    ICS Fax 10/30/25'): that stamp is read on some pages and not others of one transmission."""
    return [seg for seg in _PIPES.split(header) if seg.strip() and not _RECEIVED_STAMP.match(seg)]


def _month(name: str) -> int | None:
    return _MONTHS.get(name[:3].lower())


def _stamp(header: str | None) -> _Stamp | None:
    """The first date (plus the time after it) in the header, or None."""
    if not header:
        return None
    for seg in _header_segments(header):
        m = _DATE.search(seg)
        if not m:
            continue
        g = m.groupdict()
        if g["y1"]:
            date = f"{int(g['y1']):04d}-{int(g['m1']):02d}-{int(g['d1']):02d}"
        elif g["y2"]:
            year = int(g["y2"]) + (2000 if len(g["y2"]) == 2 else 0)
            date = f"{year:04d}-{int(g['m2']):02d}-{int(g['d2']):02d}"
        else:
            name, day, year = (g["mon3"], g["d3"], g["y3"]) if g["mon3"] else (g["mon4"], g["d4"], g["y4"])
            date = f"{int(year):04d}-{_MONTHS[name[:3].lower()]:02d}-{int(day):02d}"
        t = _TIME.search(seg, m.end()) or _TIME.search(seg)
        if not t:
            return _Stamp(date, None, None, None, False)
        hour, ap = int(t.group("h")), (t.group("ap") or "").lower()
        if ap == "p" and hour < 12:
            hour += 12
        elif ap == "a" and hour == 12:
            hour = 0
        return _Stamp(date, hour, t.group("mi"), t.group("s"), bool(ap) or hour == 0 or hour > 12)
    return None


def fax_id(header: str | None) -> str | None:
    """Transmission id from the fax header: the canonical stamp 'YYYYMMDD-HHMMSS' when the
    header carries a date, else the header with page-number tokens removed and whitespace
    collapsed. The TPA receipt stamp is ignored. Pages of one fax share an id."""
    if not header:
        return None
    stamp = _stamp(header)
    if stamp is not None:
        return stamp.canonical()
    parts = []
    for seg in _header_segments(header):
        text = " ".join(_HEADER_TOKEN.sub(lambda m: m.group("keep") or "", seg).split())
        if text:
            parts.append(text)
    return " | ".join(parts) or None


@dataclass
class _Fax:
    """Transmission identity of one page as the rules see it."""
    id: str | None                 # fax_id(header)
    stamp: _Stamp | None           # _stamp(header)
    k: int | None                  # fax page number; inferred for a page whose header has none
    n: int | None


def _same_header(a: _Fax, b: _Fax) -> bool:
    if a.id is None or b.id is None:
        return False
    if a.stamp is not None and b.stamp is not None:
        return a.stamp.matches(b.stamp)
    return a.id == b.id


def _fax_of(sig: PageSignals, prev: _Fax | None) -> _Fax:
    fax = _Fax(fax_id(sig.fax_header), _stamp(sig.fax_header),
               sig.fax_page.k if sig.fax_page else None, sig.fax_page.n if sig.fax_page else None)
    if fax.k is None and prev is not None and prev.k is not None and _same_header(fax, prev):
        fax.k, fax.n = prev.k + 1, prev.n     # "PAGE" with the number cut off (sample page 158)
    return fax


# ---------- rules 1 to 8 for one page ----------

def _same_fax(cur: PageSignals, prev: PageSignals, cur_f: _Fax, prev_f: _Fax) -> bool:
    """Rule 1. Same stamp and a counter that does not go backwards; or, when the header text
    drifts (sample pages 37-41, 79-83, 157-159), a counter that continues with the same total."""
    if _same_header(cur_f, prev_f):
        return cur_f.k is None or prev_f.k is None or cur_f.k > prev_f.k
    return (cur_f.k is not None and prev_f.k is not None and cur_f.n is not None
            and cur_f.n == prev_f.n and cur_f.k == prev_f.k + 1 and not _dates_differ(cur, prev))


def _headers_contradict(cur_f: _Fax, prev_f: _Fax) -> bool:
    """Rule 5 trigger: two headers that name different transmissions. A header that merely lost
    its date is not a contradiction unless the page counter restarted as well."""
    if cur_f.id is None or prev_f.id is None or _same_header(cur_f, prev_f):
        return False
    if (cur_f.stamp is None) == (prev_f.stamp is None):
        return True
    return cur_f.k is not None and prev_f.k is not None and cur_f.k <= prev_f.k


def _label_continues(cur: PageSignals, prev: PageSignals) -> bool:
    return (cur.page_label is not None and prev.page_label is not None
            and cur.page_label.k == prev.page_label.k + 1
            and cur.doc_type == prev.doc_type)


def _label_restarts(cur: PageSignals, prev: PageSignals) -> bool:
    """A printed 'Page N' (N > 1) followed by a first page (no label, or 'Page 1'): the same
    form printed again inside one fax (sample pages 153-156). Repeated letterheads never do this."""
    return (prev.page_label is not None and prev.page_label.k > 1
            and (cur.page_label is None or cur.page_label.k == 1))


def _dates_differ(cur: PageSignals, prev: PageSignals) -> bool:
    return bool(cur.date_of_service and prev.date_of_service
                and cur.date_of_service != prev.date_of_service)


def _fax_pos(sig: PageSignals) -> str:
    if sig.fax_page is None:
        return "?"
    n = sig.fax_page.n if sig.fax_page.n is not None else "N"
    return f"{sig.fax_page.k}/{n}"


def boundary_reason(cur: PageSignals, prev: PageSignals, cur_f: _Fax, prev_f: _Fax) -> str | None:
    """Section 7 rules 1 to 8 for one page. Returns the reason when `cur` starts a new
    record, or None when it continues the record of `prev`."""
    # A printed first/second page pair is stronger evidence than an unreadable fax
    # counter or a drifting page classifier. Do not suppress headings, resets or dates.
    printed_pair = (prev.page_label is not None and prev.page_label.k == 1
                    and cur.page_label is not None and cur.page_label.k == 2)
    fax_reset = (cur.fax_page is not None and
                 (cur.fax_page.k == 1 or
                  (prev.fax_page is not None and cur.fax_page.k <= prev.fax_page.k)))
    if printed_pair and not cur.starts_new_record and not fax_reset and not _dates_differ(cur, prev):
        return None
    same_fax = _same_fax(cur, prev, cur_f, prev_f)
    if same_fax or _label_continues(cur, prev):
        # Rule 3: inside one fax, or while page labels continue, only a confident new heading
        # with another type, another date or a restarted page label starts a record. A
        # letterhead repeated on every page does not.
        if cur.starts_new_record and cur.boundary_confidence == "high":
            if cur.doc_type != prev.doc_type:
                detail = f"type {prev.doc_type} -> {cur.doc_type}"
            elif _dates_differ(cur, prev):
                detail = f"date {prev.date_of_service} -> {cur.date_of_service}"
            elif _label_restarts(cur, prev):
                detail = f"page label restarts after page {prev.page_label.k}"
            else:
                return None
            where = "inside the same fax" if same_fax else "while page labels continue"
            return f"new document {where} ({detail})"
        return None
    if cur.fax_page is not None and cur.fax_page.k == 1:                       # rule 4
        return f"fax restart {_fax_pos(cur)}"
    if _headers_contradict(cur_f, prev_f):                                     # rule 5
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
    faxes: list[_Fax] = []
    for sig in signals:
        faxes.append(_fax_of(sig, faxes[-1] if faxes else None))
    spans: list[list] = [[1, 1, FIRST_PAGE_REASON]]
    for i in range(1, len(signals)):
        reason = boundary_reason(signals[i], signals[i - 1], faxes[i], faxes[i - 1])
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
