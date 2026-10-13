"""Pure metric functions for the eval. No database, no model calls.

Inputs are GoldRecord / PredRecord lists. Every function returns plain dicts so run_eval.py can
print them and dump them to JSON unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from itertools import combinations


@dataclass
class GoldRecord:
    id: str
    start_page: int
    end_page: int
    date_of_service: str | None = None
    record_type: str | None = None
    accepted_types: list[str] = field(default_factory=list)
    allow_internal_splits: bool = False
    duplicate_of: str | None = None
    duplicate_kind: str | None = None
    note: str = ""

    @property
    def span(self) -> tuple[int, int]:
        return (self.start_page, self.end_page)

    def accepted(self) -> set[str]:
        """accepted_types plus record_type itself (the labeled type is always a hit)."""
        ok = set(self.accepted_types)
        if self.record_type:
            ok.add(self.record_type)
        return ok


@dataclass
class PredRecord:
    id: str
    start_page: int
    end_page: int
    date_of_service: str | None = None  # effective value: override applied on top of the model value
    record_type: str | None = None
    boundary_reason: str = ""

    @property
    def span(self) -> tuple[int, int]:
        return (self.start_page, self.end_page)


@dataclass
class Match:
    kind: str  # exact | tiled | none
    pred_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind != "none"


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def compress_ranges(pages: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for p in sorted(pages):
        if out and p == out[-1][1] + 1:
            out[-1][1] = p
        else:
            out.append([p, p])
    return out


# ---------- gold file ----------

def parse_gold(obj: dict) -> tuple[tuple[int, int], list[GoldRecord]]:
    """Validate a gold file in the section 12 shape. Raises ValueError on bad spans or ids."""
    golds: list[GoldRecord] = []
    for i, r in enumerate(obj.get("records") or [], 1):
        g = GoldRecord(
            id=str(r.get("id") or f"r{i:02d}"),
            start_page=int(r["start_page"]), end_page=int(r["end_page"]),
            date_of_service=r.get("date_of_service"), record_type=r.get("record_type"),
            accepted_types=list(r.get("accepted_types") or []),
            allow_internal_splits=bool(r.get("allow_internal_splits", False)),
            duplicate_of=r.get("duplicate_of"), duplicate_kind=r.get("duplicate_kind"),
            note=r.get("note") or "",
        )
        if g.end_page < g.start_page:
            raise ValueError(f"gold record {g.id}: end_page {g.end_page} < start_page {g.start_page}")
        golds.append(g)
    ids = [g.id for g in golds]
    if len(set(ids)) != len(ids):
        raise ValueError("gold record ids are not unique")
    golds.sort(key=lambda g: g.start_page)
    for a, b in zip(golds, golds[1:]):
        if b.start_page <= a.end_page:
            raise ValueError(f"gold records {a.id} and {b.id} overlap")
    lp = obj.get("labeled_pages")
    if lp:
        lo, hi = int(lp[0]), int(lp[1])
    else:
        lo, hi = 1, (golds[-1].end_page if golds else 0)
    return (lo, hi), golds


def clip_to_window(records, lo: int, hi: int):
    """Drop spans outside [lo, hi] and clip the rest; ids are kept. Sorted by start_page."""
    out = []
    for r in records:
        s, e = max(r.start_page, lo), min(r.end_page, hi)
        if s <= e:
            out.append(replace(r, start_page=s, end_page=e))
    return sorted(out, key=lambda r: r.start_page)


# ---------- boundaries ----------

def match_spans(gold: list[GoldRecord], pred: list[PredRecord]) -> dict[str, Match]:
    """Exact: one predicted span equals the gold span. Tiled: for an allow_internal_splits
    record, the predicted spans inside it cover it from start to end with no gap."""
    by_span = {p.span: p for p in pred}
    ordered = sorted(pred, key=lambda p: p.start_page)
    out: dict[str, Match] = {}
    for g in gold:
        if g.span in by_span:
            out[g.id] = Match("exact", [by_span[g.span].id])
            continue
        if g.allow_internal_splits:
            inside = [p for p in ordered if p.start_page >= g.start_page and p.end_page <= g.end_page]
            tiles = bool(inside) and inside[0].start_page == g.start_page \
                and inside[-1].end_page == g.end_page \
                and all(b.start_page == a.end_page + 1 for a, b in zip(inside, inside[1:]))
            if tiles:
                out[g.id] = Match("tiled", [p.id for p in inside])
                continue
        out[g.id] = Match("none")
    return out


def start_page_metrics(gold: list[GoldRecord], pred: list[PredRecord]) -> dict:
    """Precision, recall, F1 over start pages. A predicted start strictly inside an
    allow_internal_splits gold record is dropped: neither a hit nor a false positive."""
    splittable = [g for g in gold if g.allow_internal_splits]
    gold_starts = {g.start_page: g for g in gold}
    pred_starts: dict[int, PredRecord] = {}
    dropped = 0
    for p in pred:
        if any(g.start_page < p.start_page <= g.end_page for g in splittable):
            dropped += 1
            continue
        pred_starts[p.start_page] = p
    tp = len(gold_starts.keys() & pred_starts.keys())
    fp = sorted(pred_starts.keys() - gold_starts.keys())
    fn = sorted(gold_starts.keys() - pred_starts.keys())
    return {
        "precision": ratio(tp, tp + len(fp)),
        "recall": ratio(tp, tp + len(fn)),
        "f1": ratio(2 * tp, 2 * tp + len(fp) + len(fn)),
        "tp": tp, "fp": len(fp), "fn": len(fn), "dropped_internal": dropped,
        "false_starts": [{"page": s, "pred_id": pred_starts[s].id,
                          "boundary_reason": pred_starts[s].boundary_reason} for s in fp],
        "missed_starts": [{"page": s, "gold_id": gold_starts[s].id, "note": gold_starts[s].note} for s in fn],
    }


def span_match_rate(gold: list[GoldRecord], matches: dict[str, Match]) -> dict:
    exact = sum(1 for g in gold if matches[g.id].kind == "exact")
    tiled = sum(1 for g in gold if matches[g.id].kind == "tiled")
    return {
        "rate": ratio(exact + tiled, len(gold)), "matched": exact + tiled,
        "exact": exact, "tiled": tiled, "total": len(gold),
        "unmatched": [{"gold_id": g.id, "span": list(g.span), "note": g.note}
                      for g in gold if not matches[g.id].ok],
    }


def page_accuracy(gold: list[GoldRecord], pred: list[PredRecord], lo: int, hi: int) -> dict:
    """A page is right when its predicted span equals the gold span that holds it, or lies
    inside that gold span when the gold record allows internal splits."""
    gold_at: dict[int, GoldRecord] = {}
    for g in gold:
        for p in range(g.start_page, g.end_page + 1):
            gold_at[p] = g
    pred_at: dict[int, PredRecord] = {}
    for q in pred:
        for p in range(q.start_page, q.end_page + 1):
            pred_at[p] = q
    hits = total = uncovered = 0
    misses: list[int] = []
    for p in range(lo, hi + 1):
        g = gold_at.get(p)
        if g is None:
            uncovered += 1
            continue
        total += 1
        q = pred_at.get(p)
        inside = q is not None and g.start_page <= q.start_page and q.end_page <= g.end_page
        if q is not None and (q.span == g.span or (g.allow_internal_splits and inside)):
            hits += 1
        else:
            misses.append(p)
    return {"accuracy": ratio(hits, total), "hits": hits, "total": total,
            "uncovered_pages": uncovered, "miss_ranges": compress_ranges(misses)}


def field_accuracy(gold: list[GoldRecord], pred: list[PredRecord], matches: dict[str, Match]) -> dict:
    """Date and type accuracy on matched gold records. For a tiled match the head piece (the
    predicted span that starts where the gold record starts) is the one compared."""
    by_id = {p.id: p for p in pred}
    rows: list[dict] = []
    date_hits = type_hits = 0
    for g in gold:
        m = matches[g.id]
        if not m.ok:
            continue
        head = by_id[m.pred_ids[0]]
        date_hit = normalize_date(head.date_of_service) == normalize_date(g.date_of_service)
        type_hit = head.record_type in g.accepted()
        date_hits += int(date_hit)
        type_hits += int(type_hit)
        rows.append({"gold_id": g.id, "kind": m.kind, "pred_id": head.id,
                     "gold_date": g.date_of_service, "pred_date": head.date_of_service, "date_hit": date_hit,
                     "accepted_types": sorted(g.accepted()), "pred_type": head.record_type, "type_hit": type_hit})
    n = len(rows)
    return {"n_matched": n, "date_accuracy": ratio(date_hits, n), "date_hits": date_hits,
            "type_accuracy": ratio(type_hits, n), "type_hits": type_hits, "rows": rows}


# ---------- duplicates ----------

def duplicate_metrics(gold: list[GoldRecord], matches: dict[str, Match], groups: dict[str, str]) -> dict:
    """groups: predicted record id -> duplicate group id (records in no group are absent).
    Gold pairs are the transitive closure of duplicate_of. Predicted pairs are all pairs of
    distinct gold records inside one predicted group, mapped through exact or tiled matches."""
    pred_to_gold = {pid: g.id for g in gold for pid in matches[g.id].pred_ids}
    parent = {g.id: g.id for g in gold}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    explicit: dict[frozenset, str] = {}
    warnings: list[str] = []
    for g in gold:
        if not g.duplicate_of:
            continue
        if g.duplicate_of not in parent or g.duplicate_of == g.id:
            warnings.append(f"{g.id}: duplicate_of {g.duplicate_of!r} is not another gold record")
            continue
        parent[find(g.id)] = find(g.duplicate_of)
        explicit[frozenset((g.id, g.duplicate_of))] = g.duplicate_kind or "unspecified"
    by_root: dict[str, list[str]] = {}
    for g in gold:
        by_root.setdefault(find(g.id), []).append(g.id)
    gold_pairs = {frozenset(p) for ids in by_root.values() for p in combinations(ids, 2)}

    by_group: dict[str, set[str]] = {}
    for pid, gid in groups.items():
        if pid in pred_to_gold:
            by_group.setdefault(gid, set()).add(pred_to_gold[pid])
    pred_pairs = {frozenset(p) for ids in by_group.values() for p in combinations(sorted(ids), 2)}

    hits = gold_pairs & pred_pairs
    false_pairs = pred_pairs - gold_pairs
    matched = {g.id for g in gold if matches[g.id].ok}
    rows = []
    for pair in sorted(gold_pairs, key=sorted):
        a, b = sorted(pair)
        if pair in hits:
            status = "hit"
        elif a in matched and b in matched:
            status = "miss"
        else:
            status = "unmapped"  # a boundary error means the pair could never be found
        rows.append({"pair": [a, b], "kind": explicit.get(pair, "transitive"), "status": status})
    return {"gold_pairs": len(gold_pairs), "hits": len(hits), "hit_rate": ratio(len(hits), len(gold_pairs)),
            "false_pairs": len(false_pairs), "false_pair_list": sorted(sorted(p) for p in false_pairs),
            "pairs": rows, "warnings": warnings}


def evaluate(gold_obj: dict, preds: list[PredRecord], groups: dict[str, str]) -> dict:
    """All boundary metrics for one document, inside the gold file's labeled_pages window."""
    (lo, hi), gold = parse_gold(gold_obj)
    gold = clip_to_window(gold, lo, hi)
    pred = clip_to_window(preds, lo, hi)
    matches = match_spans(gold, pred)
    matched_pred = {pid for m in matches.values() for pid in m.pred_ids}
    return {
        "labeled_pages": [lo, hi], "n_gold": len(gold), "n_pred": len(pred),
        "starts": start_page_metrics(gold, pred),
        "spans": span_match_rate(gold, matches),
        "pages": page_accuracy(gold, pred, lo, hi),
        "fields": field_accuracy(gold, pred, matches),
        "duplicates": duplicate_metrics(gold, matches, groups),
        "unmatched_pred": [{"id": p.id, "span": list(p.span), "boundary_reason": p.boundary_reason}
                           for p in pred if p.id not in matched_pred],
    }


