"""Unit tests for the pure grouping rules (CLAUDE.md section 7) and group_document."""
from __future__ import annotations

import pytest

from app import config, db
from app.models import FaxPage, PageSignals, record_id_for
from app.pipeline.group import FIRST_PAGE_REASON, fax_id, group_document, group_pages

FAX_A = "10/30/2025 3:17:27 PM PDT"
FAX_B = "11/14/2025 9:05:11 AM PST"
KAISER = "Dec/29/2025 2:01:57 PM Kaiser"


def pg(new=False, conf="medium", fax=None, k=None, n=None, label=None, label_n=None,
       dtype="progress_report", date=None, cover=False, header=None, text="") -> PageSignals:
    """Build PageSignals with only the fields the grouping rules read.
    `fax` is a transmission stamp; with k and n it becomes a "PAGE k/nnn" style header."""
    if header is None and fax is not None:
        header = f"{fax} PAGE {k}/{n:03d} Fax Server" if k is not None and n is not None else fax
    return PageSignals(
        transcription=text,
        fax_header=header,
        fax_page=FaxPage(k=k, n=n) if k is not None else None,
        page_label=FaxPage(k=label, n=label_n) if label is not None else None,
        starts_new_record=new,
        boundary_confidence=conf,
        doc_type=dtype,
        date_of_service=date,
        gist="test page",
        is_blank_or_cover=cover,
    )


def kaiser(k, n=6, **kw) -> PageSignals:
    """A Kaiser-style header: 'Dec/29/2025 2:01:57 PM Kaiser 2/6'."""
    return pg(header=f"{KAISER} {k}/{n}", k=k, n=n, **kw)


def fax(stamp, n, first_k=1, last_k=None, date=None, dtype="progress_report", new_conf="high"):
    """One clean fax transmission: a confident first page, then continuation pages."""
    last_k = n if last_k is None else last_k
    pages = [pg(new=True, conf=new_conf, fax=stamp, k=first_k, n=n, dtype=dtype, date=date)]
    pages += [pg(fax=stamp, k=i, n=n, dtype=dtype, date=date) for i in range(first_k + 1, last_k + 1)]
    return pages


def spans_of(pages) -> list[tuple[int, int]]:
    return [(s.start_page, s.end_page) for s in group_pages(pages)]


# ---------- fax_id ----------

class TestFaxId:
    def test_canonical_timestamp_key(self):
        # The same printed timestamp transcribed in different formats maps to one key.
        assert fax_id("10/30/2025 3:17:27 PM PDT PAGE 2/005 Fax Server") == "20251030-151727"
        assert fax_id("2025-10-30 3:17:27 PM PDT PAGE 3/005") == "20251030-151727"
        assert fax_id("2025-10-30T15:17:27-07:00 PAGE 4/005") == "20251030-151727"
        assert fax_id("Dec/29/2025 2:01:57 PM Kaiser 2/6") == "20251229-140157"
        assert fax_id("2025-12-29 14:01:57 3/6") == "20251229-140157"

    def test_strips_page_tokens_without_a_date(self):
        assert fax_id("KPRFDT001 Page 2 of 25") == "KPRFDT001"
        assert fax_id("KPRFDT001 page 3") == "KPRFDT001"
        assert fax_id("KPRFDT001 3 of 25") == "KPRFDT001"

    def test_tpa_stamp_does_not_change_the_key(self):
        header = "10/30/2025 3:17:27 PM PDT PAGE 2/005 Fax Server | Received by TPA ICS Fax 10/30/25"
        assert fax_id(header) == "20251030-151727"
        assert fax_id("1/2/2025 10:00 1/2") == "20250102-100000"

    def test_same_fax_fallback_when_header_text_differs(self):
        # Same fax, timestamp mangled beyond repair, but the page counter runs on with one total.
        prev = pg(fax="Fax Server A", k=3, n=5)
        cur = pg(fax="Fax Server B", k=4, n=5)
        assert len(group_pages([prev, cur])) == 1

    def test_pages_of_one_transmission_share_an_id(self):
        ids = {fax_id(f"{FAX_A} PAGE {k}/005 Fax Server") for k in range(2, 6)}
        assert len(ids) == 1
        ids = {fax_id(f"{KAISER} {k}/6") for k in range(2, 7)}
        assert len(ids) == 1

    def test_different_transmissions_differ(self):
        assert fax_id(f"{FAX_A} PAGE 2/005") != fax_id(f"{FAX_B} PAGE 2/005")

    def test_empty_or_page_only_headers_have_no_id(self):
        assert fax_id(None) is None
        assert fax_id("") is None
        assert fax_id("PAGE 2/005") is None
        assert fax_id("  2/6 ") is None


