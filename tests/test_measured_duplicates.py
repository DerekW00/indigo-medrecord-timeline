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

import asyncio
import sqlite3
import threading

import pytest

from app import config, db
from scripts.check_measured_duplicates import copy_store


def decision(a, b, **changes):
    return dedupe.DuplicateConfirmation(**{
        "relation": "duplicate", "confidence": "high", "reason": "synthetic identical clinical content",
        "a_pages": list(range(a["start_page"], a["end_page"] + 1)),
        "b_pages": list(range(b["start_page"], b["end_page"] + 1)), "unique_content": "", **changes})


def near_pair():
    shared = " ".join(f"clinical{n}" for n in range(100))
    return record("a", [None], shared + " stable"), record("b", [None], shared + " stabIe")


def test_clinical_tokens_keep_signs_decimals_negation_and_unidentified_lines():
    tokens = dedupe.clinical_tokens("No fever\n-2.5 +3 −4 .5 120/80\n< ≤ ≥ > % ° =\n1/5")
    assert {"no", "fever", "-2.5", "+3", "−4", ".5", "120/80", "<", "≤", "≥", ">", "%", "°", "=", "1/5"} <= tokens
    assert dedupe.clinical_tokens("FAX discussion: no fever") >= {"fax", "no", "fever"}
    assert dedupe.clinical_tokens("10/30/2025 3:17:27 PM PDT PAGE 2/005 Fax Server\nNo fever") == {"no", "fever"}
    assert dedupe.jaccard(set(), set()) == 0


def test_sign_conflict_is_never_punctuation_normalized_exact():
    text = "The patient examination today records stable strength and measurement "
    a, b = record("a", [None], text + "-2.5"), record("b", [None], text + "+2.5")
    assert dedupe.content_hash(a["text"]) == dedupe.content_hash(b["text"])
    assert dedupe.find_duplicates([a, b]) == []


def test_similarity_alone_never_collapses_even_high_boilerplate_overlap():
    a, b = near_pair()
    assert dedupe.candidate_score(a, b) >= .96
    assert dedupe.find_duplicates([a, b]) == []
    assert len(dedupe.find_duplicates([a, b], confirmations={("a", "b"): decision(a, b)})) == 2


@pytest.mark.parametrize("changes", [
    {"relation": "different"}, {"relation": "complementary"}, {"relation": "distinct"},
    {"confidence": "medium"}, {"confidence": "low"}, {"unique_content": "additional restriction"},
    {"a_pages": []}, {"b_pages": [999]},
])
def test_nonredundant_or_incomplete_confirmation_preserves_both(changes):
    a, b = near_pair()
    assert dedupe.find_duplicates([a, b], confirmations={("a", "b"): decision(a, b, **changes)}) == []


@pytest.mark.parametrize("changes", [
    {"date_of_service": None}, {"date_of_service": "unknown"}, {"date_of_service": "2025-02-01"},
    {"record_type": "operative_report"}, {"record_type": None},
])
def test_confirmation_cannot_bypass_date_or_type_gate(changes):
    a, b = near_pair()
    b.update(changes)
    assert dedupe.find_duplicates([a, b], confirmations={("a", "b"): decision(a, b)}) == []


def test_unknown_date_cannot_bridge_two_known_encounters():
    a, b = near_pair()
    b["date_of_service"] = None
    c = {**a, "id": "c", "date_of_service": "2025-03-01", "text": a["text"] + " improved"}
    decisions = {(x["id"], y["id"]): decision(x, y) for x in [a, b, c] for y in [a, b, c]}
    assert dedupe.find_duplicates([a, b, c], confirmations=decisions) == []


