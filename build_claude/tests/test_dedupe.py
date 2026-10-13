"""Unit tests for app.pipeline.dedupe (spec section 8)."""
from __future__ import annotations

import asyncio
import pytest

from app import config, db
from app.models import record_id_for
from app.pipeline import dedupe
from app.pipeline.dedupe import content_hash, find_duplicates, jaccard, normalize_text, shingles

PR2 = """KAISER PERMANENTE on-the-job
PRIMARY TREATING PHYSICIAN'S PROGRESS REPORT (PR-2)
Patient: [REDACTED]  DOB: [REDACTED]  Claim No: [REDACTED]
Date of injury: 09/30/2024    Date of exam: 10/30/2025
Subjective: The patient returns for follow-up of a left ankle fracture treated with open reduction
and internal fixation. Pain is improving, rated 3 of 10, worse with prolonged standing.
Objective: Well healed incisions. Mild swelling of the left ankle. Dorsiflexion 10 degrees,
plantar flexion 30 degrees. Neurovascularly intact distally. Gait antalgic without assistive device.
Assessment: Left ankle fracture status post ORIF, healing as expected.
Plan: Continue physical therapy twice a week for six weeks. Weight bearing as tolerated in a
walking boot. Return to clinic in six weeks with repeat radiographs.
Work status: Modified duty, seated work only, no lifting over 10 pounds, through 12/15/2025.
Signed: John Smith, MD, Occupational Medicine
DWC Form PR-2 (10/2015)  Page 1
"""

OTHER_PR2 = """KAISER PERMANENTE on-the-job
PRIMARY TREATING PHYSICIAN'S PROGRESS REPORT (PR-2)
Patient: [REDACTED]  DOB: [REDACTED]  Claim No: [REDACTED]
Date of injury: 09/30/2024    Date of exam: 10/30/2025
Subjective: Seen today for right shoulder pain after lifting boxes at work. Reports clicking and
night pain, rated 6 of 10, no numbness or tingling in the arm.
Objective: Tenderness over the greater tuberosity. Positive Hawkins and Neer signs. Forward flexion
150 degrees, abduction 140 degrees. Strength 4 of 5 in external rotation. Cervical spine normal.
Assessment: Right shoulder impingement syndrome, rule out rotator cuff tear.
Plan: MRI of the right shoulder without contrast. Home exercise program. Ibuprofen 600 mg three
times daily with food. Follow up after imaging.
Work status: Modified duty, no overhead work and no lifting over 5 pounds with the right arm.
Signed: Jane Doe, DO, Occupational Medicine
DWC Form PR-2 (10/2015)  Page 1
"""

FAX_A = "10/30/2025 3:17:27 PM PDT  PAGE 2/005  Fax Server\n"
FAX_B = "Dec/29/2025 2:01:57 PM  Kaiser  2/6\n"
STAMP = "Received by TPA ICS Fax 10/30/25\n"


def rec(rid, text, *, doc="docA", created="2026-01-01T00:00:00+00:00", start=1, end=2,
        date="2025-10-30", rtype="progress_report"):
    return {"id": rid, "doc_id": doc, "doc_created_at": created, "start_page": start, "end_page": end,
            "date_of_service": date, "record_type": rtype, "text": text}