# ---------- basic shapes ----------

def test_empty_and_single_page():
    assert group_pages([]) == []
    [span] = group_pages([pg(new=True, conf="high", fax=FAX_A, k=1, n=1)])
    assert (span.start_page, span.end_page) == (1, 1)
    assert span.boundary_reason == FIRST_PAGE_REASON
    assert span.partial_start is False


def test_clean_fax_of_five_pages():
    pages = fax(FAX_A, 5, date="2025-10-30")
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 5)]
    assert spans[0].boundary_reason == FIRST_PAGE_REASON
    assert spans[0].partial_start is False


def test_two_faxes_back_to_back():
    pages = fax(FAX_A, 3, date="2025-10-30") + fax(FAX_B, 4, date="2025-11-14")
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 3), (4, 7)]
    assert spans[1].boundary_reason == "fax restart 1/4"
    assert spans[1].partial_start is False


def test_two_faxes_with_stripped_cover_pages():
    # Fax cover pages were removed before scanning: both faxes start at fax page 2 (rule 5).
    pages = fax(FAX_A, 5, first_k=2, date="2025-10-30") + fax(FAX_B, 6, first_k=2, last_k=4, date="2025-11-14")
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 4), (5, 7)]
    assert spans[1].boundary_reason.startswith("fax transmission changed")
    assert spans[1].partial_start is False  # fax page 2 with a full heading is not a partial record


def test_fax_change_takes_precedence_over_heading_signal():
    second = fax(FAX_B, 6, first_k=2, last_k=4)
    pages = fax(FAX_A, 5, first_k=2) + second
    assert group_pages(pages)[1].boundary_reason.startswith("fax transmission changed")
    # Same bundle, but the new fax starts mid-document (no heading): still a boundary, now partial.
    second[0] = pg(fax=FAX_B, k=2, n=6)
    spans = group_pages(fax(FAX_A, 5, first_k=2) + second)
    assert [(s.start_page, s.end_page) for s in spans] == [(1, 4), (5, 7)]
    assert spans[1].partial_start is True


# ---------- bundle starting at fax page 2/6 (sample page 1) ----------

def test_bundle_starts_at_fax_page_2_of_6_with_full_heading():
    pages = [kaiser(2, new=True, conf="high", dtype="permanent_stationary_report", date="2025-12-29", label=1)]
    pages += [kaiser(k, dtype="permanent_stationary_report", date="2025-12-29", label=k - 1) for k in (3, 4, 5)]
    pages += fax(FAX_A, 5, first_k=2, date="2025-10-30")
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 4), (5, 8)]
    assert spans[0].boundary_reason == FIRST_PAGE_REASON
    assert spans[0].partial_start is False
    assert spans[1].boundary_reason.startswith("fax transmission changed")


def test_bundle_starts_mid_record_is_partial():
    pages = [kaiser(2, label=2, label_n=5)] + [kaiser(k, label=k) for k in (3, 4)]
    pages += fax(FAX_A, 2)
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 3), (4, 5)]
    assert spans[0].partial_start is True
    assert spans[1].partial_start is False


# ---------- letterhead repeated on every page of one fax (rule 3) ----------

def test_letterhead_repeated_on_every_page_of_one_fax():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=k, n=6, date="2025-10-30") for k in range(2, 7)]
    assert spans_of(pages) == [(1, 5)]