@pytest.mark.parametrize("outcome", ["approve", "error", "fake", "incomplete", "too_long", "budget"])
def test_async_production_path_is_conservative_and_keeps_work_off_loop(monkeypatch, outcome):
    a, b = near_pair()
    for r in (a, b):
        r.update(text_complete=True, transcription=r["text"])
    if outcome == "incomplete":
        b["text_complete"] = False
    if outcome == "too_long":
        b["transcription"] = "x" * 80001
    calls, published, evidence = [], [], []
    caller_thread = threading.get_ident()

    def load():
        assert threading.get_ident() != caller_thread
        return [a, b]

    def publish(rows):
        assert threading.get_ident() != caller_thread
        published.extend(rows)

    async def confirm(x, y):
        calls.append((x["id"], y["id"]))
        if outcome == "error":
            raise RuntimeError("synthetic failure")
        return decision(x, y)

    monkeypatch.setattr(dedupe, "_load_items", load)
    monkeypatch.setattr(db, "replace_duplicate_groups", publish)
    monkeypatch.setattr(config, "FAKE_LLM", True)
    if outcome != "fake":
        monkeypatch.setattr(dedupe, "confirm_pair", confirm)
    asyncio.run(dedupe.dedupe_all(max_confirmations=0 if outcome == "budget" else 8, evidence=evidence))
    assert len(published) == (2 if outcome == "approve" else 0)
    assert len(calls) == (1 if outcome in {"approve", "error"} else 0)
    assert len(evidence) == 1


def test_real_confirmation_uses_existing_adapter_and_full_page_markers(monkeypatch, tmp_path):
    a, b = near_pair()
    for r in (a, b):
        r["transcription"] = "=== Page 1 ===\n" + r["text"]
    monkeypatch.setattr(config, "FAKE_LLM", False)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    calls = []

    async def text_json(system, user, model, tag):
        calls.append(tag)
        assert model is dedupe.DuplicateConfirmation
        assert a["transcription"] in user and b["transcription"] in user
        return decision(a, b)

    monkeypatch.setattr(dedupe.llm, "text_json", text_json)
    assert asyncio.run(dedupe.confirm_pair(a, b)).relation == "duplicate"
    assert len(calls) == 1


