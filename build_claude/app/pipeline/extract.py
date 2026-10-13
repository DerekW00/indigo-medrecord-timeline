"""Stage 2: one vision call per page -> PageSignals saved on the pages row.

Pages run in parallel under a semaphore. A page failure never stops the document: the page is
marked failed and the rest continue. The only exception is the MRT_FAIL_AT_PAGE demo hook: it
fails that page once per process and stops launching further pages, so the document stops there
(earlier pages are saved, later pages stay pending) and a retry visibly resumes from that page.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

from .. import config, db, llm
from ..models import PageSignals
from .prompts_util import load_prompt

log = logging.getLogger("mrt.extract")
_fail_hook_fired: set[int] = set()  # page numbers where the injected failure already fired
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def simple_normalize(text: str) -> str:
    """Lowercase, keep [a-z0-9 ], collapse whitespace (local copy of the dedupe normalization)."""
    return _SPACES.sub(" ", _NON_ALNUM.sub(" ", text.lower())).strip()


def strip_fax_header(text: str, fax_header: str | None) -> str:
    """Remove the fax header pieces from a transcription so two faxes of one page hash alike."""
    if not fax_header:
        return text
    for piece in fax_header.split("|"):
        piece = piece.strip()
        if len(piece) >= 6:
            text = text.replace(piece, " ")
    return text


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def extract_document(doc_id: str) -> int:
    """Run the vision call on every page whose status is not done. Returns the failed page count."""
    doc = db.get_document(doc_id)
    if doc is None:
        raise ValueError(f"unknown document {doc_id}")
    pages = db.get_pages(doc_id)
    todo = [p for p in pages if p["status"] != "done"]
    progress = {"done": len(pages) - len(todo)}
    db.set_document(doc_id, stage_done=progress["done"], stage_total=len(pages))

    system, user_template = load_prompt("page_extract")
    sem = asyncio.Semaphore(max(1, config.CONCURRENCY))
    stop = asyncio.Event()  # set by the injected failure: pages not yet started stay pending

    async def run_page(page: dict) -> None:
        page_no = int(page["page_no"])
        attempts = int(page["attempts"] or 0) + 1
        tag = f"{doc_id[:8]} p{page_no}"
        async with sem:
            if stop.is_set():
                return
            try:
                if config.FAIL_AT_PAGE and page_no == config.FAIL_AT_PAGE and page_no not in _fail_hook_fired:
                    _fail_hook_fired.add(page_no)
                    stop.set()
                    raise RuntimeError("injected failure")
                user = user_template.format(page_no=page_no, page_count=doc["page_count"], filename=doc["filename"])
                signals = await llm.vision_json(system, user, page["image_path"], PageSignals, tag=tag)
                assert isinstance(signals, PageSignals)
                text = signals.transcription
                text_hash = sha256_hex(simple_normalize(strip_fax_header(text, signals.fax_header)))
                db.save_page_result(doc_id, page_no, signals.model_dump(), text, text_hash, attempts)
                progress["done"] += 1
                db.set_document(doc_id, stage_done=progress["done"])
            except Exception as exc:  # LLMError after its retries, or anything else: page fails, doc continues
                message = str(exc) or type(exc).__name__
                log.warning("%s extract failed (attempt %d): %s", tag, attempts, message)
                db.save_page_failure(doc_id, page_no, message, attempts)

    await asyncio.gather(*(run_page(p) for p in todo))
    failed = sum(1 for p in db.get_pages(doc_id) if p["status"] == "failed")
    log.info("%s extract: %d done, %d failed, %d pages", doc_id[:8], progress["done"], failed, len(pages))
    return failed