def test_letterhead_repeated_with_missing_dates_or_medium_confidence():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=2, n=6, date="2025-10-30")]
    pages += [pg(new=True, conf="high", fax=FAX_A, k=k, n=6) for k in range(3, 7)]
    assert spans_of(pages) == [(1, 5)]
    pages = [pg(new=True, conf="high", fax=FAX_A, k=2, n=6, date="2025-10-30")]
    pages += [pg(new=True, conf="medium", fax=FAX_A, k=k, n=6, dtype="work_status", date="2025-11-01")
              for k in range(3, 7)]
    assert spans_of(pages) == [(1, 5)]


def test_same_type_but_new_date_inside_one_fax_splits():
    pages = fax(FAX_A, 8, first_k=2, last_k=4, date="2025-10-30")
    pages += [pg(new=True, conf="high", fax=FAX_A, k=5, n=8, date="2025-11-20")]
    pages += [pg(fax=FAX_A, k=k, n=8, date="2025-11-20") for k in (6, 7, 8)]
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 3), (4, 7)]
    assert spans[1].boundary_reason.startswith("new document inside the same fax")
    assert "date 2025-10-30 -> 2025-11-20" in spans[1].boundary_reason


# ---------- cover sheets and blank pages ----------

def test_cover_sheet_attaches_to_the_record_that_follows():
    pages = fax(FAX_A, 3, date="2025-10-30")
    pages += [pg(new=True, conf="high", fax=FAX_B, k=1, n=4, dtype="cover_sheet", cover=True)]
    pages += fax(FAX_B, 4, first_k=2, date="2025-11-14")
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 3), (4, 7)]
    assert "cover or blank page 4 attached in front" in spans[1].boundary_reason
    assert spans[1].partial_start is False


def test_cover_sheet_as_a_continuation_still_attaches_forward():
    # The walk may put a cover at the end of the previous span; it still moves forward.
    pages = fax(FAX_A, 3)
    pages += [pg(fax=FAX_A, k=4, n=4, dtype="cover_sheet", cover=True)]  # same fax, rule 3 continuation
    pages += fax(FAX_B, 2)
    assert spans_of(pages) == [(1, 3), (4, 6)]


def test_cover_sheet_on_page_one():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=1, n=3, dtype="cover_sheet", cover=True)]
    pages += fax(FAX_A, 3, first_k=2, date="2025-10-30")
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 3)]
    assert spans[0].boundary_reason.startswith(FIRST_PAGE_REASON)


def test_two_cover_pages_in_a_row():
    pages = fax(FAX_A, 2)
    pages += [pg(new=True, conf="high", fax=FAX_B, k=1, n=5, dtype="cover_sheet", cover=True),
              pg(fax=FAX_B, k=2, n=5, dtype="other", cover=True)]
    pages += fax(FAX_B, 5, first_k=3)
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 2), (3, 7)]
    assert "pages 3-4 attached in front" in spans[1].boundary_reason


def test_blank_page_inside_a_record_stays_put():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=1, n=4), pg(fax=FAX_A, k=2, n=4, dtype="other", cover=True),
             pg(fax=FAX_A, k=3, n=4), pg(fax=FAX_A, k=4, n=4)]
    assert spans_of(pages) == [(1, 4)]


def test_blank_last_page_stays_with_its_record():
    pages = fax(FAX_A, 3) + [pg(fax=FAX_A, k=4, n=4, dtype="other", cover=True)]
    assert spans_of(pages) == [(1, 4)]
    # A cover sheet that the walk made its own record at the very end joins the record before it.
    pages = fax(FAX_A, 3) + [pg(new=True, conf="high", fax=FAX_B, k=1, n=1, dtype="cover_sheet", cover=True)]
    assert spans_of(pages) == [(1, 4)]


def test_all_cover_pages_is_one_record():
    pages = [pg(new=True, conf="high", dtype="cover_sheet", cover=True), pg(dtype="other", cover=True)]
    assert spans_of(pages) == [(1, 2)]


# ---------- no fax header at all (rules 6 and 7) ----------