def with_typos(text: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        assert old in text, old
        text = text.replace(old, new, 1)
    return text


def decision(a, b):
    return dedupe.DuplicateConfirmation(relation="duplicate", confidence="high", reason="synthetic copy",
        a_pages=list(range(a["start_page"], a["end_page"] + 1)),
        b_pages=list(range(b["start_page"], b["end_page"] + 1)), unique_content="")


async def approve(a, b):
    return decision(a, b)


def confirmed(records):
    return find_duplicates(records, confirmations={(a["id"], b["id"]): decision(a, b)
                                                   for a in records for b in records if a != b})


# ---------- normalization ----------

def test_normalize_drops_fax_header_lines_and_stamp():
    raw = FAX_A + PR2 + STAMP
    norm = normalize_text(raw)
    assert norm == normalize_text(PR2)
    assert "fax server" not in norm and "received by tpa" not in norm and "3 17 27" not in norm
    assert norm.startswith("kaiser permanente on the job primary treating physician s progress report")
    assert "date of injury 09 30 2024" in norm        # dates inside the body survive as words
    assert "dwc form pr 2 10 2015 page 1" in norm      # the printed page label survives


def test_normalize_handles_split_headers_and_charset():
    raw = "10/30/2025 3:17:27 PM PDT\nPAGE 2/005\n" + PR2 + "RECEIVED BY TPA ICS FAX 10/30/25"
    assert normalize_text(raw) == normalize_text(PR2)
    assert normalize_text("  Héllo,  World!!\n\tBP 120/80 ") == "h llo world bp 120 80"
    assert normalize_text("") == "" and normalize_text(None) == ""


def test_normalize_is_idempotent_and_hash_matches():
    once = normalize_text(FAX_A + PR2 + STAMP)
    assert normalize_text(once) == once
    assert content_hash(FAX_A + PR2 + STAMP) == content_hash(FAX_B + PR2) == content_hash(once)
    assert content_hash(PR2) != content_hash(OTHER_PR2)


def test_shingles_and_jaccard():
    assert shingles("a b c d") == {("a", "b", "c"), ("b", "c", "d")}
    assert shingles("a b") == {("a", "b")} and shingles("") == set()
    assert jaccard(set(), {("a",)}) == 0.0
    assert jaccard({1, 2, 3}, {2, 3, 4}) == pytest.approx(0.5)


# ---------- pairing ----------

def test_identical_text_with_different_fax_headers_is_exact():
    rows = find_duplicates([
        rec("r1", FAX_A + PR2 + STAMP, doc="docA", created="2026-01-01T00:00:00+00:00"),
        rec("r2", FAX_B + PR2, doc="docB", created="2026-01-02T00:00:00+00:00", rtype="office_visit"),
    ])
    by_id = {r["record_id"]: r for r in rows}
    assert set(by_id) == {"r1", "r2"}
    assert by_id["r1"] == {"record_id": "r1", "group_id": "r1", "is_canonical": 1, "reason": "exact", "score": 1.0}
    assert by_id["r2"] == {"record_id": "r2", "group_id": "r1", "is_canonical": 0, "reason": "exact", "score": 1.0}


def test_text_with_a_few_transcription_differences_is_near():
    noisy = with_typos(PR2, {"swelling": "sweling", "antalgic": "antalgie", "walking boot": "walking bo0t",
                             "radiographs": "radiograhps"})
    rows = confirmed([rec("r1", FAX_A + PR2), rec("r2", FAX_B + noisy, doc="docB")])
    by_id = {r["record_id"]: r for r in rows}
    assert set(by_id) == {"r1", "r2"}
    assert by_id["r2"]["reason"] == "similar_confirmed" and by_id["r2"]["group_id"] == "r1"
    assert dedupe.NEAR_THRESHOLD <= by_id["r2"]["score"] < 1.0
    assert by_id["r1"] == {"record_id": "r1", "group_id": "r1", "is_canonical": 1, "reason": "similar_confirmed", "score": 1.0}


def test_different_records_with_same_date_and_type_are_not_duplicates():
    assert find_duplicates([rec("r1", FAX_A + PR2), rec("r2", FAX_B + OTHER_PR2, doc="docB")]) == []
    # shared letterhead and form boilerplate is far below the threshold
    assert jaccard(shingles(normalize_text(PR2)), shingles(normalize_text(OTHER_PR2))) < 0.3


def test_near_candidates_need_same_type_and_known_equal_dates():
    noisy = with_typos(PR2, {"swelling": "sweling", "antalgic": "antalgie"})
    # different dates: never candidates, even with nearly identical text
    assert find_duplicates([rec("r1", PR2, date="2025-10-30"), rec("r2", noisy, date="2025-11-13")]) == []
    # unknown date: never candidates
    assert confirmed([rec("r1", PR2, date="2025-10-30"), rec("r2", noisy, date=None)]) == []
    # a user-edited date in M/D/YYYY form compares as the same day
    assert len(confirmed([rec("r1", PR2, date="2025-10-30"), rec("r2", noisy, date="10/30/2025")])) == 2
    # different types: never near candidates ...
    assert find_duplicates([rec("r1", PR2), rec("r2", noisy, rtype="office_visit")]) == []
    # ... but identical text is exact regardless of type
    assert len(find_duplicates([rec("r1", PR2), rec("r2", PR2, rtype="office_visit")])) == 2


def test_canonical_is_most_pages_then_earliest_doc_then_start_page():
    rows = find_duplicates([
        rec("short_a", PR2, doc="docA", created="2026-01-01T00:00:00+00:00", start=50, end=51),
        rec("long_b", PR2, doc="docB", created="2026-01-02T00:00:00+00:00", start=1, end=3),
        rec("short_b", PR2, doc="docB", created="2026-01-02T00:00:00+00:00", start=7, end=8),
    ])
    by_id = {r["record_id"]: r for r in rows}
    assert set(by_id) == {"short_a", "long_b", "short_b"}
    assert by_id["long_b"]["is_canonical"] == 1
    assert {r["group_id"] for r in rows} == {"long_b"}
    assert sum(r["is_canonical"] for r in rows) == 1

    # same page count: earliest document wins, then the lower start page inside it
    rows = find_duplicates([
        rec("b1", PR2, doc="docB", created="2026-01-02T00:00:00+00:00", start=1, end=2),
        rec("a9", PR2, doc="docA", created="2026-01-01T00:00:00+00:00", start=9, end=10),
        rec("a3", PR2, doc="docA", created="2026-01-01T00:00:00+00:00", start=3, end=4),
    ])
    assert [r["record_id"] for r in rows if r["is_canonical"]] == ["a3"]
    assert {r["group_id"] for r in rows} == {"a3"}


def test_blank_or_tiny_records_never_pair():
    assert find_duplicates([rec("r1", ""), rec("r2", "", doc="docB")]) == []
    assert find_duplicates([rec("r1", "fax cover sheet"), rec("r2", "fax cover sheet", doc="docB")]) == []
    assert find_duplicates([rec("r1", FAX_A), rec("r2", FAX_B, doc="docB")]) == []


def test_near_pairs_do_not_chain_into_one_group():
    a = rec("a", " ".join(f"token{n}" for n in range(100)))
    b = rec("b", " ".join(f"token{n}" for n in range(10, 110)))
    c = rec("c", " ".join(f"token{n}" for n in range(20, 120)))
    assert dedupe.candidate_score(a, b) >= 0.8
    assert dedupe.candidate_score(b, c) >= 0.8
    assert dedupe.candidate_score(a, c) < 0.8
    rows = confirmed([a, b, c])
    assert {r["record_id"] for r in rows} == {"a", "b"}
    assert {r["group_id"] for r in rows} == {"a"}


def test_rows_are_sorted_and_input_duplicates_ignored():
    rows = find_duplicates([rec("r2", PR2, doc="docB", created="2026-01-02T00:00:00+00:00"),
                            rec("r1", PR2), rec("r1", PR2)])
    assert [(r["record_id"], r["is_canonical"]) for r in rows] == [("r1", 1), ("r2", 0)]


# ---------- dedupe_all against a temporary database ----------

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    previous = getattr(db._local, "conn", None)
    db._local.conn = None
    db.init_db()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
    db._local.conn = previous


def seed_document(doc_id: str, created_at: str, records: list[tuple[int, int, list[str], dict]]) -> list[str]:
    """records: (start_page, end_page, page texts, summary fields). Returns record ids."""
    page_count = max(end for _, end, _, _ in records)
    db.upsert_document(doc_id, f"{doc_id}.pdf", page_count)
    db.set_document(doc_id, status="done", created_at=created_at)
    spans = []
    for start, end, texts, _ in records:
        for page_no, text in zip(range(start, end + 1), texts):
            db.upsert_page(doc_id, page_no, f"{doc_id}/{page_no:04d}.jpg")
            db.save_page_result(doc_id, page_no, {}, text, content_hash(text), 1)
        spans.append({"id": record_id_for(doc_id, start, end), "start_page": start, "end_page": end,
                      "boundary_reason": "test", "partial_start": 0})
    db.sync_records(doc_id, spans)
    for span, (_, _, texts, summary) in zip(spans, records):
        fields = {"record_type": "progress_report", "title": "PR-2", "date_of_service": "2025-10-30",
                  "date_confidence": "exact", "provider": None, "facility": None, "summary": "s",
                  "key_findings": [], "flags": [], **summary}
        db.save_record_summary(span["id"], fields, content_hash("\n".join(texts)))
    return [s["id"] for s in spans]


def split_pages(text: str) -> list[str]:
    lines = text.splitlines()
    half = len(lines) // 2
    return ["\n".join(lines[:half]), "\n".join(lines[half:])]


def test_dedupe_all_uses_page_text_overrides_and_hidden(temp_db, monkeypatch):
    monkeypatch.setattr(dedupe, "confirm_pair", approve)
    noisy_other = with_typos(OTHER_PR2, {"clicking": "clickng", "tuberosity": "tuberosty", "Ibuprofen": "Ibuprofn"})
    r1, r3 = seed_document("docA", "2026-01-01T00:00:00+00:00", [
        (1, 2, split_pages(FAX_A + PR2 + STAMP), {}),
        (3, 4, split_pages(FAX_A + OTHER_PR2), {"record_type": "office_visit"}),
    ])
    r2, r4 = seed_document("docB", "2026-01-02T00:00:00+00:00", [
        (1, 2, split_pages(FAX_B + PR2), {}),
        (3, 4, split_pages(FAX_B + noisy_other), {"record_type": "office_visit"}),
    ])
    db.upsert_override(r4, {"hidden": 1})

    asyncio.run(dedupe.dedupe_all())
    groups = db.get_duplicate_groups()
    assert set(groups) == {r1, r2}, "hidden r4 and its partner r3 must stay out"
    assert groups[r1]["is_canonical"] == 1 and groups[r1]["reason"] == "exact"
    assert groups[r2] == {"record_id": r2, "group_id": r1, "is_canonical": 0, "reason": "exact", "score": 1.0}

    # unhide r4 but give it an edited date: the effective date differs, so r3 and r4 are not candidates
    db.upsert_override(r4, {"hidden": 0, "date_of_service": "2024-01-01"})
    asyncio.run(dedupe.dedupe_all())
    assert set(db.get_duplicate_groups()) == {r1, r2}

    # reset the edit: r3 and r4 pair as similar, canonical in the earlier document
    db.delete_override(r4)
    asyncio.run(dedupe.dedupe_all())
    groups = db.get_duplicate_groups()
    assert set(groups) == {r1, r2, r3, r4}
    assert groups[r3]["is_canonical"] == 1 and groups[r4]["group_id"] == r3
    assert groups[r4]["reason"] == "similar_confirmed" and 0.7 <= groups[r4]["score"] < 1.0

    # an edited type on one side breaks the near candidacy (exact pairs are unaffected)
    db.upsert_override(r4, {"record_type": "consultation"})
    asyncio.run(dedupe.dedupe_all())
    assert set(db.get_duplicate_groups()) == {r1, r2}


# ---------- staleness repair: these pin why the worker and the API must re-run dedupe_all ----------

def visible_ids() -> list[str]:
    """Record ids the default /api/timeline filters keep: done, not hidden, and either ungrouped
    or the canonical of its group (same rule as api.timeline, read straight from the tables)."""
    overrides, groups = db.get_overrides(), db.get_duplicate_groups()
    return [r["id"] for r in db.all_done_records()
            if not (overrides.get(r["id"]) or {}).get("hidden")
            and (r["id"] not in groups or groups[r["id"]]["is_canonical"])]


def test_dedupe_all_repairs_group_whose_canonical_was_regrouped(temp_db):
    """Reviewer finding: a re-group deletes the canonical record, db.sync_records drops only that
    row, and the surviving copy keeps is_canonical=0 pointing at a dead id, so the default timeline
    hides it. dedupe_all right after group_document (and on the failed-summarize path) repairs it."""
    pages = split_pages(FAX_A + PR2 + STAMP)
    a1, = seed_document("docA", "2026-01-01T00:00:00+00:00", [(1, 2, pages, {})])
    b1, = seed_document("docB", "2026-01-02T00:00:00+00:00", [(1, 2, split_pages(FAX_B + PR2), {})])
    asyncio.run(dedupe.dedupe_all())
    groups = db.get_duplicate_groups()
    assert groups[a1]["is_canonical"] == 1 and groups[b1]["group_id"] == a1
    assert visible_ids() == [a1]

    # boundaries of docA change on a retry: the 2-page record becomes two 1-page records (pending)
    db.sync_records("docA", [
        {"id": record_id_for("docA", 1, 1), "start_page": 1, "end_page": 1, "boundary_reason": "t", "partial_start": 0},
        {"id": record_id_for("docA", 2, 2), "start_page": 2, "end_page": 2, "boundary_reason": "t", "partial_start": 0},
    ])
    stale = db.get_duplicate_groups()
    assert set(stale) == {b1} and stale[b1]["group_id"] == a1 and stale[b1]["is_canonical"] == 0
    assert visible_ids() == [], "the stale state hides the only surviving copy (the defect)"

    # the repair: the rebuild ignores pending records, so b1 is ungrouped and visible again
    asyncio.run(dedupe.dedupe_all())
    assert db.get_duplicate_groups() == {}
    assert visible_ids() == [b1]

    # once docA's new records are summarized, a rebuild forms the groups over the new spans
    for start in (1, 2):
        db.save_record_summary(record_id_for("docA", start, start), {
            "record_type": "progress_report", "title": "PR-2", "date_of_service": "2025-10-30",
            "date_confidence": "exact", "summary": "s", "key_findings": [], "flags": []}, "h")
    asyncio.run(dedupe.dedupe_all())
    assert db.get_duplicate_groups() == {}, "1-page halves are not duplicates of the 2-page record"


def test_dedupe_all_after_hiding_canonical_promotes_the_other_copy(temp_db):
    """Reviewer finding: hiding the canonical card left the other copy with is_canonical=0, so both
    vanished until an unrelated document finished. A rebuild after the override write fixes it."""
    a1, = seed_document("docA", "2026-01-01T00:00:00+00:00", [(1, 2, split_pages(FAX_A + PR2), {})])
    b1, = seed_document("docB", "2026-01-02T00:00:00+00:00", [(1, 2, split_pages(FAX_B + PR2), {})])
    asyncio.run(dedupe.dedupe_all())
    assert visible_ids() == [a1]

    before = db.get_override(a1)
    db.upsert_override(a1, {"hidden": 1})      # exactly what PATCH {hidden: true} does
    assert visible_ids() == [], "stale groups: the hidden canonical takes its copy down with it"
    assert dedupe.pairing_changed(before, db.get_override(a1))

    asyncio.run(dedupe.dedupe_all())
    assert db.get_duplicate_groups() == {}     # a group of one is no group
    assert visible_ids() == [b1]

    before = db.get_override(a1)
    db.delete_override(a1)                     # reset to the model version
    assert dedupe.pairing_changed(before, None)
    asyncio.run(dedupe.dedupe_all())
    groups = db.get_duplicate_groups()
    assert groups[a1]["is_canonical"] == 1 and groups[b1]["group_id"] == a1
    assert visible_ids() == [a1]


def test_pairing_changed_only_for_date_type_and_hidden():
    assert not dedupe.pairing_changed(None, None)
    assert not dedupe.pairing_changed(None, {"title": "x", "summary": "y", "provider": "p"})
    assert not dedupe.pairing_changed({"hidden": 0, "date_of_service": None}, None)
    assert not dedupe.pairing_changed({"hidden": 0, "record_type": ""}, {"hidden": None, "record_type": None})
    assert dedupe.pairing_changed(None, {"hidden": 1})
    assert dedupe.pairing_changed({"hidden": 1}, {"hidden": 0})
    assert dedupe.pairing_changed(None, {"date_of_service": "2025-10-30"})
    assert dedupe.pairing_changed({"date_of_service": "2025-10-30"}, {"date_of_service": None})
    assert dedupe.pairing_changed({"record_type": "office_visit"}, {"record_type": "consultation"})
    assert not dedupe.pairing_changed({"record_type": "office_visit", "title": "a"},
                                      {"record_type": "office_visit", "title": "b"})


def test_dedupe_all_async_runs_off_the_loop_and_concurrent_calls_serialize(temp_db):
    import asyncio
    a1, = seed_document("docA", "2026-01-01T00:00:00+00:00", [(1, 2, split_pages(FAX_A + PR2), {})])
    b1, = seed_document("docB", "2026-01-02T00:00:00+00:00", [(1, 2, split_pages(FAX_B + PR2), {})])

    async def both():
        await asyncio.gather(dedupe.dedupe_all_async(), dedupe.dedupe_all_async())

    asyncio.run(both())
    groups = db.get_duplicate_groups()
    assert groups[a1]["is_canonical"] == 1 and groups[b1]["group_id"] == a1
