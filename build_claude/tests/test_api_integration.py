"""Synthetic API regressions for reviewer edits that change duplicate membership."""
import threading

import httpx
import pytest

from app import api, config, db
from app.models import record_id_for
from app.pipeline import dedupe


@pytest.fixture
def duplicate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "FAKE_LLM", True)
    monkeypatch.setattr(db, "_local", threading.local())
    db.init_db()
    text = ("Synthetic test document containing enough distinct tokens for exact duplicate detection. "
            "The source consists of invented alpha beta gamma delta epsilon zeta eta theta facts. "
            "This fixture contains no real personal or medical information.")
    ids = []
    for index in (1, 2):
        doc_id = f"synthetic-document-{index}"
        db.upsert_document(doc_id, f"synthetic-{index}.pdf", 1, "done")
        db.set_document(doc_id, created_at=f"2026-01-0{index}T00:00:00+00:00")
        db.upsert_page(doc_id, 1, str(tmp_path / f"absent-{index}.jpg"))
        db.save_page_result(doc_id, 1, {}, text, dedupe.content_hash(text), 1)
        rid = record_id_for(doc_id, 1, 1)
        db.sync_records(doc_id, [{"id": rid, "start_page": 1, "end_page": 1,
                                  "boundary_reason": "synthetic", "partial_start": 0}])
        db.save_record_summary(rid, {"record_type": "other", "title": "Synthetic record",
            "date_of_service": "2026-01-01", "date_confidence": "exact",
            "summary": "Synthetic fixture only."}, dedupe.content_hash(text))
        ids.append(rid)
    yield ids
    db.connect().close()


@pytest.mark.asyncio
async def test_hiding_canonical_exposes_copy_and_reset_restores_group(duplicate_store):
    canonical, copy = duplicate_store
    await dedupe.dedupe_all_async()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
        assert [r["id"] for r in (await client.get("/api/timeline")).json()] == [canonical]
        response = await client.patch(f"/api/records/{canonical}", json={"hidden": True})
        assert response.status_code == 200 and response.json()["hidden"]
        visible = (await client.get("/api/timeline")).json()
        assert [r["id"] for r in visible] == [copy]
        assert visible[0]["duplicate"] is None
        reset = await client.delete(f"/api/records/{canonical}/overrides")
        assert reset.status_code == 200
        assert reset.json()["duplicate"]["is_canonical"]
        assert reset.json()["duplicate"]["copies"] == 2
        assert [r["id"] for r in (await client.get("/api/timeline")).json()] == [canonical]
        assert len((await client.get("/api/timeline?include_duplicates=1")).json()) == 2


@pytest.mark.asyncio
async def test_prose_only_save_and_reset_do_not_repeat_duplicate_confirmation(duplicate_store, monkeypatch):
    async def forbidden():
        pytest.fail("Prose-only edits must not recompute duplicate groups")
    monkeypatch.setattr(dedupe, "dedupe_all_async", forbidden)
    rid = duplicate_store[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
        response = await client.patch(f"/api/records/{rid}", json={"title": "Reviewer title", "summary": "Reviewer summary"})
        assert response.status_code == 200 and response.json()["edited"]
        reset = await client.delete(f"/api/records/{rid}/overrides")
        assert reset.status_code == 200 and not reset.json()["edited"]
