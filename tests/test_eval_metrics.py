"""Synthetic cases for eval/metrics.py (no database, no model)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import metrics as M  # noqa: E402


def gold(records, labeled=None):
    obj = {"filename": "x.pdf", "records": records}
    if labeled:
        obj["labeled_pages"] = labeled
    return obj


def g(id, s, e, date=None, rtype=None, **kw):
    return {"id": id, "start_page": s, "end_page": e, "date_of_service": date, "record_type": rtype, **kw}


def p(id, s, e, date=None, rtype=None, reason=""):
    return M.PredRecord(id=id, start_page=s, end_page=e, date_of_service=date, record_type=rtype,
                        boundary_reason=reason)


def test_perfect_prediction_scores_one_everywhere():
    gd = gold([
        g("r01", 1, 4, "2025-12-29", "permanent_stationary_report",
          accepted_types=["permanent_stationary_report", "progress_report"]),
        g("r02", 5, 9, "2025-10-30", "progress_report"),
        g("r03", 10, 12, "2025-09-15", "physical_therapy"),
    ], labeled=[1, 12])
    preds = [p("a", 1, 4, "2025-12-29", "progress_report"), p("b", 5, 9, "2025-10-30", "progress_report"),
             p("c", 10, 12, "2025-09-15", "physical_therapy")]
    res = M.evaluate(gd, preds, {})
    assert (res["starts"]["precision"], res["starts"]["recall"], res["starts"]["f1"]) == (1.0, 1.0, 1.0)
    assert res["spans"]["rate"] == 1.0 and res["spans"]["exact"] == 3
    assert res["pages"] == {"accuracy": 1.0, "hits": 12, "total": 12, "uncovered_pages": 0, "miss_ranges": []}
    assert res["fields"]["date_accuracy"] == 1.0
    assert res["fields"]["type_accuracy"] == 1.0  # progress_report is in accepted_types of r01
    assert res["duplicates"] == {"gold_pairs": 0, "hits": 0, "hit_rate": None, "false_pairs": 0,
                                 "false_pair_list": [], "pairs": [], "warnings": []}
    assert res["unmatched_pred"] == []


def test_allow_internal_splits_tiling():
    gd = gold([
        g("r01", 1, 4, "2025-01-01", "progress_report"),
        g("r02", 5, 20, "2024-10-02", "hospital_record", allow_internal_splits=True,
          accepted_types=["hospital_record", "operative_report"]),
        g("r03", 21, 25, "2024-09-30", "office_visit"),
    ], labeled=[1, 25])
    tiled = [p("a", 1, 4, "2025-01-01", "progress_report"),
             p("b", 5, 10, "2024-10-02", "hospital_record"), p("c", 11, 15, "2024-10-03", "operative_report"),
             p("d", 16, 20, "2024-10-05", "lab_result"), p("e", 21, 25, "2024-09-30", "office_visit")]
    res = M.evaluate(gd, tiled, {})
    assert res["starts"]["f1"] == 1.0 and res["starts"]["dropped_internal"] == 2
    assert res["spans"] ["rate"] == 1.0 and res["spans"]["tiled"] == 1 and res["spans"]["exact"] == 2
    assert res["pages"]["accuracy"] == 1.0
    assert res["fields"]["date_accuracy"] == 1.0 and res["fields"]["type_accuracy"] == 1.0  # head piece b

    # A span that straddles the gold boundary is not a tiling and its pages are wrong.
    straddle = [p("a", 1, 6), p("b", 7, 20), p("c", 21, 25)]
    res = M.evaluate(gd, straddle, {})
    assert (res["starts"]["tp"], res["starts"]["fp"], res["starts"]["fn"]) == (2, 0, 1)  # start 7 ignored
    assert res["starts"]["recall"] == pytest.approx(2 / 3, abs=1e-4) and res["starts"]["f1"] == 0.8
    assert res["spans"]["matched"] == 1 and [u["gold_id"] for u in res["spans"]["unmatched"]] == ["r01", "r02"]
    assert res["pages"]["hits"] == 19 and res["pages"]["miss_ranges"] == [[1, 6]]


def test_bundle_starts_mid_record_and_labeled_window_clips_predictions():
    # Page 1 is fax page 2/6 of a record the model wrongly cut after page 1; the human labeled
    # pages 1-10 only, and the pipeline produced spans beyond that window.
    gd = gold([g("r01", 1, 3, "2025-12-29", "permanent_stationary_report", note="starts at fax page 2/6"),
               g("r02", 4, 10, "2025-10-30", "progress_report")], labeled=[1, 10])
    preds = [p("a", 1, 1, reason="first page"), p("b", 2, 3, reason="new letterhead or form title (low)"),
             p("c", 4, 12, "2025-10-30", "progress_report", reason="fax restart 1/N"), p("d", 13, 15)]
    res = M.evaluate(gd, preds, {})
    assert res["n_pred"] == 3  # span d lies outside the window
    assert (res["starts"]["tp"], res["starts"]["fp"], res["starts"]["fn"]) == (2, 1, 0)
    assert res["starts"]["precision"] == pytest.approx(2 / 3, abs=1e-4) and res["starts"]["recall"] == 1.0
    assert res["starts"]["false_starts"] == [{"page": 2, "pred_id": "b",
                                              "boundary_reason": "new letterhead or form title (low)"}]
    assert res["spans"]["rate"] == 0.5  # r02 matches after c is clipped to 4-10
    assert res["pages"]["accuracy"] == 0.7 and res["pages"]["miss_ranges"] == [[1, 3]]
    assert [u["id"] for u in res["unmatched_pred"]] == ["a", "b"]


def test_date_and_type_accuracy_use_accepted_types_and_normalized_dates():
    gd = gold([g("r01", 1, 2, "2024-10-02", "hospital_record", accepted_types=["hospital_record", "operative_report"]),
               g("r02", 3, 4, "2024-10-05", "consultation"),
               g("r03", 5, 6, None, "other")])
    preds = [p("a", 1, 2, "2024-10-02", "operative_report"),  # date hit, type hit via accepted_types
             p("b", 3, 4, "10/05/2024", "progress_report"),   # date hit after normalization, type miss
             p("c", 5, 6, "2024-10-09", "other")]             # gold is undated: date miss, type hit
    res = M.evaluate(gd, preds, {})
    f = res["fields"]
    assert f["n_matched"] == 3
    assert f["date_accuracy"] == pytest.approx(2 / 3, abs=1e-4) and f["type_accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert [r["date_hit"] for r in f["rows"]] == [True, True, False]
    assert [r["type_hit"] for r in f["rows"]] == [True, False, True]


def test_duplicate_pairs_closure_hits_false_and_unmapped():
    gd = gold([g("r01", 1, 2), g("r02", 3, 4, duplicate_of="r01", duplicate_kind="near"),
               g("r03", 5, 6), g("r04", 7, 8, duplicate_of="r03", duplicate_kind="exact"),
               g("r05", 9, 10), g("r06", 11, 12, duplicate_of="r01", duplicate_kind="partial")])
    preds = [p("a", 1, 2), p("b", 3, 4), p("c", 5, 6), p("d", 7, 8), p("e", 9, 10), p("f", 11, 12)]
    groups = {"a": "g1", "b": "g1", "f": "g1", "c": "g2", "e": "g2"}
    d = M.evaluate(gd, preds, groups)["duplicates"]
    assert d["gold_pairs"] == 4 and d["hits"] == 3 and d["hit_rate"] == 0.75
    assert d["false_pairs"] == 1 and d["false_pair_list"] == [["r03", "r05"]]
    by_pair = {tuple(r["pair"]): r for r in d["pairs"]}
    assert by_pair[("r02", "r06")]["kind"] == "transitive" and by_pair[("r02", "r06")]["status"] == "hit"
    assert by_pair[("r03", "r04")] == {"pair": ["r03", "r04"], "kind": "exact", "status": "miss"}

    # A boundary error on r04 makes its pair impossible: reported as unmapped, not as a miss.
    preds2 = preds[:3] + [p("d1", 7, 7), p("d2", 8, 8)] + preds[4:]
    d2 = M.evaluate(gd, preds2, {"d1": "g3", "c": "g3"})["duplicates"]
    by_pair = {tuple(r["pair"]): r for r in d2["pairs"]}
    assert by_pair[("r03", "r04")]["status"] == "unmapped" and d2["false_pairs"] == 0


def test_date_rule_check_normalizes_every_written_form():
    transcription = ("10/30/2025 3:17:27 PM PDT PAGE 2/005 Fax Server\nDec/29/2025 2:01:57 PM Kaiser 2/6\n"
                     "Visit date: 10/30/2025. Next visit Nov 13, 2025. Date of injury 1/5/24. DOB 1/5/49\n"
                     "BP 120/80, pain 5/10, Pain: 2.5 / 10, VAS 7.5 out of 10, ROM 3.5-40 deg, "
                     "dose 2.5/12.5 mg. DWC Form PR-2 (10/2015) Page 2")
    assert [iso for _, iso in M.extract_dates(transcription)] == [
        "2025-10-30", "2025-12-29", "2025-10-30", "2025-11-13", "2024-01-05", "1949-01-05"]
    fields = {"date_of_service": "2025-10-30",
              "summary": "Seen on October 30, 2025 for follow-up; return on 2025-11-13. Injured 2024-01-05. "
                         "An MRI was done on 11/02/2025 and reviewed December 29th, 2025. "
                         "Pain rated 2.5/10, VAS 7.5/10, ROM 3.5-40 deg; born January 5, 1949.",
              "key_findings": ["Follow-up 11/13/25", "MRI 2 November 2025", "VAS 7.5/10", "dose 2.5/12.5 mg"]}
    violations = M.date_rule_check(fields, transcription)
    assert violations == [{"field": "summary", "text": "11/02/2025", "date": "2025-11-02"},
                          {"field": "key_findings", "text": "2 November 2025", "date": "2025-11-02"}]
    assert M.normalize_date("3/15/70") == "1970-03-15" and M.normalize_date(" 2024-10-02 ") == "2024-10-02"
    assert M.normalize_date(None) is None and M.normalize_date("unknown") == "unknown"


def test_extract_dates_ignores_fractions_decimals_and_mixed_separators():
    for text in ["pain 2.5/10", "VAS 7.5/10", "ROM 3.5-40 deg", "dose 2.5/12.5 mg", "BP 120/80",
                 "10.30.2025", "2025-10/30", "1/5-1/12", "98.6 F", "PR-2 (10/2015)"]:
        assert M.extract_dates(text) == [], text
    assert M.extract_dates("10-30-2025 and 10/30/25 and 1/5/49 and 2024-10-02") == [
        ("10-30-2025", "2025-10-30"), ("10/30/25", "2025-10-30"), ("1/5/49", "1949-01-05"),
        ("2024-10-02", "2024-10-02")]


def test_parse_gold_rejects_overlaps_and_duplicate_ids():
    with pytest.raises(ValueError):
        M.parse_gold(gold([g("r01", 1, 5), g("r02", 5, 8)]))
    with pytest.raises(ValueError):
        M.parse_gold(gold([g("r01", 1, 5), g("r01", 6, 8)]))
    (lo, hi), golds = M.parse_gold(gold([g("r02", 6, 8), g("r01", 1, 5)]))
    assert (lo, hi) == (1, 8) and [x.id for x in golds] == ["r01", "r02"]