def test_type_and_date_change_without_fax_header():
    pages = [pg(new=True, conf="high", dtype="office_visit", date="2025-01-05"),
             pg(dtype="office_visit", date="2025-01-05"),
             pg(conf="medium", dtype="imaging_report", date="2025-01-10"),
             pg(dtype="imaging_report", date="2025-01-10")]
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 2), (3, 4)]
    assert spans[1].boundary_reason.startswith("type and date changed")


def test_type_change_alone_or_low_confidence_is_not_a_boundary():
    first = pg(new=True, conf="high", dtype="office_visit", date="2025-01-05")
    assert spans_of([first, pg(dtype="imaging_report", date="2025-01-05")]) == [(1, 2)]        # same date
    assert spans_of([first, pg(dtype="imaging_report")]) == [(1, 2)]                           # no date
    assert spans_of([first, pg(conf="low", dtype="imaging_report", date="2025-01-10")]) == [(1, 2)]  # low
    assert spans_of([first, pg(dtype="office_visit", date="2025-01-10")]) == [(1, 2)]          # same type


def test_new_heading_without_fax_header_splits_at_any_confidence():
    first = pg(new=True, conf="high", dtype="office_visit", date="2025-01-05")
    for conf in ("high", "medium", "low"):
        spans = group_pages([first, pg(new=True, conf=conf, dtype="office_visit", date="2025-01-05")])
        assert [(s.start_page, s.end_page) for s in spans] == [(1, 1), (2, 2)]
        assert spans[1].boundary_reason == f"new letterhead or form title ({conf} confidence)"


# ---------- sample pages 79 to 83: fax id changes while the page labels continue ----------

def test_fax_id_change_while_page_labels_continue_sample_79_83():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=2, n=6, label=1, date="2025-09-15"),
             pg(fax=FAX_A, k=3, n=6, label=2, date="2025-09-15"),
             pg(fax=FAX_B, k=2, n=4, label=3, date="2025-09-15"),   # new transmission, "Page 3" continues
             pg(fax=FAX_B, k=3, n=4, label=4, date="2025-09-15"),
             pg(fax=FAX_B, k=4, n=4, label=5, date="2025-09-15")]
    assert spans_of(pages) == [(1, 5)]
    # Page labels also win when the new transmission starts at fax page 1 (rule 3 before rule 4).
    pages[2] = pg(fax=FAX_B, k=1, n=3, label=3, date="2025-09-15")
    assert spans_of(pages) == [(1, 5)]


def test_without_page_labels_the_fax_change_splits():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=2, n=6, date="2025-09-15"),
             pg(fax=FAX_A, k=3, n=6, date="2025-09-15"),
             pg(fax=FAX_B, k=2, n=4, date="2025-09-15"),
             pg(fax=FAX_B, k=3, n=4, date="2025-09-15")]
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 2), (3, 4)]
    assert spans[1].boundary_reason.startswith("fax transmission changed")
    assert spans[1].partial_start is True


def test_page_labels_only_continue_within_one_doc_type():
    pages = [pg(new=True, conf="high", fax=FAX_A, k=2, n=6, label=1, dtype="progress_report"),
             pg(new=True, conf="medium", fax=FAX_B, k=2, n=4, label=2, dtype="physical_therapy")]
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 1), (2, 2)]
    assert spans[1].boundary_reason.startswith("fax transmission changed")


# ---------- sample pages 149 to 154: several documents inside one 25-page fax ----------

def test_two_documents_inside_one_fax_sample_149_154():
    stamp = "KPRFDT001 10/06/2024 08:15"
    pages = fax(FAX_A, 2, dtype="hospital_record", date="2024-10-02")           # tail of the previous fax
    pages += [
        pg(new=True, conf="high", fax=stamp, k=1, n=25, dtype="imaging_report", date="2024-10-02"),    # 149 X-ray
        pg(fax=stamp, k=2, n=25, dtype="imaging_report", date="2024-10-02"),                            # 150
        pg(new=True, conf="high", fax=stamp, k=3, n=25, dtype="operative_report", date="2024-10-02"),  # 151
        pg(fax=stamp, k=4, n=25, dtype="operative_report", date="2024-10-02"),                          # 152
        pg(new=True, conf="high", fax=stamp, k=5, n=25, dtype="hospital_record", date="2024-10-05"),   # 153 discharge
        pg(fax=stamp, k=6, n=25, dtype="hospital_record", date="2024-10-05"),                           # 154
    ]
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 2), (3, 4), (5, 6), (7, 8)]
    assert spans[1].boundary_reason == "fax restart 1/25"
    assert spans[2].boundary_reason == "new document inside the same fax (type imaging_report -> operative_report)"
    assert spans[3].boundary_reason.startswith("new document inside the same fax")
    assert not any(s.partial_start for s in spans)


