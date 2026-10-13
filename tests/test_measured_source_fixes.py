"""Synthetic guards for bounded replay; no real medical fixtures or API calls."""
import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from app import config, db, llm
from app.models import FaxPage, PageSignals, RecordSummary
from app.pipeline.group import group_document
from scripts.replay_measured_fixes import backup_source, digest, failing_spans, refresh_pages, refresh_selection, replay


@pytest.mark.parametrize('raw', ['150', '153', '151,151', '151,152,153', '0'])
def test_rejects_unapproved_refresh_units(raw):
    with pytest.raises(ValueError):
        refresh_selection(raw)


def test_only_explicit_page_units_selected():
    assert refresh_selection('') == []
    assert refresh_selection('152') == [152]
    assert refresh_selection('151,152') == [151, 152]


@pytest.fixture
def store(tmp_path, monkeypatch):
    previous = getattr(db._local, 'conn', None)
    monkeypatch.setattr(db._local, 'conn', None, raising=False)
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'app.db')
    db.init_db()
    yield tmp_path
    db.connect().close()
    db._local.conn = previous


def seed(store, count=152):
    doc = db.upsert_document('synthetic', 'synthetic.pdf', count, 'done')
    for number in range(1, count + 1):
        image = store / 'pages' / 'synthetic' / f'{number:04d}.jpg'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b'synthetic-image')
        signal = PageSignals(transcription='Synthetic visit 2026-01-08.', gist='Synthetic', boundary_confidence='high',
                             starts_new_record=number == 1, date_of_service='2026-01-08',
                             doc_type='office_visit', page_label=FaxPage(k=number))
        db.upsert_page(doc['id'], number, str(image))
        db.save_page_result(doc['id'], number, signal.model_dump(), signal.transcription, 'test', 1)
    return doc


def test_backup_preserves_source_and_rewrites_copied_images(store):
    seed(store, 2)
    db.connect().execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before = digest(store / 'app.db')
    destination = store.parent / (store.name + '-copy')
    backup_source(store, destination)
    assert digest(store / 'app.db') == before
    with sqlite3.connect(destination / 'app.db') as copy:
        paths = [Path(row[0]) for row in copy.execute('SELECT image_path FROM pages')]
    assert len(paths) == 2
    assert all(path.is_relative_to(destination) and path.read_bytes() == b'synthetic-image' for path in paths)
    with pytest.raises(ValueError):
        backup_source(store, destination)
    with pytest.raises(ValueError):
        backup_source(store, store)
    gold = store / 'gold.json'
    gold.write_text('{}')
    with pytest.raises(ValueError):
        backup_source(gold, store.parent / 'bad-copy')


def test_refresh_calls_only_requested_pages_and_persists_validated_result(store, monkeypatch):
    doc = seed(store)
    calls = []
    async def vision(system, user, image_path, model, **kwargs):
        number = int(Path(image_path).stem)
        calls.append(number)
        return PageSignals(transcription='Visit: 2026-01-09', gist='Synthetic refresh', boundary_confidence='high', starts_new_record=False, doc_type='office_visit',
                           date_of_service='2026-01-09')
    monkeypatch.setattr(llm, 'vision_json', vision)
    before = {row['page_no']: row for row in db.get_pages(doc['id'])}
    audit = {'vision_page_units': []}
    asyncio.run(refresh_pages(doc, [151, 152], audit))
    assert calls == audit['vision_page_units'] == [151, 152]
    for row in db.get_pages(doc['id']):
        if row['page_no'] not in calls:
            assert row == before[row['page_no']]
        else:
            assert row['text'] == 'Visit: 2026-01-09'
            assert row['attempts'] == 2
    with pytest.raises(ValueError):
        asyncio.run(refresh_pages(doc, [1], audit))
    assert calls == [151, 152]


def test_replay_invalidates_only_failed_or_new_spans_and_never_reads_gold(store, monkeypatch):
    doc = seed(store, 3)
    for number in (2, 3):
        page = db.get_page(doc['id'], number)
        signal = PageSignals.model_validate_json(page['signals_json'])
        signal.starts_new_record = True
        signal.page_label = FaxPage(k=1)
        db.save_page_result(doc['id'], number, signal.model_dump(), signal.transcription, 'test', 1)
    group_document(doc['id'])
    records = db.get_records(doc['id'])
    summary = RecordSummary(title='Synthetic', record_type='office_visit', date_of_service='2026-01-08',
                            date_confidence='exact', summary='Synthetic source fact.')
    for record in records[:2]:
        db.save_record_summary(record['id'], summary.model_dump(), 'original-hash')
    preserved = db.get_record(records[0]['id'])
    calls = []
    async def text(system, user, model, **kwargs):
        calls.append(kwargs['tag'])
        return summary
    async def forbidden(*args, **kwargs):
        pytest.fail('replay called vision without an explicit request')
    monkeypatch.setattr(llm, 'text_json', text)
    monkeypatch.setattr(llm, 'vision_json', forbidden)
    report = {'doc_id': doc['id'], 'judge': {'records': [
        {'span': [1, 1], 'unsupported': 0}, {'span': [2, 2], 'unsupported': 1}]}}
    report_path = store / 'baseline.json'
    report_path.write_text(json.dumps(report))
    fingerprint = digest(report_path)
    audit = {'vision_page_units': [], 'summary_units': []}
    asyncio.run(replay(doc, [], failing_spans(report, doc['id']), audit))
    assert len(calls) == 2
    assert set(audit['invalidated_record_ids']) == {records[1]['id'], records[2]['id']}
    assert db.get_record(records[0]['id'])['summary'] == preserved['summary']
    assert db.get_record(records[0]['id'])['content_hash'] == preserved['content_hash']
    assert all(row['status'] == 'done' for row in db.get_records(doc['id']))
    assert digest(report_path) == fingerprint
    with pytest.raises(ValueError):
        failing_spans(report, 'other-document')