# ---------- dates ----------

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_NAMES = (r"january|february|march|april|may|june|july|august|september|october|november|december|"
                r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec")
# Same separator on both sides and never ".", so fractions and decimals (2.5/10, 3.5-40) are not dates.
_DATE_PATTERNS = [
    # 2025-10-30, 2025/10/30
    re.compile(r"\b(?P<y>\d{4})(?P<sep>[-/])(?P<m>\d{1,2})(?P=sep)(?P<d>\d{1,2})\b"),
    # 10/30/2025, 10-30-2025, 10/30/25
    re.compile(r"\b(?P<m>\d{1,2})(?P<sep>[-/])(?P<d>\d{1,2})(?P=sep)(?P<y>\d{4}|\d{2})\b"),
    # October 30, 2025 / Oct 30 2025 / Oct. 30th, 2025 / Dec/29/2025 (fax header style)
    re.compile(r"\b(?P<mon>" + _MONTH_NAMES + r")\.?[\s/\-]+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?[\s/\-]+(?P<y>\d{4})\b",
               re.IGNORECASE),
    # 30 October 2025
    re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>" + _MONTH_NAMES + r")\.?,?\s+(?P<y>\d{4})\b",
               re.IGNORECASE),
]


# Two-digit years stay at most ten years ahead of today: "1/5/49" is a 1949 birth date, not 2049.
_TWO_DIGIT_YEAR_HORIZON = date.today().year + 10


