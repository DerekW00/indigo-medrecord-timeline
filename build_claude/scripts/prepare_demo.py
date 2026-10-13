"""Prepare transparent cached-page demo inputs; never call a model.

The second bundle contains source pages 5..40. Its page extractions can be
reused because the rendered pixels are identical. No second-bundle summary is
copied: its last record is clipped, so the normal worker must summarize it.
Run with the target server stopped and a new --target-data directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pymupdf


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pixels(page) -> tuple[str, str]:
    zoom = 1600 / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return (hashlib.sha256(pix.samples).hexdigest(),
            hashlib.sha256(pix.tobytes("jpg", jpg_quality=80)).hexdigest())


def fixtures(source: Path, part: Path, retry: Path) -> dict:
    with pymupdf.open(source) as original:
        assert original.page_count == 188, "expected the original 188-page sample"
        if not part.exists():
            part.parent.mkdir(parents=True, exist_ok=True)
            with pymupdf.open() as subset:
                subset.insert_pdf(original, from_page=4, to_page=39)
                # Exclusive creation prevents another preparer from being overwritten.
                with part.open("xb") as output:
                    output.write(subset.tobytes())
        with pymupdf.open(part) as subset:
            assert subset.page_count == 36
            mapping = []
            for index in range(36):
                pixel_hash, image_hash = pixels(original[index + 4])
                assert (pixel_hash, image_hash) == pixels(subset[index]), f"page {index + 1} is not source page {index + 5}"
                mapping.append({"page": index + 1, "source_page": index + 5,
                                "pixels_sha256": pixel_hash, "expected_image_sha256": image_hash})
    if not retry.exists():
        retry.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as synthetic:
            for page in range(1, 13):
                synthetic.new_page().insert_text((72, 72), f"SYNTHETIC RETRY DEMO ONLY - page {page} of 12")
            with retry.open("xb") as output:
                output.write(synthetic.tobytes())
    with pymupdf.open(retry) as synthetic:
        assert synthetic.page_count == 12
        assert all("SYNTHETIC RETRY DEMO ONLY" in p.get_text() for p in synthetic)
    return {"source_pdf": str(source), "source_id": digest(source), "part_pdf": str(part),
            "part_id": digest(part), "retry_pdf": str(retry), "retry_id": digest(retry), "page_mapping": mapping}


def seed(source_data: Path, target: Path, manifest: dict) -> None:
    assert not target.exists(), "target must be a new directory; never overwrite a demo store"
    source_db = source_data / "app.db"
    source_hash = digest(source_db)
    target.mkdir(parents=True)
    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as original:
        with sqlite3.connect(target / "app.db") as copied:
            original.backup(copied)
    confirmations = source_data / "duplicate_confirmations"
    if confirmations.exists():
        assert confirmations.is_dir()
        shutil.copytree(confirmations, target / "duplicate_confirmations")
    with sqlite3.connect(target / "app.db") as conn:
        conn.row_factory = sqlite3.Row
        documents = conn.execute("SELECT id,status FROM documents").fetchall()
        assert len(documents) == 1 and documents[0]["id"] == manifest["source_id"]
        assert documents[0]["status"] == "done"
        pages = conn.execute("SELECT * FROM pages ORDER BY page_no").fetchall()
        assert len(pages) == 188 and all(p["status"] == "done" for p in pages)
        for row in pages:
            dest = target / "pages" / row["doc_id"] / f"{row['page_no']:04d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(row["image_path"], dest)
            conn.execute("UPDATE pages SET image_path=? WHERE doc_id=? AND page_no=?",
                         (str(dest), row["doc_id"], row["page_no"]))
        (target / "uploads").mkdir()
        shutil.copy2(manifest["source_pdf"], target / "uploads" / f"{manifest['source_id']}.pdf")
        for mapping in manifest["page_mapping"]:
            row = pages[mapping["source_page"] - 1]
            assert digest(Path(row["image_path"])) == mapping["expected_image_sha256"], "cached image differs from source"
            dest = target / "pages" / manifest["part_id"] / f"{mapping['page']:04d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(row["image_path"], dest)
            conn.execute("INSERT INTO pages(doc_id,page_no,image_path,status,attempts,error,signals_json,text,text_hash) "
                         "VALUES(?,?,?,'done',?,NULL,?,?,?)",
                         (manifest["part_id"], mapping["page"], str(dest), row["attempts"],
                          row["signals_json"], row["text"], row["text_hash"]))
            mapping["cached_image_sha256"] = digest(dest)
            mapping["signals_sha256"] = hashlib.sha256(row["signals_json"].encode()).hexdigest()
        conn.commit()
    assert digest(source_db) == source_hash, "source database changed during preparation"
    manifest.update(source_data=str(source_data), source_db_sha256=source_hash, target_data=str(target),
                    second_document_registered=False, second_summaries_copied=0, new_model_calls=0,
                    extraction_mode="36 existing real page signals replayed after pixel equality check")
    (target / "preparation.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--part-pdf", type=Path, required=True)
    parser.add_argument("--retry-pdf", type=Path, required=True)
    parser.add_argument("--source-data", type=Path)
    parser.add_argument("--target-data", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    assert bool(args.source_data) == bool(args.target_data), "provide both data directories or neither"
    result = fixtures(args.source_pdf.resolve(), args.part_pdf.resolve(), args.retry_pdf.resolve())
    if args.source_data:
        seed(args.source_data.resolve(), args.target_data.resolve(), result)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "page_mapping"}, indent=2))


if __name__ == "__main__":
    main()