def test_copy_store_requires_fresh_separate_destination_and_rewrites_paths(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "scan.jpg"
    image.write_bytes(b"synthetic jpeg fixture")
    with sqlite3.connect(source / "app.db") as conn:
        conn.execute("CREATE TABLE pages(doc_id TEXT,page_no INTEGER,image_path TEXT)")
        conn.execute("INSERT INTO pages VALUES('synthetic',1,?)", (str(image),))
    before = (source / "app.db").read_bytes()
    for destination in [source, source / "child", tmp_path]:
        with pytest.raises(ValueError):
            copy_store(source, destination)
    destination = tmp_path / "copy"
    copy_store(source, destination)
    with sqlite3.connect(destination / "app.db") as conn:
        copied = conn.execute("SELECT image_path FROM pages").fetchone()[0]
    assert copied == str(destination / "pages" / "synthetic" / "0001.jpg")
    assert (destination / "pages" / "synthetic" / "0001.jpg").read_bytes() == image.read_bytes()
    assert (source / "app.db").read_bytes() == before
    with pytest.raises(ValueError):
        copy_store(source, destination)


def test_incomplete_text_cannot_prove_exact_text_match():
    a, b = near_pair()
    b["text"] = a["text"]
    b["text_complete"] = False
    assert dedupe.find_duplicates([a, b], exact_only=True) == []


def test_confirmation_budget_caps_calls_and_never_retries_reversed_pair(monkeypatch):
    common = " ".join(f"clinical{n}" for n in range(100))
    items = [record(str(n), [None], common + f" extra{n}", text_complete=True,
                    transcription=common + f" extra{n}") for n in range(6)]
    calls, evidence, published = [], [], []

    async def distinct(a, b):
        calls.append((a["id"], b["id"]))
        return decision(a, b, relation="distinct")

    monkeypatch.setattr(dedupe, "_load_items", lambda: items)
    monkeypatch.setattr(dedupe, "confirm_pair", distinct)
    monkeypatch.setattr(db, "replace_duplicate_groups", published.extend)
    asyncio.run(dedupe.dedupe_all(max_confirmations=8, evidence=evidence))
    assert len(calls) == 8
    assert len({frozenset(pair) for pair in calls}) == 8
    assert len(evidence) == 15 and not published
    assert sum(e.get("skipped") == "confirmation budget" for e in evidence) == 7


@pytest.mark.parametrize("score,expected", [(0.8, False), (0.849999, False), (0.85, True), (0.96, True)])
def test_affirmative_confirmation_cannot_bypass_point85_candidate_threshold(monkeypatch, score, expected):
    a, b = near_pair()
    monkeypatch.setattr(dedupe, "candidate_score", lambda *_: score)
    rows = dedupe.find_duplicates([a, b], confirmations={("a", "b"): decision(a, b)})
    assert bool(rows) is expected
    if rows:
        assert all(row["reason"] == "similar_confirmed" for row in rows)


@pytest.mark.parametrize("score,expected", [(0.8, False), (0.849999, False), (0.85, True), (0.96, True)])
def test_valid_uncertain_fallback_has_inclusive_point85_threshold(monkeypatch, score, expected):
    a, b = near_pair()
    monkeypatch.setattr(dedupe, "candidate_score", lambda *_: score)
    rows = dedupe.find_duplicates([a, b], confirmations={('a', 'b'): decision(
        a, b, relation='uncertain', confidence='medium', unique_content='Ambiguous signature state')})
    assert bool(rows) is expected
    if rows:
        assert all(r['reason'] == 'similar' for r in rows)
        assert next(r for r in rows if r['record_id'] == 'b')['score'] == score


@pytest.mark.parametrize('change', [
    {'date_of_service': None}, {'date_of_service': '2025-01-02'},
    {'record_type': None}, {'record_type': 'physical_therapy'},
])
def test_uncertain_fallback_preserves_same_date_and_same_type_gates(change):
    a, b = near_pair()
    b.update(change)
    assert dedupe.find_duplicates([a, b], confirmations={('a', 'b'): decision(a, b, relation='uncertain')}) == []


@pytest.mark.parametrize('bad', [None, {}, {'relation': 'uncertain'}, 'uncertain'])
def test_missing_or_invalid_response_is_never_uncertainty(bad):
    a, b = near_pair()
    assert dedupe.find_duplicates([a, b], confirmations={('a', 'b'): bad}) == []


@pytest.mark.parametrize('outcome', ['uncertain', 'distinct', 'different', 'complementary', 'invalid', 'missing', 'error'])
def test_production_fallback_requires_valid_uncertain_response(monkeypatch, outcome):
    a, b = near_pair()
    for item in (a, b):
        item.update(text_complete=True, transcription=item['text'])
    published, evidence = [], []
    async def confirm(*_):
        if outcome == 'error':
            raise RuntimeError('synthetic failure')
        if outcome == 'missing':
            return None
        if outcome == 'invalid':
            return {'relation': 'uncertain'}
        return decision(a, b, relation=outcome, confidence='medium')
    monkeypatch.setattr(dedupe, '_load_items', lambda: [a, b])
    monkeypatch.setattr(dedupe, 'confirm_pair', confirm)
    monkeypatch.setattr(db, 'replace_duplicate_groups', published.extend)
    asyncio.run(dedupe.dedupe_all(evidence=evidence))
    assert bool(published) == (outcome == 'uncertain')
    assert evidence[0]['accepted'] == (outcome == 'uncertain')
    if published:
        assert all(row['reason'] == 'similar' for row in published)


def cache_pair(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FAKE_LLM", False)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-6-luna")
    a, b = near_pair()
    for item in (a, b):
        item.update(text_complete=True, transcription=item["text"])
    return a, b


def test_confirmation_cache_survives_module_reload_and_preserves_first_decision(monkeypatch, tmp_path):
    import importlib
    a, b = cache_pair(monkeypatch, tmp_path)
    caller = threading.get_ident()
    calls = []
    original_key = dedupe.confirmation_cache_key
    original_save = dedupe.save_cached_confirmation
    def key(*args):
        assert threading.get_ident() != caller
        return original_key(*args)
    def save(*args, **kwargs):
        assert threading.get_ident() != caller
        return original_save(*args, **kwargs)
    async def model(*_):
        calls.append(True)
        return decision(a, b, relation="uncertain", confidence="medium")
    monkeypatch.setattr(dedupe, "confirmation_cache_key", key)
    monkeypatch.setattr(dedupe, "save_cached_confirmation", save)
    monkeypatch.setattr(dedupe.llm, "text_json", model)
    assert asyncio.run(dedupe.confirm_pair(a, b)).relation == "uncertain"
    importlib.reload(dedupe)
    assert asyncio.run(dedupe.confirm_pair(a, b)).relation == "uncertain"
    assert len(calls) == 1
    kept = dedupe.save_cached_confirmation(a, b, decision(a, b, relation="different"))
    assert kept.relation == "uncertain"


@pytest.mark.parametrize("field,value", [
    ("id", "changed"), ("doc_id", "changed"), ("start_page", 2), ("end_page", 2),
    ("text", "changed"), ("transcription", "changed"), ("text_complete", False),
    ("date_of_service", "2025-02-02"), ("record_type", "physical_therapy"),
])
@pytest.mark.parametrize("side", [0, 1])
def test_confirmation_cache_key_includes_both_complete_evidence_records(monkeypatch, tmp_path, field, value, side):
    a, b = cache_pair(monkeypatch, tmp_path)
    key = dedupe.confirmation_cache_key(a, b)
    (a, b)[side][field] = value
    assert dedupe.confirmation_cache_key(a, b) != key


def test_confirmation_cache_key_includes_model_and_exact_prompt(monkeypatch, tmp_path):
    a, b = cache_pair(monkeypatch, tmp_path)
    original_prompt = (dedupe.PROMPTS_DIR / "duplicate_confirm.md").read_text()
    prompt = tmp_path / "duplicate_confirm.md"
    prompt.write_text(original_prompt)
    monkeypatch.setattr(dedupe, "PROMPTS_DIR", tmp_path)
    key = dedupe.confirmation_cache_key(a, b)
    monkeypatch.setattr(config, "OPENAI_MODEL", "changed-model")
    assert dedupe.confirmation_cache_key(a, b) != key
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-6-luna")
    prompt.write_text(original_prompt + "\n")
    assert dedupe.confirmation_cache_key(a, b) != key


@pytest.mark.parametrize("relation", ["different", "distinct", "complementary"])
def test_cached_veto_is_reused_and_never_collapses(monkeypatch, tmp_path, relation):
    a, b = cache_pair(monkeypatch, tmp_path)
    dedupe.save_cached_confirmation(a, b, decision(a, b, relation=relation))
    async def forbidden(*_):
        pytest.fail("cached decision must not call model")
    monkeypatch.setattr(dedupe.llm, "text_json", forbidden)
    cached = asyncio.run(dedupe.confirm_pair(a, b))
    assert cached.relation == relation
    assert dedupe.confirmation_reason(cached, a, b) is None


def test_fake_mode_bypasses_valid_cache_and_writes(monkeypatch, tmp_path):
    a, b = cache_pair(monkeypatch, tmp_path)
    dedupe.save_cached_confirmation(a, b, decision(a, b, relation="uncertain"))
    monkeypatch.setattr(config, "FAKE_LLM", True)
    async def forbidden(*_):
        pytest.fail("fake mode must not call model")
    monkeypatch.setattr(dedupe.llm, "text_json", forbidden)
    assert asyncio.run(dedupe.confirm_pair(a, b)) is None
    a["text"] = "changed"
    assert dedupe.save_cached_confirmation(a, b, decision(a, b)) is None
    assert len(list((tmp_path / "duplicate_confirmations").glob("*.json"))) == 1


@pytest.mark.parametrize("response", [None, {}, {"relation": "uncertain"}, "error"])
def test_failed_or_invalid_confirmation_is_never_cached(monkeypatch, tmp_path, response):
    a, b = cache_pair(monkeypatch, tmp_path)
    async def model(*_):
        if response == "error":
            raise RuntimeError("synthetic failure")
        return response
    monkeypatch.setattr(dedupe.llm, "text_json", model)
    if response is None:
        assert asyncio.run(dedupe.confirm_pair(a, b)) is None
    else:
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(dedupe.confirm_pair(a, b))
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("corruption", ["not json", "{}", '{"key": "wrong", "decision": {}}'])
def test_corrupt_confirmation_cache_is_miss_and_replaced_with_valid_response(monkeypatch, tmp_path, corruption):
    a, b = cache_pair(monkeypatch, tmp_path)
    key = dedupe.confirmation_cache_key(a, b)
    path = tmp_path / "duplicate_confirmations" / f"{key}.json"
    path.parent.mkdir()
    path.write_text(corruption)
    calls = []
    async def model(*_):
        calls.append(True)
        return decision(a, b, relation="different")
    monkeypatch.setattr(dedupe.llm, "text_json", model)
    for _ in range(2):
        result = asyncio.run(dedupe.confirm_pair(a, b))
        assert result.relation == "different"
        assert dedupe.confirmation_reason(result, a, b) is None
    assert len(calls) == 1
