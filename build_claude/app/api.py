"""FastAPI routes (section 9): documents, timeline, records, overrides, static files."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, worker
from .models import DOC_TYPES, RecordPatch

log = logging.getLogger("mrt.api")
STATIC_DIR = config.ROOT / "static"
OVERRIDE_FIELDS = ("title", "date_of_service", "record_type", "provider", "summary")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>Medical Record Timeline</title>
<body style="font-family:system-ui;margin:2rem"><h1>Medical Record Timeline</h1>
<p>The UI (static/index.html) is not built yet. The API is up: <code>GET /api/documents</code>,
<code>POST /api/documents</code>, <code>GET /api/timeline</code>, <code>GET /api/records/{id}</code>.</p></body>"""


def _mkdirs() -> None:
    for d in (config.DATA_DIR, config.PAGES_DIR, config.UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _mkdirs()
    db.init_db()
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Medical Record Timeline", lifespan=lifespan)
_mkdirs()  # StaticFiles checks the directory at mount time
app.mount("/pages", StaticFiles(directory=str(config.PAGES_DIR)), name="pages")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")


@app.get("/", include_in_schema=False)
async def index():
    page = STATIC_DIR / "index.html"
    if page.exists():
        return FileResponse(page, media_type="text/html")
    return HTMLResponse(_PLACEHOLDER)


@app.get("/api/meta")
async def meta():
    return {"doc_types": list(DOC_TYPES), "model": config.OPENAI_MODEL, "fake_llm": config.FAKE_LLM}


# ---------- documents ----------

def _doc_or_404(doc_id: str) -> dict:
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


def _busy(doc: dict) -> bool:
    return doc["status"] in worker.ACTIVE_STATUSES or worker.is_active(doc["id"])


@app.post("/api/documents")
async def upload_documents(files: list[UploadFile] = File(...)):
    """One or more PDFs. All files are validated before any is stored (all or nothing)."""
    staged = []
    for f in files:
        name = Path(f.filename or "upload.pdf").name
        data = await f.read()
        await f.close()
        try:
            doc_id, page_count = await asyncio.to_thread(worker.inspect_pdf, data)
        except ValueError as e:
            raise HTTPException(400, f"{name}: {e}") from e
        staged.append((doc_id, page_count, data, name))
    out = []
    for doc_id, page_count, data, name in staged:
        doc, is_new = await asyncio.to_thread(worker.store_pdf, doc_id, page_count, data, name)
        if is_new:
            worker.enqueue(doc_id)
        elif doc["status"] in worker.RETRYABLE_STATUSES or worker.needs_resume(doc):
            worker.retry(doc_id)  # failed/interrupted, or done under a lower MRT_MAX_PAGES
        log.info("upload %s (%s) new=%s", doc_id[:12], name, is_new)
        out.append({**db.get_document(doc_id), "existing": not is_new})
    return out


@app.get("/api/documents")
async def list_documents():
    return db.list_documents()


@app.post("/api/documents/{doc_id}/retry")
async def retry_document(doc_id: str):
    doc = _doc_or_404(doc_id)
    if _busy(doc):
        raise HTTPException(409, f"document is {doc['status']}, wait for it to finish")
    worker.retry(doc_id)
    return db.get_document(doc_id)


def _remove_files(doc_id: str) -> None:
    shutil.rmtree(config.PAGES_DIR / doc_id, ignore_errors=True)
    worker.pdf_path(doc_id).unlink(missing_ok=True)


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    doc = _doc_or_404(doc_id)
    if _busy(doc):
        raise HTTPException(409, f"document is {doc['status']}, wait for it to finish")
    db.delete_document(doc_id)
    await asyncio.to_thread(_remove_files, doc_id)
    try:  # duplicate groups may have lost members
        from .pipeline.dedupe import dedupe_all
        await worker.call_stage(dedupe_all)
    except Exception:
        log.exception("dedupe after delete failed")
    return {"ok": True, "id": doc_id}


# ---------- timeline items ----------

def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _item(rec: dict, ov: dict | None, dup: dict | None, members: dict, by_id: dict) -> dict:
    model = {k: rec.get(k) for k in OVERRIDE_FIELDS}
    effective = dict(model)
    edited = False
    if ov:
        for k in OVERRIDE_FIELDS:
            if ov.get(k) is not None:
                effective[k] = ov[k]
                edited = True
    item = {
        "id": rec["id"], "doc_id": rec["doc_id"], "filename": rec.get("filename"),
        "doc_created_at": rec.get("doc_created_at") or "",
        "start_page": rec["start_page"], "end_page": rec["end_page"],
        "page_count": rec["end_page"] - rec["start_page"] + 1,
        "status": rec.get("status"), "error": rec.get("error"),
        **effective,
        "date_confidence": "exact" if ov and ov.get("date_of_service") else rec.get("date_confidence"),
        "facility": rec.get("facility"),
        "key_findings": _loads(rec.get("key_findings_json"), []),
        "flags": _loads(rec.get("flags_json"), []),
        "model": model,
        "edited": edited,
        "hidden": bool(ov and ov.get("hidden")),
        "partial_start": bool(rec.get("partial_start")),
        "boundary_reason": rec.get("boundary_reason"),
        "duplicate": None,
    }
    if dup:
        ids = members.get(dup["group_id"], [])
        others = [
            {"id": o["id"], "filename": o.get("filename"), "start_page": o["start_page"], "end_page": o["end_page"]}
            for o in (by_id.get(rid) for rid in ids if rid != rec["id"]) if o
        ]
        item["duplicate"] = {
            "group_id": dup["group_id"], "is_canonical": bool(dup["is_canonical"]),
            "copies": len(ids), "others": others, "reason": dup.get("reason"), "score": dup.get("score"),
        }
    return item


def build_items(records: list[dict] | None = None) -> list[dict]:
    """Effective items (model fields with overrides applied, plus duplicate info) for `records`,
    default every done record. Records carry filename and doc_created_at (see db.all_done_records)."""
    done = db.all_done_records()
    if records is None:
        records = done
    by_id = {r["id"]: r for r in done}
    overrides = db.get_overrides()
    dups = db.get_duplicate_groups()
    members: dict[str, list[str]] = {}
    for rid, d in dups.items():
        members.setdefault(d["group_id"], []).append(rid)
    return [_item(r, overrides.get(r["id"]), dups.get(r["id"]), members, by_id) for r in records]


def sort_items(items: list[dict], desc: bool = False) -> list[dict]:
    """Date asc (or desc), undated last; ties keep document order then start_page."""
    def doc_order(i):
        return (i["doc_created_at"], i["start_page"])
    dated = sorted((i for i in items if i["date_of_service"]), key=doc_order)
    dated.sort(key=lambda i: i["date_of_service"], reverse=desc)  # stable, so ties keep doc order
    undated = sorted((i for i in items if not i["date_of_service"]), key=doc_order)
    return dated + undated


@app.get("/api/timeline")
async def timeline(include_duplicates: bool = False, include_hidden: bool = False,
                   type_: str = Query("", alias="type"), doc_id: str = "", sort: str = "asc"):
    items = build_items()
    if type_:
        items = [i for i in items if i["record_type"] == type_]
    if doc_id:
        items = [i for i in items if i["doc_id"] == doc_id]
    if not include_hidden:
        items = [i for i in items if not i["hidden"]]
    if not include_duplicates:
        items = [i for i in items if not (i["duplicate"] and not i["duplicate"]["is_canonical"])]
    return sort_items(items, desc=(sort == "desc"))


# ---------- records ----------

def _pages(rec: dict) -> list[dict]:
    out = []
    for p in db.get_pages(rec["doc_id"]):
        if not rec["start_page"] <= p["page_no"] <= rec["end_page"]:
            continue
        signals = _loads(p.get("signals_json"), {}) or {}
        name = Path(p["image_path"]).name if p.get("image_path") else f"{p['page_no']:04d}.jpg"
        out.append({
            "page_no": p["page_no"], "image_url": f"/pages/{rec['doc_id']}/{name}",
            "transcription": p.get("text") or "", "gist": signals.get("gist") or "",
            "status": p.get("status"),
        })
    return out


def _record_item(record_id: str) -> dict:
    rec = db.get_record(record_id)
    if rec is None:
        raise HTTPException(404, "record not found")
    doc = db.get_document(rec["doc_id"]) or {}
    rec["filename"] = doc.get("filename")
    rec["doc_created_at"] = doc.get("created_at")
    item = build_items([rec])[0]
    item["pages"] = _pages(rec)
    return item


def _clean_patch(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k in OVERRIDE_FIELDS and isinstance(v, str):
            v = v.strip() or None
        if k == "date_of_service" and v is not None and not DATE_RE.match(v):
            raise HTTPException(422, "date_of_service must be YYYY-MM-DD")
        if k == "record_type" and v is not None and v not in DOC_TYPES:
            raise HTTPException(422, f"record_type must be one of {', '.join(DOC_TYPES)}")
        out[k] = v
    return out


@app.get("/api/records/{record_id}")
async def get_record(record_id: str):
    return _record_item(record_id)


@app.patch("/api/records/{record_id}")
async def patch_record(record_id: str, patch: RecordPatch):
    """Absent field = untouched, null = clear that override field, value = set it."""
    if db.get_record(record_id) is None:
        raise HTTPException(404, "record not found")
    fields = _clean_patch(patch.model_dump(exclude_unset=True))
    if fields:
        from .pipeline.dedupe import dedupe_all_async, pairing_changed
        before = db.get_override(record_id)
        db.upsert_override(record_id, fields)
        if pairing_changed(before, db.get_override(record_id)):
            await dedupe_all_async()
    return _record_item(record_id)


@app.delete("/api/records/{record_id}/overrides")
async def reset_record(record_id: str):
    if db.get_record(record_id) is None:
        raise HTTPException(404, "record not found")
    from .pipeline.dedupe import dedupe_all_async, pairing_changed
    before = db.get_override(record_id)
    db.delete_override(record_id)
    if pairing_changed(before, None):
        await dedupe_all_async()
    return _record_item(record_id)
