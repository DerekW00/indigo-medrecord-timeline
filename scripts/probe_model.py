"""M0 probe: send page 1 of the sample as an image, ask for JSON, print the parsed result.

    .venv/bin/python -m scripts.probe_model [samples/record_1.pdf] [page_no]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app import config, llm  # noqa: E402
from app.models import PageSignals  # noqa: E402


class Probe(BaseModel):
    document_title: str
    fax_header: str | None
    dates_seen: list[str]
    first_sentence: str


async def main() -> None:
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(config.ROOT / "samples" / "record_1.pdf")
    page_no = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    out = Path("/tmp/mrt_probe.jpg")
    doc = pymupdf.open(pdf)
    page = doc[page_no - 1]
    zoom = config.RENDER_LONG_SIDE / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    pix.save(str(out), jpg_quality=80)
    print(f"model={config.OPENAI_MODEL} page={page_no} image={out} ({out.stat().st_size} bytes)")

    t0 = time.monotonic()
    probe = await llm.vision_json(
        "You read scanned medical pages. Output JSON only, matching the schema.",
        "Give the document title, the fax header verbatim (or null), every date you see, "
        "and the first full sentence of body text.",
        out, Probe, tag="probe")
    print(f"probe ok in {time.monotonic() - t0:.1f}s:", json.dumps(probe.model_dump(), indent=2))

    # Full PageSignals extraction with the production prompt, if it exists already.
    try:
        from app.pipeline.prompts_util import load_prompt
        system, user = load_prompt("page_extract")
        t0 = time.monotonic()
        sig = await llm.vision_json(system, user.format(page_no=page_no, page_count=doc.page_count,
                                                        filename=Path(pdf).name), out, PageSignals, tag=f"probe p{page_no}")
        print(f"PageSignals ok in {time.monotonic() - t0:.1f}s:", json.dumps(sig.model_dump(), indent=2)[:3000])
    except ImportError:
        print("prompts_util not present yet; skipped the PageSignals probe")


if __name__ == "__main__":
    asyncio.run(main())
