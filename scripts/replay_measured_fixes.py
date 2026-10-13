"""Bounded source repair. No rendering, full extraction, dedupe or judge is invoked.

Run in a fresh process: MRT_DATA_DIR is selected before importing app modules.
Only the explicitly authorized page units 151 and 152 can be refreshed. Gold is
read only by the unchanged evaluator after all source processing has finished.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ALLOWED_REFRESH = frozenset({151, 152})  # authorization boundary, never a clinical answer


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def refresh_selection(raw: str) -> list[int]:
    pages = [int(value) for value in raw.split(',') if value.strip()]
    if len(pages) != len(set(pages)) or not set(pages) <= ALLOWED_REFRESH:
        raise ValueError('Only distinct explicitly requested page units 151 and 152 are authorized')
    return pages


def backup_source(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_dir() or not (source / 'app.db').is_file():
        raise ValueError('source-data must be a cached store directory, not a gold/extraction file')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('source and destination must be separate stores')
    if destination.exists():
        raise ValueError('destination must be fresh; existing stores are never overwritten')
    destination.mkdir(parents=True)
    with sqlite3.connect((source / 'app.db').as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination / 'app.db') as dst:
            src.backup(dst)
    for name in ('pages', 'uploads'):
        if (source / name).exists():
            shutil.copytree(source / name, destination / name)
    with sqlite3.connect(destination / 'app.db') as dst:
        for doc_id, page_no, old_path in dst.execute('SELECT doc_id,page_no,image_path FROM pages').fetchall():
            original = Path(old_path).resolve()
            relative = original.relative_to(source)
            copied = destination / relative
            if not copied.is_file():
                raise ValueError(f'missing cached image for page {page_no}')
            dst.execute('UPDATE pages SET image_path=? WHERE doc_id=? AND page_no=?',
                        (str(copied), doc_id, page_no))


def failing_spans(report: dict, doc_id: str) -> set[tuple[int, int]]:
    if report.get('doc_id') != doc_id:
        raise ValueError('baseline report belongs to a different document')
    return {tuple(row['span']) for row in report['judge']['records'] if row.get('unsupported', 0)}


async def refresh_pages(doc: dict, selection: list[int], audit: dict) -> None:
    from app import db, llm
    from app.models import PageSignals
    from app.pipeline.extract import sha256_hex, simple_normalize, strip_fax_header
    from app.pipeline.prompts_util import load_prompt

    # Revalidate even when called directly, not through CLI parsing.
    refresh_selection(','.join(map(str, selection)))
    system, template = load_prompt('page_extract')
    for page_no in selection:
        page = db.get_page(doc['id'], page_no)
        if page is None or page['status'] != 'done':
            raise ValueError('refresh requires a completed cached page')
        audit['vision_page_units'].append(page_no)
        signals = await llm.vision_json(
            system, template.format(page_no=page_no, page_count=doc['page_count'], filename=doc['filename']),
            page['image_path'], PageSignals, tag=f"{doc['id'][:8]} p{page_no} targeted-refresh")
        signals = PageSignals.model_validate(signals)
        text = signals.transcription
        text_hash = await asyncio.to_thread(sha256_hex, simple_normalize(strip_fax_header(text, signals.fax_header)))
        db.save_page_result(doc['id'], page_no, signals.model_dump(), text, text_hash, page['attempts'] + 1)


async def replay(doc: dict, selection: list[int], bad_spans: set[tuple[int, int]], audit: dict) -> None:
    from app import db, llm
    from app.pipeline.group import group_document
    from app.pipeline.summarize import summarize_document

    before = db.get_records(doc['id'])
    audit['before_spans'] = [[r['start_page'], r['end_page']] for r in before]
    await refresh_pages(doc, selection, audit)
    group_document(doc['id'])
    affected = []
    for record in db.get_records(doc['id']):
        span = (record['start_page'], record['end_page'])
        if (record['status'] != 'done' or span in bad_spans or
                any(span[0] <= page <= span[1] for page in selection)):
            affected.append(record['id'])
            db._exec("UPDATE records SET status='pending',error=NULL WHERE id=?", (record['id'],))
    audit['invalidated_record_ids'] = affected
    original_text_json = llm.text_json

    async def counted(*args, **kwargs):
        audit['summary_units'].append(kwargs.get('tag', ''))
        return await original_text_json(*args, **kwargs)

    llm.text_json = counted
    try:
        failures = await summarize_document(doc['id'])
    finally:
        llm.text_json = original_text_json
    after = db.get_records(doc['id'])
    audit['after_spans'] = [[r['start_page'], r['end_page']] for r in after]
    old = {r['id']: r for r in before}
    fields = ('summary', 'date_of_service', 'date_confidence', 'record_type', 'key_findings_json', 'flags_json', 'content_hash')
    audit['changed_records'] = [
        {'id': r['id'], 'span': [r['start_page'], r['end_page']],
         'fields': [key for key in fields if old.get(r['id'], {}).get(key) != r.get(key)]}
        for r in after if r['id'] in affected]
    audit['removed_record_ids'] = sorted(set(old) - {r['id'] for r in after})
    db.set_document(doc['id'], status='failed' if failures else 'done',
                    error=f'{failures} summaries failed' if failures else None,
                    stage_done=sum(r['status'] == 'done' for r in after), stage_total=len(after))
    if failures:
        raise RuntimeError(f'{failures} summaries failed')


def acceptance(report: dict) -> bool:
    b = report['boundaries']
    return bool(b and b['spans']['matched'] == b['spans']['total'] == 31
                and b['pages']['hits'] == b['pages']['total'] == 188
                and b['fields']['date_hits'] == b['fields']['type_hits'] == b['fields']['n_matched'] == 31
                and b['starts']['fp'] == b['starts']['fn'] == 0
                and report['date_rule']['violations'] == 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-data', 'data-dir', 'doc-id', 'baseline-eval', 'gold', 'out'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--refresh-pages', default='')
    args = parser.parse_args(argv)
    selection = refresh_selection(args.refresh_pages)
    source, destination = Path(args.source_data).resolve(), Path(args.data_dir).resolve()
    report_path, gold, out = map(lambda p: Path(p).resolve(), (args.baseline_eval, args.gold, args.out))
    if out in (report_path, gold) or source == out or source in out.parents:
        raise ValueError('output must not overwrite immutable source evidence')
    os.environ['MRT_DATA_DIR'] = str(destination)
    if 'app.config' in sys.modules:
        raise RuntimeError('run replay in a fresh process before app imports')
    from app import config, db
    if config.OPENAI_MODEL != 'gpt-6-luna':
        raise ValueError('Only gpt-6-luna is authorized')
    if not config.FAKE_LLM and not config.OPENAI_API_KEY:
        raise ValueError('OPENAI_API_KEY is unavailable')
    fingerprints = {path: digest(path) for path in (source / 'app.db', report_path, gold)}
    baseline = json.loads(report_path.read_text())
    backup_source(source, destination)
    audit = {'source_data': str(source), 'destination': str(destination),
             'source_sha256': {str(p): value for p, value in fingerprints.items()},
             'code_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
             'model': config.OPENAI_MODEL, 'fake_llm': config.FAKE_LLM,
             'vision_page_units': [], 'summary_units': [], 'judge_calls': 0,
             'prompt_sha256': {name: digest(config.ROOT / 'app/prompts' / name)
                               for name in ('page_extract.md', 'record_summary.md')}}
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(destination / 'replay.log')])
    try:
        db.init_db()
        docs = [d for d in db.list_documents() if d['id'].startswith(args.doc_id)]
        if len(docs) != 1:
            raise ValueError('doc-id must select exactly one document')
        doc = docs[0]
        pages = db.get_pages(doc['id'])
        if len(pages) != doc['page_count'] or any(p['status'] != 'done' for p in pages):
            raise ValueError('source must contain all completed cached extractions')
        audit['doc_id'] = doc['id']
        bad = failing_spans(baseline, doc['id'])
        audit['baseline_unsupported_spans'] = sorted(bad)
        asyncio.run(replay(doc, selection, bad, audit))
        from eval.run_eval import main as evaluate
        evaluate(['--doc-id', doc['id'], '--gold', str(gold), '--out', str(out)])
        result = json.loads(out.read_text())
        audit['acceptance_passed'] = acceptance(result)
        return 0 if audit['acceptance_passed'] else 1
    finally:
        audit['source_unchanged'] = all(digest(path) == value for path, value in fingerprints.items())
        (destination / 'replay-provenance.json').write_text(json.dumps(audit, indent=2) + '\n')
        if not audit['source_unchanged']:
            raise RuntimeError('immutable evidence changed during replay')


if __name__ == '__main__':
    raise SystemExit(main())
