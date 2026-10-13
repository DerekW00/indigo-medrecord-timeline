"""Stage 1: render each PDF page to a JPEG (PyMuPDF), one pages row per page."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pymupdf

from .. import config, db

log = logging.getLogger("mrt.render")
CHUNK = 8  # pages rendered per thread call; the PDF is opened once per chunk


def image_path_for(doc_id: str, page_no: int) -> Path:
    return config.PAGES_DIR / doc_id / f"{page_no:04d}.jpg"


def _page_count(pdf_path: str) -> int:
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def _render_pages(pdf_path: str, targets: list[tuple[int, Path]]) -> None:
    """Render (page_no, path) pairs. Writes to a temp name first so a crash never leaves a
    truncated JPEG that a later run would skip as already rendered."""
    long_side = float(config.RENDER_LONG_SIDE)
    with pymupdf.open(pdf_path) as doc:
        for page_no, path in targets:
            page = doc[page_no - 1]
            zoom = long_side / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
            tmp = path.with_name(f"{path.stem}.part.jpg")
            pix.save(str(tmp), jpg_quality=80)
            os.replace(tmp, path)


async def render_document(doc_id: str, pdf_path: str, page_count: int) -> None:
    """Render every page that has no image yet (honoring MRT_MAX_PAGES) and upsert its pages row.
    Idempotent: existing images are skipped, existing rows keep their status."""
    actual = await asyncio.to_thread(_page_count, pdf_path)
    total = min(page_count, actual) if page_count else actual
    if config.MAX_PAGES > 0:
        total = min(total, config.MAX_PAGES)
    (config.PAGES_DIR / doc_id).mkdir(parents=True, exist_ok=True)
    db.set_document(doc_id, stage_done=0, stage_total=total)

    done = 0
    for start in range(1, total + 1, CHUNK):
        chunk = range(start, min(start + CHUNK, total + 1))
        missing = [(n, image_path_for(doc_id, n)) for n in chunk if not image_path_for(doc_id, n).exists()]
        if missing:
            await asyncio.to_thread(_render_pages, pdf_path, missing)
        for n in chunk:
            db.upsert_page(doc_id, n, str(image_path_for(doc_id, n)))
            done += 1
        db.set_document(doc_id, stage_done=done)
    log.info("%s render: %d pages (%d of %d in the file)", doc_id[:8], total, total, actual)
