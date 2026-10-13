"""Synthetic document removal, lifecycle, and concurrent reupload regressions."""
import asyncio
import threading

import httpx
import pymupdf
import pytest

from app import api, config, db, llm, worker
from app.models import record_id_for
from app.pipeline import dedupe


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "FAKE_LLM", True)
    monkeypatch.setattr(db, "_local", threading.local())
    monkeypatch.setattr(api, "_document_mutations", asyncio.Lock())
    monkeypatch.setattr(worker, "_current", None)
    monkeypatch.setattr(worker, "enqueue", lambda doc_id: None)
    async def forbidden_model_call(*args, **kwargs):
        pytest.fail("Removal of exact synthetic copies must not call a model")

    monkeypatch.setattr(llm, "text_json", forbidden_model_call)
    api._mkdirs()
    db.init_db()
    yield tmp_path
    db.connect().close()


def seed(index, status="done"):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 40), f"Synthetic removal fixture {index}")
        data = pdf.tobytes()
    doc_id, count = worker.inspect_pdf(data)
    worker.store_pdf(doc_id, count, data, f"synthetic-{index}.pdf")
    db.set_document(doc_id, status=status, created_at=f"2026-01-0{index}T00:00:00+00:00")
    directory = config.PAGES_DIR / doc_id
    directory.mkdir()
    image = directory / "0001.jpg"
    image.write_bytes(b"identical synthetic image")
    db.upsert_page(doc_id, 1, str(image))
    text = "Synthetic nonmedical alpha beta gamma delta epsilon zeta eta theta facts."
    db.save_page_result(doc_id, 1, {}, text, dedupe.content_hash(text), 1)
    rid = record_id_for(doc_id, 1, 1)
    db.sync_records(doc_id, [{"id": rid, "start_page": 1, "end_page": 1,
                              "boundary_reason": "synthetic", "partial_start": 0}])
    db.save_record_summary(rid, {"record_type": "other", "title": "Synthetic record",
        "date_of_service": "2026-01-01", "date_confidence": "exact",
        "summary": "Synthetic fixture only."}, dedupe.content_hash(text))
    db.upsert_override(rid, {"title": f"Edited synthetic {index}"})
    return doc_id, rid, data


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "failed"])
async def test_removal_cleans_owned_data_and_recanonicalizes_remaining_copies(store, status):
    first, second, third = seed(1, status), seed(2), seed(3)
    await dedupe.dedupe_all_async()
    assert db.get_duplicate_groups()[first[1]]["is_canonical"]
    async with client() as http:
        response = await http.delete(f"/api/documents/{first[0]}")
        assert response.status_code == 200
        assert db.get_document(first[0]) is None
        assert db.get_pages(first[0]) == []
        assert db.get_record(first[1]) is None
        assert db.get_override(first[1]) is None
        assert not worker.pdf_path(first[0]).exists()
        assert not (config.PAGES_DIR / first[0]).exists()
        for doc_id, rid, data in (second, third):
            assert worker.pdf_path(doc_id).read_bytes() == data
            assert (config.PAGES_DIR / doc_id / "0001.jpg").exists()
            assert db.get_record(rid) and db.get_override(rid)
        timeline = (await http.get("/api/timeline")).json()
        assert len(timeline) == 1 and timeline[0]["id"] == second[1]
        assert timeline[0]["duplicate"]["copies"] == 2
        assert timeline[0]["duplicate"]["is_canonical"]
        assert first[1] not in db.get_duplicate_groups()
        assert (await http.delete(f"/api/documents/{first[0]}")).status_code == 404
        uploaded = await http.post("/api/documents", files={
            "files": ("again.pdf", first[2], "application/pdf")})
        assert uploaded.status_code == 200
        assert uploaded.json()[0]["id"] == first[0]
        assert uploaded.json()[0]["existing"] is False
        assert worker.pdf_path(first[0]).read_bytes() == first[2]
        assert db.get_record(first[1]) is None and db.get_override(first[1]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "interrupted", *worker.ACTIVE_STATUSES])
