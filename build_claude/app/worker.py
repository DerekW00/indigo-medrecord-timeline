"""Document queue, stage runner, resume on startup, and PDF registration.

One asyncio.Queue of document ids, one worker task. Stages are idempotent and
skip done units, so a retry or a server restart resumes where it stopped.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import threading
import uuid
from pathlib import Path

import pymupdf

from . import config, db

log = logging.getLogger("mrt.worker")

ACTIVE_STATUSES = ("rendering", "extracting", "grouping", "summarizing", "deduping")
RETRYABLE_STATUSES = ("failed", "interrupted")

_queue: asyncio.Queue[str] | None = None
_pending: set[str] = set()      # ids waiting in the queue (dedupes enqueue)
_task: asyncio.Task | None = None
_current: str | None = None     # id being processed right now
_store_lock = threading.Lock()  # concurrent uploads of one file: one writer, one is_new


# ---------- PDF registration (shared by the upload route and the CLI) ----------

def pdf_path(doc_id: str) -> Path:
    return config.UPLOADS_DIR / f"{doc_id}.pdf"


def effective_page_count(page_count: int) -> int:
    """Pages this process handles: the file's page count capped by MRT_MAX_PAGES (0 = all)."""
    return min(page_count, config.MAX_PAGES) if config.MAX_PAGES > 0 else page_count


def inspect_pdf(data: bytes) -> tuple[str, int]:
    """sha256 id and the file's real page count. MRT_MAX_PAGES is applied at stage time, never
    stored, so lifting the cap later processes the rest of the file. Raises ValueError for
    unreadable PDFs."""
    doc_id = hashlib.sha256(data).hexdigest()
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            n = pdf.page_count
    except Exception as e:  # pymupdf raises several types for bad input
        raise ValueError(f"not a readable PDF ({e})") from e
    if n < 1:
        raise ValueError("PDF has no pages")
    return doc_id, n


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a per-call temp name and rename: a concurrent or crashed writer never leaves
    a partial file under the final name, and two writers never share a temp file."""
    tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def store_pdf(doc_id: str, page_count: int, data: bytes, filename: str) -> tuple[dict, bool]:
    """Write the file (if absent) and the documents row. Returns (document, is_new).
    Serialized, so concurrent uploads of the same bytes get exactly one writer and one is_new."""
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = pdf_path(doc_id)
    with _store_lock:
        if not path.exists():
            _write_atomic(path, data)
        existing = db.get_document(doc_id)
        if existing is None:
            return db.upsert_document(doc_id, Path(filename).name or "upload.pdf", page_count), True
        if existing["page_count"] != page_count:  # stored by an older build that capped the count
            db.set_document(doc_id, page_count=page_count)
            existing = db.get_document(doc_id)
        return existing, False


def needs_resume(doc: dict) -> bool:
    """True for a done document that holds fewer pages than this process would handle: it was
    processed under a lower MRT_MAX_PAGES. A retry renders and extracts only the missing pages."""
    if doc["status"] != "done":
        return False
    return _progress(doc["id"], "rendering") < effective_page_count(int(doc["page_count"] or 0))


def register_pdf(path: str | Path) -> str:
    """Register a PDF from disk without the server. Returns the document id."""
    p = Path(path)
    data = p.read_bytes()
    doc_id, n = inspect_pdf(data)
    store_pdf(doc_id, n, data, p.name)
    return doc_id


# ---------- queue ----------

def _get_queue() -> asyncio.Queue[str]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def enqueue(doc_id: str) -> None:
    if doc_id in _pending:
        return
    _pending.add(doc_id)
    _get_queue().put_nowait(doc_id)


def retry(doc_id: str) -> None:
    """Re-run a document: failed pages become pending again, nothing is deleted."""
    db.reset_failed_pages(doc_id)
    db.set_document(doc_id, status="queued", error=None)
    enqueue(doc_id)


def is_active(doc_id: str) -> bool:
    return doc_id == _current


def start() -> None:
    """Call once inside the running event loop: resume interrupted documents, start the worker."""
    global _task
    if _task is not None and not _task.done():
        return
    for doc in db.list_documents():
        if doc["status"] not in ("done", "failed"):
            db.set_document(doc["id"], status="interrupted")
            enqueue(doc["id"])
            log.info("resume %s (%s)", doc["id"][:12], doc["filename"])
        elif needs_resume(doc):
            log.warning("%s (%s): %d of %d pages processed under a lower MRT_MAX_PAGES; drop the file "
                        "again or POST /api/documents/%s/retry to process the rest", doc["id"][:12],
                        doc["filename"], _progress(doc["id"], "rendering"),
                        effective_page_count(int(doc["page_count"] or 0)), doc["id"])
    _task = asyncio.get_running_loop().create_task(_run(), name="mrt-worker")


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None


async def _run() -> None:
    global _current
    queue = _get_queue()
    while True:
        doc_id = await queue.get()
        _pending.discard(doc_id)
        _current = doc_id
        try:
            await process_document(doc_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker: unhandled error on %s", doc_id)
        finally:
            _current = None
            queue.task_done()


# ---------- stage runner ----------

async def call_stage(fn, *args):
    """Run a stage function whether it is async or sync (sync ones go to a thread)."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    result = await asyncio.to_thread(fn, *args)
    if inspect.isawaitable(result):
        result = await result
    return result