def test_medium_confidence_heading_inside_a_fax_does_not_split():
    stamp = "KPRFDT001 10/06/2024 08:15"
    pages = [pg(new=True, conf="high", fax=stamp, k=1, n=25, dtype="imaging_report", date="2024-10-02"),
             pg(fax=stamp, k=2, n=25, dtype="imaging_report", date="2024-10-02"),
             pg(new=True, conf="medium", fax=stamp, k=3, n=25, dtype="operative_report", date="2024-10-02")]
    assert spans_of(pages) == [(1, 3)]


# ---------- Kaiser chart printouts with "(continued)" pages ----------

def test_chart_printout_with_continued_pages_in_one_fax():
    stamp = "Oct/06/2024 11:40:12 AM Kaiser"
    pages = [pg(new=True, conf="high", header=f"{stamp} 2/58", k=2, n=58, dtype="hospital_record", date="2024-10-02")]
    pages += [pg(conf="high", header=f"{stamp} {k}/58", k=k, n=58, dtype="hospital_record", date="2024-10-02")
              for k in range(3, 10)]
    assert spans_of(pages) == [(1, 8)]


def test_chart_printout_without_fax_header_or_labels():
    # Repeated patient header and "(continued)" titles: the model says continuation on every page.
    pages = [pg(new=True, conf="high", dtype="hospital_record", date="2024-10-02")]
    pages += [pg(conf="high", dtype="hospital_record", date="2024-10-02") for _ in range(6)]
    assert spans_of(pages) == [(1, 7)]


def test_chart_printout_page_labels_keep_internal_notes_together():
    pages = [pg(new=True, conf="high", label=1, label_n=57, dtype="hospital_record", date="2024-10-02")]
    pages += [pg(label=k, label_n=57, dtype="hospital_record", date="2024-10-02") for k in (2, 3)]
    pages += [pg(new=True, conf="high", label=4, label_n=57, dtype="hospital_record", date="2024-10-02")]
    pages += [pg(label=5, label_n=57, dtype="hospital_record", date="2024-10-02")]
    assert spans_of(pages) == [(1, 5)]


def test_chart_printout_internal_split_into_notes_is_allowed():
    pages = [pg(new=True, conf="high", dtype="hospital_record", date="2024-10-02"),
             pg(dtype="hospital_record", date="2024-10-02"),
             pg(new=True, conf="high", dtype="operative_report", date="2024-10-02"),
             pg(dtype="operative_report", date="2024-10-02")]
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 2), (3, 4)]
    assert spans[1].boundary_reason == "new letterhead or form title (high confidence)"


# ---------- partial_start ----------

def test_partial_start_cases():
    # page label k > 1 on the first page
    assert group_pages([pg(label=2, label_n=5), pg(label=3, label_n=5)])[0].partial_start is True
    # fax page k > 1 and no new heading
    assert group_pages([pg(fax=FAX_A, k=3, n=5), pg(fax=FAX_A, k=4, n=5)])[0].partial_start is True
    # fax page 2 with a full heading: a stripped cover page, not a partial record
    assert group_pages([pg(new=True, conf="high", fax=FAX_A, k=2, n=5), pg(fax=FAX_A, k=3, n=5)])[0].partial_start is False
    # the literal section 7 rule: fax page k > 1 without a heading is partial, even with page label 1
    assert group_pages([pg(fax=FAX_A, k=3, n=5, label=1), pg(fax=FAX_A, k=4, n=5, label=2)])[0].partial_start is True
    # a confident heading on fax page 3 is a record start, not a partial record
    assert group_pages([pg(new=True, conf="high", fax=FAX_A, k=3, n=5), pg(fax=FAX_A, k=4, n=5)])[0].partial_start is False
    # a cover sheet in front does not hide a partial first content page
    pages = [pg(new=True, conf="high", fax=FAX_A, k=1, n=5, dtype="cover_sheet", cover=True),
             pg(fax=FAX_A, k=2, n=5, label=3, label_n=6)]
    assert group_pages(pages)[0].partial_start is True