async def test_removal_rejects_unfinished_document(store, status):
    doc_id, rid, data = seed(1, status)
    async with client() as http:
        assert (await http.delete(f"/api/documents/{doc_id}")).status_code == 409
    assert db.get_document(doc_id)["status"] == status
    assert db.get_record(rid) and worker.pdf_path(doc_id).read_bytes() == data


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "failed"])
async def test_removal_rejects_document_still_owned_by_worker(store, monkeypatch, status):
    doc_id, _, _ = seed(1, status)
    monkeypatch.setattr(worker, "_current", doc_id)
    async with client() as http:
        assert (await http.delete(f"/api/documents/{doc_id}")).status_code == 409
    assert db.get_document(doc_id)


@pytest.mark.asyncio
async def test_reupload_waits_until_old_file_cleanup_finishes(store, monkeypatch):
    doc_id, _, data = seed(1)
    entered, release, inspected = threading.Event(), threading.Event(), threading.Event()
    remove_files, inspect_pdf = api._remove_files, worker.inspect_pdf

    def paused_cleanup(value):
        entered.set()
        assert release.wait(5), "test failed to release synthetic cleanup"
        remove_files(value)

    def inspected_pdf(value):
        result = inspect_pdf(value)
        inspected.set()
        return result

    monkeypatch.setattr(api, "_remove_files", paused_cleanup)
    monkeypatch.setattr(worker, "inspect_pdf", inspected_pdf)
    async with client() as http:
        removal = asyncio.create_task(http.delete(f"/api/documents/{doc_id}"))
        upload = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            upload = asyncio.create_task(http.post("/api/documents", files={
                "files": ("again.pdf", data, "application/pdf")}))
            assert await asyncio.to_thread(inspected.wait, 5)
            # An event-loop barrier gives upload registration a chance to run.
            await asyncio.sleep(0)
            assert not upload.done()
            release.set()
            assert (await removal).status_code == 200
            response = await upload
            assert response.status_code == 200 and not response.json()[0]["existing"]
            assert db.get_document(doc_id)["status"] == "queued"
            assert worker.pdf_path(doc_id).read_bytes() == data
        finally:
            release.set()
            await asyncio.gather(*(t for t in (removal, upload) if t), return_exceptions=True)


@pytest.mark.asyncio
async def test_removal_unlinks_cached_page_directory_without_removing_source(store):
    doc_id, _, _ = seed(1)
    pages = config.PAGES_DIR / doc_id
    source = store / "original-source-pages"
    pages.rename(source)
    pages.symlink_to(source, target_is_directory=True)
    original_pdf = store / "original.pdf"
    original_pdf.write_bytes(worker.pdf_path(doc_id).read_bytes())
    async with client() as http:
        assert (await http.delete(f"/api/documents/{doc_id}")).status_code == 200
    assert not pages.is_symlink()
    assert (source / "0001.jpg").read_bytes() == b"identical synthetic image"
    assert original_pdf.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_metadata_for_removal_retry(store, monkeypatch):
    doc_id, rid, data = seed(1)
    remove_files = api._remove_files

    def failed_cleanup(value):
        raise OSError("synthetic filesystem failure")

    monkeypatch.setattr(api, "_remove_files", failed_cleanup)
    transport = httpx.ASGITransport(app=api.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.delete(f"/api/documents/{doc_id}")).status_code == 500
        assert db.get_document(doc_id)["status"] == "done"
        assert db.get_record(rid) and db.get_override(rid)
        assert len(db.get_pages(doc_id)) == 1
        assert worker.pdf_path(doc_id).read_bytes() == data
        monkeypatch.setattr(api, "_remove_files", remove_files)
        assert (await http.delete(f"/api/documents/{doc_id}")).status_code == 200
        assert db.get_document(doc_id) is None and db.get_record(rid) is None
        assert db.get_override(rid) is None and db.get_pages(doc_id) == []
        assert not worker.pdf_path(doc_id).exists()