def _progress(doc_id: str, stage: str) -> int:
    """Count finished units of a stage straight from the store (independent of the stage code)."""
    conn = db.connect()
    if stage == "rendering":
        sql, args = "SELECT COUNT(*) FROM pages WHERE doc_id=?", (doc_id,)
    elif stage == "extracting":
        sql, args = "SELECT COUNT(*) FROM pages WHERE doc_id=? AND status='done'", (doc_id,)
    else:
        sql, args = "SELECT COUNT(*) FROM records WHERE doc_id=? AND status='done'", (doc_id,)
    return conn.execute(sql, args).fetchone()[0]


async def _run_stage(doc_id: str, stage: str, total: int, fn, *args):
    """Set the stage status, run it, and keep documents.stage_done fresh while it runs."""
    done = await asyncio.to_thread(_progress, doc_id, stage)
    db.set_document(doc_id, status=stage, stage_done=done, stage_total=total)
    task = asyncio.ensure_future(call_stage(fn, *args))
    last = done
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=1.0)
            cur = await asyncio.to_thread(_progress, doc_id, stage)
            if cur != last:
                last = cur
                db.set_document(doc_id, stage_done=cur)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        else:
            cur = await asyncio.to_thread(_progress, doc_id, stage)
            db.set_document(doc_id, stage_done=cur)


async def process_document(doc_id: str) -> None:
    """Run every stage in order. Any exception marks the document failed and returns."""
    from .pipeline.dedupe import dedupe_all
    from .pipeline.extract import extract_document
    from .pipeline.group import group_document
    from .pipeline.render import render_document
    from .pipeline.summarize import summarize_document

    doc = db.get_document(doc_id)
    if doc is None:
        log.warning("process_document: unknown document %s", doc_id)
        return
    pdf = pdf_path(doc_id)
    page_count = int(doc["page_count"] or 0)         # the file's real page count
    effective = effective_page_count(page_count)     # what this run handles (MRT_MAX_PAGES)
    cap = f", first {effective}" if effective < page_count else ""
    log.info("start %s (%s, %s pages%s)", doc_id[:12], doc["filename"], page_count, cap)
    try:
        if not pdf.exists():
            raise FileNotFoundError(f"upload file missing: {pdf}")
        db.set_document(doc_id, error=None)

        await _run_stage(doc_id, "rendering", effective, render_document, doc_id, str(pdf), page_count)

        n_pages = await asyncio.to_thread(_progress, doc_id, "rendering")  # rows, incl. earlier runs
        failed_pages = await _run_stage(doc_id, "extracting", n_pages, extract_document, doc_id)
        if failed_pages:
            db.set_document(doc_id, status="failed", error=f"{failed_pages} pages failed, retry to resume")
            log.warning("%s: %s pages failed", doc_id[:12], failed_pages)
            return

        db.set_document(doc_id, status="grouping", stage_done=0, stage_total=0)
        spans = await call_stage(group_document, doc_id)

        failed_records = await _run_stage(doc_id, "summarizing", len(spans or []), summarize_document, doc_id)
        if failed_records:
            db.set_document(doc_id, status="failed", error=f"{failed_records} records failed, retry to resume")
            log.warning("%s: %s records failed", doc_id[:12], failed_records)
            return

        db.set_document(doc_id, status="deduping")
        await call_stage(dedupe_all)

        db.set_document(doc_id, status="done", error=None)
        log.info("done %s: %s records", doc_id[:12], len(spans or []))
    except asyncio.CancelledError:
        db.set_document(doc_id, status="interrupted")
        raise
    except Exception as e:
        log.exception("%s failed", doc_id[:12])
        db.set_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}"[:2000])