def test_partial_start_of_a_middle_record():
    pages = fax(FAX_A, 3) + [pg(fax=FAX_B, k=3, n=6), pg(fax=FAX_B, k=4, n=6)] + fax(FAX_A.replace("30", "31"), 2)
    spans = group_pages(pages)
    assert spans_of(pages) == [(1, 3), (4, 5), (6, 7)]
    assert [s.partial_start for s in spans] == [False, True, False]


# ---------- group_document against SQLite ----------

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    db._local.conn = None
    db.init_db()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
    db._local.conn = None


def _store(doc_id: str, signals: list[PageSignals], failed: tuple[int, ...] = ()) -> None:
    db.upsert_document(doc_id, "x.pdf", len(signals))
    for page_no, sig in enumerate(signals, start=1):
        db.upsert_page(doc_id, page_no, f"pages/{page_no:04d}.jpg")
        if page_no in failed:
            db.save_page_failure(doc_id, page_no, "injected failure", 3)
        else:
            db.save_page_result(doc_id, page_no, sig.model_dump(), sig.transcription, "hash", 1)


def test_group_document_writes_records_with_deterministic_ids(temp_db):
    doc = "d" * 64
    _store(doc, fax(FAX_A, 3, date="2025-10-30") + fax(FAX_B, 2, date="2025-11-14"))
    rows = group_document(doc)
    assert [(r["start_page"], r["end_page"]) for r in rows] == [(1, 3), (4, 5)]
    assert rows[0]["id"] == record_id_for(doc, 1, 3)
    assert rows[1]["boundary_reason"] == "fax restart 1/2"
    stored = db.get_records(doc)
    assert [r["id"] for r in stored] == [r["id"] for r in rows]
    assert all(r["status"] == "pending" for r in stored)
    assert stored[0]["partial_start"] == 0

    # A second run keeps the ids, so summaries and user overrides survive a re-group.
    db.save_record_summary(rows[0]["id"], {
        "record_type": "progress_report", "title": "PR-2", "date_of_service": "2025-10-30",
        "date_confidence": "exact", "provider": None, "facility": None, "summary": "s",
        "key_findings": [], "flags": []}, "h")
    assert [r["id"] for r in group_document(doc)] == [r["id"] for r in rows]
    assert db.get_record(rows[0]["id"])["status"] == "done"


def test_group_document_failed_page_is_a_neutral_continuation(temp_db):
    doc = "e" * 64
    _store(doc, fax(FAX_A, 5, date="2025-10-30"), failed=(3,))
    rows = group_document(doc)
    assert [(r["start_page"], r["end_page"]) for r in rows] == [(1, 5)]
    assert db.get_pages(doc)[2]["status"] == "failed"


def test_group_document_replaces_stale_records(temp_db):
    doc = "f" * 64
    _store(doc, fax(FAX_A, 4))
    old = group_document(doc)
    assert [(r["start_page"], r["end_page"]) for r in old] == [(1, 4)]
    # Re-extraction now says a second fax starts at page 3: the old span is gone, two new ones exist.
    for page_no, sig in enumerate(fax(FAX_A, 2) + fax(FAX_B, 2), start=1):
        db.save_page_result(doc, page_no, sig.model_dump(), "", "hash", 1)
    new = group_document(doc)
    assert [(r["start_page"], r["end_page"]) for r in new] == [(1, 2), (3, 4)]
    assert {r["id"] for r in db.get_records(doc)} == {r["id"] for r in new}
    assert db.get_record(old[0]["id"]) is None