def _to_iso(m: re.Match) -> str | None:
    gd = m.groupdict()
    y = int(gd["y"])
    if len(gd["y"]) == 2:
        y += 2000 if 2000 + y <= _TWO_DIGIT_YEAR_HORIZON else 1900
    month = int(gd["m"]) if gd.get("m") else _MONTHS[gd["mon"][:3].lower()]
    d = int(gd["d"])
    if not (1900 <= y <= 2099 and 1 <= month <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{month:02d}-{d:02d}"


def extract_dates(text: str | None) -> list[tuple[str, str]]:
    """Every date in the text as (raw text, YYYY-MM-DD), in order of appearance."""
    found: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text or ""):
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue
            iso = _to_iso(m)
            if iso is None:
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.group(0), iso))
    found.sort()
    return [(raw, iso) for _, raw, iso in found]


def normalize_date(value: str | None) -> str | None:
    """YYYY-MM-DD when the value holds a date, the stripped value otherwise, None for empty."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    dates = extract_dates(text)
    return dates[0][1] if dates else text


def date_rule_check(fields: dict[str, object], transcription: str) -> list[dict]:
    """Every date in the generated fields must appear somewhere in the transcription, in any
    written form. Returns one row per (field, date) that does not."""
    have = {iso for _, iso in extract_dates(transcription)}
    out: list[dict] = []
    for name, value in fields.items():
        if value is None:
            continue
        text = "\n".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
        seen: set[str] = set()
        for raw, iso in extract_dates(text):
            if iso in have or iso in seen:
                continue
            seen.add(iso)
            out.append({"field": name, "text": raw, "date": iso})
    return out
