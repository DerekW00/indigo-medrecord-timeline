"""Stage 5: exact and near-duplicate detection across all documents (spec section 8).

Rules in short:
- normalize: lowercase, drop fax header lines and the TPA stamp, keep [a-z0-9 ], collapse spaces
- exact: same sha256 of the normalized text, across any documents
- near: same record_type, same effective date (or one null), Jaccard of word 3-gram shingles >= 0.7
- union-find the pairs; canonical = most pages, then earliest document, then lowest start page

`find_duplicates` is pure (unit tested); `dedupe_all` is the thin DB wrapper that rebuilds the
whole duplicate_groups table. It must run whenever its inputs change, or the table goes stale and
a copy whose canonical is gone (or hidden) is filtered off the timeline as a "duplicate":
- after `group_document` (a re-group can delete a canonical record; its copies keep pointing at it)
- after summarize, on the success and the failure path (records that did finish are `done`)
- after an override write or reset that changes date_of_service, record_type or hidden
  (`pairing_changed` tells the API whether a write needs it)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .. import db

log = logging.getLogger(__name__)

NEAR_THRESHOLD = 0.7   # Jaccard at or above this is a near duplicate
SHINGLE_SIZE = 3       # words per shingle
MIN_WORDS = 8          # shorter normalized texts (blank or failed pages) never take part
PAIRING_FIELDS = ("date_of_service", "record_type", "hidden")   # override fields that drive the pairing

_recompute_lock = threading.Lock()   # worker and API may both recompute; the later caller reads newer state

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|[a-z]{3}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}"
    r"|[a-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\b"
)
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?")
_TZ_RE = re.compile(r"\b(?:[pmce][sd]t|utc|gmt)\b")
_FRACTION_RE = re.compile(r"(?<![\d/])(\d{1,3})\s*/\s*(\d{1,3})(?![\d/])")
_PAGE_WORDS_RE = re.compile(r"\b(?:page|pg|of|fax|server)\b")
_STAMP_RE = re.compile(r"received by tpa\b.{0,24}?\bfax\b(?:\s*\d{1,2}/\d{1,2}/\d{2,4})?")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_ALNUM_RE = re.compile(r"[a-z0-9]")


# ---------- normalization ----------

def _drop_page_fractions(s: str) -> str:
    """Remove fax-style page fractions like 2/005 or 13/25 (k <= n); keep 120/80 style values."""
    return _FRACTION_RE.sub(lambda m: " " if 1 <= int(m.group(1)) <= int(m.group(2)) else m.group(0), s)


def _has_page_fraction(line: str) -> bool:
    return _drop_page_fractions(_DATE_RE.sub(" ", line)) != _DATE_RE.sub(" ", line)


def _is_fax_header_line(line: str) -> bool:
    """True for fax transmission header lines, which differ between copies of one record:
    '10/30/2025 3:17:27 pm pdt page 2/005 fax server', 'dec/29/2025 2:01:57 pm kaiser 2/6',
    a bare timestamp line, or a bare 'page 2/005' line."""
    if not line or len(line) > 160:
        return False
    stamp = bool(_DATE_RE.search(line) and _TIME_RE.search(line))
    if stamp and ("fax" in line or "page" in line or _has_page_fraction(line)):
        return True
    rest = _TZ_RE.sub(" ", _TIME_RE.sub(" ", _DATE_RE.sub(" ", line))) if stamp else line
    rest = _PAGE_WORDS_RE.sub(" ", _drop_page_fractions(rest))
    return not _ALNUM_RE.search(rest)


def normalize_text(text: str) -> str:
    """Lowercase, drop fax header lines and the 'Received by TPA ICS Fax' stamp,
    keep only [a-z0-9 ] (everything else becomes a space), collapse whitespace. Idempotent."""
    lines = [ln for ln in (text or "").lower().splitlines() if not _is_fax_header_line(ln.strip())]
    joined = _STAMP_RE.sub(" ", "\n".join(lines))
    return " ".join(_NON_ALNUM_RE.sub(" ", joined).split())


def content_hash(text: str) -> str:
    """sha256 hex of the normalized text (pass the raw concatenated page text)."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def shingles(normalized: str, k: int = SHINGLE_SIZE) -> set[tuple[str, ...]]:
    """Word k-gram shingles of a normalized text. Texts shorter than k words give one shingle."""
    words = normalized.split()
    if not words:
        return set()
    if len(words) < k:
        return {tuple(words)}
    return {tuple(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _norm_date(value) -> str | None:
    """Effective dates come from the model (YYYY-MM-DD) or from a user edit (any common form)."""
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


# ---------- pairing ----------

@dataclass
class _Item:
    id: str
    doc_created_at: str
    start_page: int
    end_page: int
    date: str | None
    record_type: str | None
    hash: str
    shingles: set
    words: int

    @property
    def pages(self) -> int:
        return self.end_page - self.start_page + 1


def _prepare(r: dict) -> _Item:
    norm = normalize_text(r.get("text") or "")
    return _Item(
        id=r["id"], doc_created_at=str(r.get("doc_created_at") or ""),
        start_page=int(r.get("start_page") or 0), end_page=int(r.get("end_page") or 0),
        date=_norm_date(r.get("date_of_service")), record_type=r.get("record_type"),
        hash=hashlib.sha256(norm.encode("utf-8")).hexdigest(),
        shingles=shingles(norm), words=len(norm.split()),
    )


def _canonical_key(it: _Item):
    return (-it.pages, it.doc_created_at, it.start_page, it.id)


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def find_duplicates(records: list[dict], threshold: float = NEAR_THRESHOLD) -> list[dict]:
    """Pure: records have id, doc_id, doc_created_at, start_page, end_page, date_of_service,
    record_type, text. Returns duplicate_groups rows {record_id, group_id, is_canonical, reason,
    score} for every member of a group of size >= 2; group_id is the canonical record id."""
    seen: set[str] = set()
    items: list[_Item] = []
    for r in records:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        it = _prepare(r)
        if it.words >= MIN_WORDS:
            items.append(it)
    uf = _UnionFind(it.id for it in items)

    by_hash: dict[str, list[_Item]] = defaultdict(list)
    for it in items:
        by_hash[it.hash].append(it)
    for same in by_hash.values():
        for other in same[1:]:
            uf.union(same[0].id, other.id)

    by_type: dict[str | None, list[_Item]] = defaultdict(list)
    for it in items:
        by_type[it.record_type].append(it)
    for bucket in by_type.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if a.hash == b.hash or (a.date and b.date and a.date != b.date):
                    continue
                sizes = (len(a.shingles), len(b.shingles))
                if min(sizes) / max(sizes) < threshold:   # Jaccard can never reach the threshold
                    continue
                if jaccard(a.shingles, b.shingles) >= threshold:
                    uf.union(a.id, b.id)

    groups: dict[str, list[_Item]] = defaultdict(list)
    for it in items:
        groups[uf.find(it.id)].append(it)
    rows: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = min(members, key=_canonical_key)
        all_exact = True
        for m in members:
            if m is canonical:
                continue
            exact = m.hash == canonical.hash
            all_exact = all_exact and exact
            rows.append({"record_id": m.id, "group_id": canonical.id, "is_canonical": 0,
                         "reason": "exact" if exact else "similar",
                         "score": 1.0 if exact else round(jaccard(m.shingles, canonical.shingles), 4)})
        rows.append({"record_id": canonical.id, "group_id": canonical.id, "is_canonical": 1,
                     "reason": "exact" if all_exact else "similar", "score": 1.0})
    rows.sort(key=lambda r: (r["group_id"], -r["is_canonical"], r["record_id"]))
    return rows


# ---------- DB wrapper ----------

def pairing_changed(before: dict | None, after: dict | None) -> bool:
    """True when an override write or reset changed a field the pairing depends on
    (date_of_service, record_type, hidden). `before`/`after` are record_overrides rows or None.
    An absent, null, empty or 0 value all mean "no override on that field"."""
    b, a = before or {}, after or {}
    return any((b.get(k) or None) != (a.get(k) or None) for k in PAIRING_FIELDS)


def dedupe_all() -> None:
    """Recompute duplicate groups over every done record of every document and store them.
    Effective date and type (user overrides applied) drive the pairing; hidden records are skipped.
    Records that are pending or failed (for example just re-grouped) take no part, so any row that
    pointed at a deleted or not-yet-summarized canonical is dropped by the rebuild.
    Serialized: concurrent callers run one after another, each over the state current at its start."""
    with _recompute_lock:
        overrides = db.get_overrides()
        page_text: dict[str, dict[int, str]] = {}
        items: list[dict] = []
        for r in db.all_done_records():
            ov = overrides.get(r["id"]) or {}
            if ov.get("hidden"):
                continue
            if r["doc_id"] not in page_text:
                page_text[r["doc_id"]] = {p["page_no"]: p["text"] or "" for p in db.get_pages(r["doc_id"])}
            pages = page_text[r["doc_id"]]
            items.append({
                "id": r["id"], "doc_id": r["doc_id"], "doc_created_at": r.get("doc_created_at"),
                "start_page": r["start_page"], "end_page": r["end_page"],
                "date_of_service": ov.get("date_of_service") or r.get("date_of_service"),
                "record_type": ov.get("record_type") or r.get("record_type"),
                "text": "\n".join(pages.get(n, "") for n in range(r["start_page"], r["end_page"] + 1)),
            })
        rows = find_duplicates(items)
        db.replace_duplicate_groups(rows)
    log.info("dedupe: %d records, %d duplicate groups, %d grouped records",
             len(items), len({row["group_id"] for row in rows}), len(rows))


async def dedupe_all_async() -> None:
    """Same as dedupe_all, off the event loop (hashing and shingling are CPU work)."""
    await asyncio.to_thread(dedupe_all)
