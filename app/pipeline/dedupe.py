"""Exact image containment and conservative, model-confirmed duplicate detection.

Every member is compared directly with the longest canonical record. Image evidence
wins over OCR discrepancies; near matches require a valid model decision.
The API's awaitable wrapper and the existing duplicate-table shape are preserved.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config, db, llm
from .prompts_util import PROMPTS_DIR, load_prompt

log = logging.getLogger(__name__)

NEAR_THRESHOLD = 0.85  # Candidate only: confirmation is mandatory
UNCERTAIN_THRESHOLD = 0.85  # Explicit uncertainty may qualify under the F2 rule
SHINGLE_SIZE = 3       # words per shingle
MIN_WORDS = 8          # shorter normalized texts (blank or failed pages) never take part
PAIRING_FIELDS = ("date_of_service", "record_type", "hidden")   # override fields that drive the pairing

_recompute_lock = threading.Lock()   # worker and API may both recompute; the later caller reads newer state
_confirmation_cache_lock = threading.Lock()

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
    return None


# ---------- conservative pairing ----------

_CLINICAL_TOKEN_RE = re.compile(
    r"[+\-−]?(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+(?:\.\d+)?)?|[a-z]+(?:['’][a-z]+)?|[+\-−%°<>≤≥=]"
)


def clinical_text(text: str) -> str:
    """Remove identified transmission metadata, keeping clinical punctuation and negation."""
    lines = []
    for line in (text or "").lower().splitlines():
        line = _STAMP_RE.sub(" ", line).strip()
        timestamp = _DATE_RE.search(line) and _TIME_RE.search(line)
        identified = (timestamp and ("fax" in line or "page" in line or _has_page_fraction(line)))
        numbered_header = re.fullmatch(r"(?:page|pg)\s*\d+\s*/\s*\d+", line)
        if identified or numbered_header:
            continue
        lines.append(line)
    return " ".join(" ".join(lines).split())


def clinical_tokens(text: str) -> set[str]:
    return set(_CLINICAL_TOKEN_RE.findall(clinical_text(text)))


def candidate_score(a: dict, b: dict) -> float:
    date_a, date_b = _norm_date(a.get("date_of_service")), _norm_date(b.get("date_of_service"))
    if not date_a or not date_b or date_a != date_b:
        return 0.0
    if not a.get("record_type") or a["record_type"] != b.get("record_type"):
        return 0.0
    return jaccard(clinical_tokens(a.get("text", "")), clinical_tokens(b.get("text", "")))


def _canonical_key(r: dict):
    return (-(r["end_page"] - r["start_page"] + 1), str(r.get("doc_created_at") or ""),
            r["start_page"], r["id"])


def _images(r: dict) -> Counter | None:
    hashes = r.get("image_hashes")
    if not hashes or len(hashes) != r["end_page"] - r["start_page"] + 1 or not all(hashes):
        return None
    return Counter(hashes)


def exact_relation(a: dict, b: dict) -> str | None:
    ah, bh = _images(a), _images(b)
    if ah and bh and bh <= ah:
        return "exact_image" if ah == bh else "partial_exact"
    # Preserve signs/decimals: the old punctuation-stripping hash cannot prove clinical equality.
    at, bt = clinical_text(a.get("text", "")), clinical_text(b.get("text", ""))
    if (a.get("text_complete", True) and b.get("text_complete", True)
            and len(at.split()) >= MIN_WORDS and at == bt):
        return "exact"
    return None


class DuplicateConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relation: Literal["duplicate", "complementary", "distinct", "different", "uncertain"]
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(min_length=1)
    a_pages: list[int]
    b_pages: list[int]
    unique_content: str


def confirmed_duplicate(decision: DuplicateConfirmation, a: dict, b: dict) -> bool:
    """Require affirmative evidence for every page and no unique clinical content."""
    return (decision.relation == "duplicate" and decision.confidence == "high"
            and not decision.unique_content.strip()
            and set(decision.a_pages) == set(range(a["start_page"], a["end_page"] + 1))
            and set(decision.b_pages) == set(range(b["start_page"], b["end_page"] + 1)))


def confirmation_reason(decision, a: dict, b: dict) -> str | None:
    """Shared F2 policy; only validated, complete decisions can hide a near copy.

    The uncertain fallback intentionally does not require a high-confidence
    duplicate verdict or an empty unique_content explanation. Explicit distinct,
    different and complementary verdicts always veto this fallback.
    """
    if decision is None:
        return None
    try:
        payload = decision.model_dump() if isinstance(decision, DuplicateConfirmation) else decision
        decision = DuplicateConfirmation.model_validate(payload, strict=True)
    except ValidationError:
        return None
    if (not a.get("text_complete", True) or not b.get("text_complete", True)
            or len(a.get("transcription", a.get("text", "")))
            + len(b.get("transcription", b.get("text", ""))) > 80000
            or candidate_score(a, b) < NEAR_THRESHOLD):
        return None
    if confirmed_duplicate(decision, a, b):
        return "similar_confirmed"
    if (decision.relation == "uncertain"
            and set(decision.a_pages) == set(range(a["start_page"], a["end_page"] + 1))
            and set(decision.b_pages) == set(range(b["start_page"], b["end_page"] + 1))
            and candidate_score(a, b) >= UNCERTAIN_THRESHOLD):
        return "similar"
    return None


def _rows_for_group(a: dict, members: list[dict]) -> list[dict]:
    if not members:
        return []
    reasons = {m["reason"] for m in members}
    reason = next(r for r in ("similar", "similar_confirmed", "partial_exact", "exact_image", "exact") if r in reasons)
    return [{"record_id": a["id"], "group_id": a["id"], "is_canonical": 1,
             "reason": reason, "score": 1.0}, *members]


def _member(a: dict, b: dict, reason: str, score: float = 1.0) -> dict:
    return {"record_id": b["id"], "group_id": a["id"], "is_canonical": 0,
            "reason": reason, "score": score}


def find_duplicates(records: list[dict], threshold: float = NEAR_THRESHOLD, *,
                    exact_only: bool = False, confirmations: dict | None = None) -> list[dict]:
    """Pure grouping; optional injected decisions obey the same production confirmation gates."""
    items = sorted({r["id"]: r for r in records}.values(), key=_canonical_key)
    assigned, rows = set(), []
    for i, a in enumerate(items):
        if a["id"] in assigned:
            continue
        members = []
        for b in items[i + 1:]:
            if b["id"] == a["id"] or b["id"] in assigned:
                continue
            reason, score = exact_relation(a, b), 1.0
            if not reason and not exact_only:
                score = candidate_score(a, b)
                decision = (confirmations or {}).get((a["id"], b["id"]))
                if score >= max(threshold, NEAR_THRESHOLD) and decision is not None:
                    reason = confirmation_reason(decision, a, b)
            if reason:
                members.append(_member(a, b, reason, score))
                assigned.add(b["id"])
        if members:
            assigned.add(a["id"])
            rows.extend(_rows_for_group(a, members))
    return sorted(rows, key=lambda r: (r["group_id"], -r["is_canonical"], r["record_id"]))


def image_hash(path: str | None) -> str | None:
    try:
        data = Path(path).read_bytes() if path else b""
        return hashlib.sha256(data).hexdigest() if data else None
    except OSError:
        return None


# ---------- DB wrapper ----------

def pairing_changed(before: dict | None, after: dict | None) -> bool:
    """True when an override write or reset changed a field the pairing depends on
    (date_of_service, record_type, hidden). `before`/`after` are record_overrides rows or None.
    An absent, null, empty or 0 value all mean "no override on that field"."""
    b, a = before or {}, after or {}
    return any((b.get(k) or None) != (a.get(k) or None) for k in PAIRING_FIELDS)


def _load_items() -> list[dict]:
    overrides = db.get_overrides()
    page_data = {}
    items = []
    for r in db.all_done_records():
        ov = overrides.get(r["id"]) or {}
        if ov.get("hidden"):
            continue
        if r["doc_id"] not in page_data:
            pages = db.get_pages(r["doc_id"])
            page_data[r["doc_id"]] = {p["page_no"]: {**p, "image_hash": image_hash(p["image_path"])}
                                      for p in pages}
        pages = page_data[r["doc_id"]]
        numbers = range(r["start_page"], r["end_page"] + 1)
        items.append({**r,
            "date_of_service": ov.get("date_of_service") or r.get("date_of_service"),
            "record_type": ov.get("record_type") or r.get("record_type"),
            "image_hashes": [pages.get(n, {}).get("image_hash") for n in numbers],
            "text": "\n".join(pages.get(n, {}).get("text") or "" for n in numbers),
            "transcription": "\n".join(f"=== Page {n} ===\n{pages.get(n, {}).get('text') or ''}"
                                          for n in numbers),
            "text_complete": all(pages.get(n, {}).get("status") == "done"
                                 and (pages.get(n, {}).get("text") or "").strip() for n in numbers),
        })
    return sorted(items, key=_canonical_key)


def confirmation_cache_key(a: dict, b: dict) -> str:
    """Fingerprint the complete ordered evidence and exact prompt; performs file I/O."""
    fields = ("id", "doc_id", "start_page", "end_page", "text", "transcription",
              "text_complete", "date_of_service", "record_type")
    payload = {
        "version": 1, "model": config.OPENAI_MODEL,
        "prompt": (PROMPTS_DIR / "duplicate_confirm.md").read_text(encoding="utf-8"),
        "a": {field: a.get(field) for field in fields},
        "b": {field: b.get(field) for field in fields},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read_cached_confirmation(key: str) -> DuplicateConfirmation | None:
    if config.FAKE_LLM:
        return None
    try:
        payload = json.loads((config.DATA_DIR / "duplicate_confirmations" / f"{key}.json").read_text())
        if not isinstance(payload, dict) or payload.get("key") != key:
            return None
        return DuplicateConfirmation.model_validate(payload.get("decision"), strict=True)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def save_cached_confirmation(a: dict, b: dict, decision, *, _key: str | None = None
                             ) -> DuplicateConfirmation | None:
    """Atomically retain the first valid decision. Call off the event loop.

    This also permits explicit provenance-backed seeding of an isolated store.
    Fake mode never reads or writes the cache.
    """
    if config.FAKE_LLM:
        return None
    decision = DuplicateConfirmation.model_validate(
        decision.model_dump() if isinstance(decision, DuplicateConfirmation) else decision, strict=True)
    key = _key or confirmation_cache_key(a, b)
    with _confirmation_cache_lock:
        existing = _read_cached_confirmation(key)
        if existing is not None:
            return existing
        directory = config.DATA_DIR / "duplicate_confirmations"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{key}.json"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".confirmation-", delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump({"key": key, "decision": decision.model_dump()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard linking publishes a complete entry without replacing a first writer.
            try:
                os.link(temp_path, target)
            except FileExistsError:
                existing = _read_cached_confirmation(key)
                if existing is not None:
                    return existing
                os.replace(temp_path, target)  # Replace a corrupt entry only.
            return decision
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


async def confirm_pair(a: dict, b: dict) -> DuplicateConfirmation | None:
    if config.FAKE_LLM:
        return None  # A skipped call is not a valid uncertain model response.
    if config.OPENAI_MODEL != "gpt-6-luna":
        raise ValueError("duplicate confirmation requires gpt-6-luna")
    key = await asyncio.to_thread(confirmation_cache_key, a, b)
    cached = await asyncio.to_thread(_read_cached_confirmation, key)
    if cached is not None:
        return cached
    # Read current contents instead of the general loader's process-local memoization.
    loader = getattr(load_prompt, "__wrapped__", load_prompt)
    system, template = await asyncio.to_thread(loader, "duplicate_confirm")
    user = template.format(a_start=a["start_page"], a_end=a["end_page"],
                           b_start=b["start_page"], b_end=b["end_page"],
                           a_text=a["transcription"], b_text=b["transcription"])
    decision = await llm.text_json(system, user, DuplicateConfirmation,
                                  f"dedupe {a['id']} {b['id']}")
    if decision is None:
        return None
    return await asyncio.to_thread(save_cached_confirmation, a, b, decision, _key=key)


async def dedupe_all(*, exact_only: bool = False, max_confirmations: int = 8,
                     evidence: list[dict] | None = None) -> None:
    """Serialize recomputation; disk/hash/SQLite work runs off the event loop.

    Failed, incomplete and over-budget calls keep both records. Valid uncertainty
    follows the shared F2 similarity rule.
    Publish atomically only after the complete pass; cancellation leaves the prior table.
    """
    while not _recompute_lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        items = await asyncio.to_thread(_load_items)
        assigned, rows, calls = set(), [], 0
        for i, a in enumerate(items):
            if a["id"] in assigned:
                continue
            members = []
            for b in items[i + 1:]:
                if b["id"] == a["id"] or b["id"] in assigned:
                    continue
                reason, score = exact_relation(a, b), 1.0
                if not reason and not exact_only:
                    score = candidate_score(a, b)
                    if score >= NEAR_THRESHOLD:
                        entry = {"a": a["id"], "b": b["id"], "score": score,
                                 "model": config.OPENAI_MODEL, "fake": config.FAKE_LLM}
                        complete = a["text_complete"] and b["text_complete"]
                        # Do not truncate evidence to manufacture certainty.
                        fits = len(a["transcription"]) + len(b["transcription"]) <= 80000
                        if not complete or not fits or calls >= max_confirmations:
                            entry["skipped"] = "incomplete text" if not complete else (
                                "input too long" if not fits else "confirmation budget")
                        else:
                            calls += 1
                            try:
                                decision = await confirm_pair(a, b)
                                if decision is None:
                                    entry["skipped"] = "no confirmation"
                                else:
                                    decision = DuplicateConfirmation.model_validate(
                                        decision.model_dump() if isinstance(decision, DuplicateConfirmation)
                                        else decision, strict=True)
                                    entry["decision"] = decision.model_dump()
                                    reason = confirmation_reason(decision, a, b)
                            except Exception as exc:
                                # Never log source text carried by adapter validation errors.
                                entry["error"] = type(exc).__name__
                        entry["accepted"] = reason in {"similar_confirmed", "similar"}
                        entry["reason"] = reason
                        if evidence is not None:
                            evidence.append(entry)
                if reason:
                    members.append(_member(a, b, reason, score))
                    assigned.add(b["id"])
            if members:
                assigned.add(a["id"])
                rows.extend(_rows_for_group(a, members))
        await asyncio.to_thread(db.replace_duplicate_groups, rows)
        log.info("dedupe: %d records, %d groups, %d confirmations", len(items),
                 len({r["group_id"] for r in rows}), calls)
    finally:
        _recompute_lock.release()


async def dedupe_all_async() -> None:
    """Compatibility entry point for the existing API."""
    await dedupe_all()
