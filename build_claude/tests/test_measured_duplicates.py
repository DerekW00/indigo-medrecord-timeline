"""Synthetic regression fixtures for measured duplicate rules; no private source text."""
from app.pipeline import dedupe


def record(rid, hashes, text="", **extra):
    return {"id": rid, "doc_id": rid, "start_page": 1, "end_page": len(hashes),
            "text": text, "image_hashes": hashes, "date_of_service": "2025-01-01",
            "record_type": "office_visit", **extra}


def test_complete_image_subset_wins_despite_conflicting_text_and_dates():
    rows = dedupe.find_duplicates([
        record("long", ["a", "b", "c", "d"]),
        record("short", ["a", "b"], "conflicting OCR", date_of_service="2025-02-02")], exact_only=True)
    assert len(rows) == 2
    assert all(r["group_id"] == "long" and r["reason"] == "partial_exact" for r in rows)
    assert next(r for r in rows if r["record_id"] == "long")["is_canonical"] == 1


def test_image_containment_counts_repeated_hashes_and_requires_all_pages():
    for hashes in (["a", "a"], ["a", None], [], ["a", ""]):
        assert dedupe.find_duplicates([record("long", ["a", "b", "c"]),
                                       record("bad", hashes)], exact_only=True) == []
    bad = record("bad", ["a"])
    bad["end_page"] = 2
    assert dedupe.find_duplicates([record("long", ["a", "b", "c"]), bad], exact_only=True) == []


def test_shared_subset_does_not_join_complementary_records():
    rows = dedupe.find_duplicates([record("a", ["x", "y"]), record("b", ["x", "z"]),
                                  record("small", ["x"])], exact_only=True)
    assert {r["record_id"] for r in rows} == {"a", "small"}


def test_missing_empty_and_unreadable_images_cannot_prove_match(tmp_path):
    assert dedupe.image_hash(str(tmp_path / "missing")) is None
    assert dedupe.image_hash(str(tmp_path)) is None
    empty = tmp_path / "empty.jpg"
    empty.touch()
    assert dedupe.image_hash(str(empty)) is None
    empty.write_bytes(b"synthetic image bytes")
    assert dedupe.image_hash(str(empty))
