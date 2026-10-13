"""Real-browser rehearsal; full188 data is copied into disposable local stores.

Set MRT_UI_DATA_SOURCE for full188; MRT_TEST_BROWSER can select Chrome/Chromium.
No screenshots or medical text are written to tracked output.
"""
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from test_resume_demo import Server, eventually, scratch, synthetic_pdf


@pytest.fixture
def browser():
    playwright = pytest.importorskip('playwright.sync_api', reason='Install requirements-dev.txt')
    with playwright.sync_playwright() as p:
        executable = os.environ.get('MRT_TEST_BROWSER') or shutil.which('google-chrome') or shutil.which('chromium')
        try:
            browser = p.chromium.launch(executable_path=executable, headless=True)
        except playwright.Error as exc:
            pytest.fail(f'Browser unavailable; install Chromium or set MRT_TEST_BROWSER: {exc}')
        yield browser
        browser.close()


def copy_completed_store(source, target):
    source = source.resolve()
    target.mkdir(parents=True, exist_ok=False)
    assert source != target.resolve()
    with sqlite3.connect(f"file:{source / 'app.db'}?mode=ro", uri=True) as src:
        assert src.execute("SELECT count(*) FROM documents WHERE status != 'done'").fetchone()[0] == 0
        assert src.execute("SELECT count(*) FROM pages WHERE status='done'").fetchone()[0] == 188
        with sqlite3.connect(target / 'app.db') as dst:
            src.backup(dst)
    for name in ('pages', 'uploads'):
        shutil.copytree(source / name, target / name)
    with sqlite3.connect(target / 'app.db') as dst:
        for doc_id, page_no, old in dst.execute('SELECT doc_id,page_no,image_path FROM pages').fetchall():
            image = target / 'pages' / doc_id / Path(old).name
            assert image.is_file()
            dst.execute('UPDATE pages SET image_path=? WHERE doc_id=? AND page_no=?',
                        (str(image), doc_id, page_no))


def wait_cards(page, count):
    page.wait_for_function("n => document.querySelectorAll('#timeline .card').length === n", arg=count)


def open_card(page, record_id):
    page.locator(f'.card[data-id="{record_id}"]').click()
    page.locator('.edit-form [name="title"]').wait_for()


def test_full188_sources_filters_edit_restart_reset(browser):
    source = os.environ.get('MRT_UI_DATA_SOURCE')
    if not source:
        pytest.skip('Full188 requires MRT_UI_DATA_SOURCE; synthetic browser tests still run')
    with scratch('full188-browser-') as directory:
        copy_completed_store(Path(source), directory / 'store')
        with Server(directory) as server:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.url)
            all_items = server.client.get('/api/timeline?include_duplicates=1').json()
            visible = server.client.get('/api/timeline').json()
            wait_cards(page, len(visible))
            assert len(all_items) >= 31
            source_pages = {}
            for item in all_items:
                response = server.client.get(f"/api/records/{item['id']}")
                response.raise_for_status()
                for record_page in response.json()['pages']:
                    source_pages[(item['doc_id'], record_page['page_no'])] = record_page['image_url']
            assert len(source_pages) == 188
            for url in source_pages.values():
                response = server.client.get(url)
                assert response.status_code == 200 and response.headers['content-type'].startswith('image/')
                assert response.content.startswith(b'\xff\xd8')
            page.locator('#filter-dups').check()
            wait_cards(page, len(all_items))
            # Baseline has no groups. On Task3's repaired copy, verify every
            # actual group, badge, member and source link without fixed counts.
            canonical = [i for i in all_items if i['duplicate'] and i['duplicate']['is_canonical']]
            expected_groups = os.environ.get('MRT_UI_EXPECT_DUPLICATE_GROUPS')
            if expected_groups is not None:
                assert len(canonical) == int(expected_groups)
            for item in canonical:
                duplicate = item['duplicate']
                card = page.locator(f'.card[data-id="{item["id"]}"]')
                assert card.locator('.copies').text_content() == f"{duplicate['copies']} copies"
                assert len(duplicate['others']) == duplicate['copies'] - 1
                for other in duplicate['others']:
                    assert page.locator(f'.card[data-id="{other["id"]}"] .dupchip').count() == 1
                    open_card(page, item['id'])
                    index = duplicate['others'].index(other)
                    page.locator('.dup-list button').nth(index).click()
                    page.locator('.detail-head .dupchip').wait_for()
                    assert page.locator('figure.page').count() == other['end_page'] - other['start_page'] + 1
            for number in (151, 154):
                item = next(i for i in all_items if i['start_page'] <= number <= i['end_page'])
                open_card(page, item['id'])
                figure = page.locator('figure.page').filter(has=page.get_by_alt_text(f'Page {number}', exact=True))
                figure.scroll_into_view_if_needed()
                figure.locator('summary').click()
                assert figure.locator('pre').is_visible()
                assert len(figure.locator('pre').text_content()) > 0
                with page.expect_popup() as popup:
                    figure.locator('a').click()
                popup.value.wait_for_load_state()
                assert popup.value.url.endswith(f'/{number:04d}.jpg')
                popup.value.close()
            page.locator('#filter-dups').uncheck()
            wait_cards(page, len(visible))
            for item in all_items:
                if item['duplicate'] and not item['duplicate']['is_canonical']:
                    assert page.locator(f'.card[data-id="{item["id"]}"]').count() == 0
            chosen = visible[0]
            page.locator('#filter-type').select_option(chosen['record_type'])
            wait_cards(page, sum(i['record_type'] == chosen['record_type'] for i in visible))
            page.locator('#filter-type').select_option('')
            page.locator('#filter-doc').select_option(chosen['doc_id'])
            wait_cards(page, sum(i['doc_id'] == chosen['doc_id'] for i in visible))
            page.locator('#filter-doc').select_option('')
            wait_cards(page, len(visible))
            card = page.locator(f'.card[data-id="{chosen["id"]}"]')
            card.focus()
            card.press('Enter')
            title = page.locator('.edit-form [name="title"]')
            title.wait_for()
            marker = 'Synthetic reviewer edit for persistence rehearsal'
            title.fill(marker)
            edits = {'title': marker, 'date_of_service': '2099-01-02',
                     'provider': 'Synthetic reviewer provider',
                     'summary': 'Synthetic summary edit for persistence rehearsal.'}
            for name, value in edits.items():
                page.locator(f'.edit-form [name="{name}"]').fill(value)
            page.locator('.edit-form [name=record_type]').select_option('other')
            edits['record_type'] = 'other'
            title.focus()
            title.press('Escape')
            assert title.input_value() == marker
            polls = []
            page.on('response', lambda r: polls.append(r.status) if r.url.endswith('/api/documents') else None)
            page.wait_for_timeout(3200)
            assert len(polls) >= 2 and title.input_value() == marker
            page.get_by_role('button', name='Save', exact=True).click()
            eventually(lambda: server.client.get(f"/api/records/{chosen['id']}").json()['title'] == marker)
            page.locator('.detail-head .edited').wait_for()
            page.reload()
            # Editing date/type recomputes membership. Fake mode has no new
            # confirmation, so previously near-grouped copies can become visible.
            wait_cards(page, len(server.client.get('/api/timeline').json()))
            open_card(page, chosen['id'])
            for name, value in edits.items():
                assert page.locator(f'.edit-form [name="{name}"]').input_value() == value
            server.stop()
            server.start()
            page.reload()
            wait_cards(page, len(server.client.get('/api/timeline').json()))
            open_card(page, chosen['id'])
            for name, value in edits.items():
                assert page.locator(f'.edit-form [name="{name}"]').input_value() == value
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='Reset to model version', exact=True).click()
            eventually(lambda: not server.client.get(f"/api/records/{chosen['id']}").json()['edited'])
            page.wait_for_function("() => document.querySelector('.edit-form [name=reset]').disabled")
            for name in edits:
                assert page.locator(f'.edit-form [name="{name}"]').input_value() == (chosen['model'][name] or '')
            page.get_by_role('button', name='Close', exact=True).focus()
            page.keyboard.press('Escape')
            assert page.locator('.edit-form').count() == 0
            assert server.calls() == {}, 'Full188 must not invoke extraction or summaries'
            assert errors == []
            page.close()


def test_two_uploads_duplicates_and_polling_while_typing(browser):
    from playwright.sync_api import expect

    with scratch('uploads-browser-') as directory, Server(directory, MRT_TEST_DELAY='0.15') as server:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.goto(server.url)
        page.locator('#file-input').set_input_files({
            'name': 'synthetic-a.pdf', 'mimeType': 'application/pdf', 'buffer': synthetic_pdf(4, 'First fixture')})
        eventually(lambda: len(server.client.get('/api/documents').json()) == 1)
        first_doc = server.client.get('/api/documents').json()[0]['id']
        server.wait_status(first_doc, 'done')
        wait_cards(page, 1)
        record_id = server.client.get('/api/timeline').json()[0]['id']
        open_card(page, record_id)
        title = page.locator('.edit-form [name=title]')
        title.fill('Draft must survive upload and polling')
        page.locator('#file-input').set_input_files({
            'name': 'synthetic-b.pdf', 'mimeType': 'application/pdf', 'buffer': synthetic_pdf(4, 'Second fixture')})
        eventually(lambda: len(server.client.get('/api/documents').json()) == 2)
        second_doc = next(d['id'] for d in server.client.get('/api/documents').json() if d['id'] != first_doc)
        server.wait_status(second_doc, 'done')
        page.locator('#doc-list .status-done').nth(1).wait_for()
        assert title.input_value() == 'Draft must survive upload and polling'
        # The fake adapter intentionally returns identical text for both PDFs.
        page.locator('.badge.copies').wait_for()
        assert page.locator('.badge.copies').first.text_content() == '2 copies'
        wait_cards(page, 1)
        expect(page.locator('#timeline-count')).to_have_text('1 records · 1 duplicate copies hidden · 0 edited')
        page.locator('.card button.copies').click()
        expect(page.locator('#filter-dups')).to_be_checked()
        wait_cards(page, 2)
        expect(page.locator('#timeline-count')).to_have_text('2 records · 0 duplicate copies hidden · 0 edited')
        page.locator('#filter-dups').uncheck()
        wait_cards(page, 1)
        open_card(page, record_id)
        page.locator('.dup-list button').click()
        page.locator('.detail-head .dupchip').wait_for()
        assert page.locator('figure.page').count() == 4
        page.locator('#filter-dups').check()
        wait_cards(page, 2)
        page.locator('[name=title]').fill('Edited copy')
        page.get_by_role('button', name='Save', exact=True).click()
        expect(page.locator('#detail h2')).to_have_text('Edited copy')
        expect(page.locator('#timeline-count')).to_have_text('2 records · 0 duplicate copies hidden · 1 edited')
        page.get_by_role('button', name='Hide from timeline', exact=True).click()
        wait_cards(page, 1)
        expect(page.locator('#timeline-count')).to_have_text('1 records · 0 duplicate copies hidden · 0 edited')
        page.locator('#filter-hidden').check()
        wait_cards(page, 2)
        expect(page.locator('#timeline-count')).to_have_text('2 records · 0 duplicate copies hidden · 1 edited')
        page.get_by_role('button', name='Unhide', exact=True).click()
        expect(page.get_by_role('button', name='Hide from timeline', exact=True)).to_be_visible()
        # An explicit badge click widens a source filter when the copy is in another PDF.
        page.locator('#filter-hidden').uncheck()
        page.locator('#filter-dups').uncheck()
        page.locator('#filter-doc').select_option(first_doc)
        wait_cards(page, 1)
        page.locator('.card button.copies').click()
        wait_cards(page, 2)
        expect(page.locator('#filter-doc')).to_have_value('')
        expect(page.locator('#filter-dups')).to_be_checked()
        expect(page.locator('.card.dup')).to_be_focused()
        expect(page.locator('.card.dup')).to_be_in_viewport()
        page.close()


def test_browser_same_process_retry(browser):
    port = int(os.environ.get('MRT_TEST_RETRY_PORT', '0')) or None
    with scratch('retry-browser-') as directory, Server(directory, port=port, MRT_FAIL_AT_PAGE='5') as server:
        page = browser.new_page()
        page.goto(server.url)
        page.locator('#file-input').set_input_files({
            'name': 'synthetic-retry.pdf', 'mimeType': 'application/pdf', 'buffer': synthetic_pdf()})
        page.get_by_role('button', name='Retry', exact=True).wait_for()
        assert '4/12' in page.locator('#doc-list').text_content()
        assert page.locator('#doc-list .stage').text_content() == 'Failed'
        assert len(server.rows("SELECT page_no FROM pages WHERE status='done'")) == 4
        pid = server.process.pid
        page.get_by_role('button', name='Retry', exact=True).click()
        page.locator('#doc-list .status-done').wait_for()
        assert server.process.pid == pid
        rows = server.rows('SELECT status,attempts,page_no FROM pages')
        assert len(rows) == 12 and all(r['status'] == 'done' for r in rows)
        assert all(r['attempts'] == 1 for r in rows if r['page_no'] < 5)
        counts = [n for (kind, _), n in server.calls().items() if kind == 'vision']
        assert len(counts) == 12 and set(counts) == {1}
        page.close()


def test_known_date_miss_reviewer_correction_survives_restart(browser):
    """Explicit demo acceptance: correct r24 in a disposable copy, then restore it."""
    source = os.environ.get('MRT_UI_DATA_SOURCE')
    if not source:
        pytest.skip('Known-date rehearsal requires MRT_UI_DATA_SOURCE')
    with scratch('date-correction-browser-') as directory:
        copy_completed_store(Path(source), directory / 'store')
        with Server(directory) as server:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(server.url)
            items = server.client.get('/api/timeline?include_duplicates=1').json()
            record = next(i for i in items if (i['start_page'], i['end_page']) == (151, 152))
            original = record['model']['date_of_service']
            page.locator('#filter-dups').check()
            wait_cards(page, len(items))
            open_card(page, record['id'])
            page.locator('.edit-form [name=date_of_service]').fill('2024-10-02')
            page.get_by_role('button', name='Save', exact=True).click()
            path = f"/api/records/{record['id']}"
            eventually(lambda: server.client.get(path).json()['date_of_service'] == '2024-10-02')
            assert server.client.get(path).json()['model']['date_of_service'] == original
            page.reload()
            open_card(page, record['id'])
            assert page.locator('.edit-form [name=date_of_service]').input_value() == '2024-10-02'
            server.stop()
            server.start()
            page.reload()
            open_card(page, record['id'])
            persisted = server.client.get(path).json()
            assert persisted['edited'] and persisted['date_of_service'] == '2024-10-02'
            assert persisted['model']['date_of_service'] == original
            assert page.locator('.edit-form [name=date_of_service]').input_value() == '2024-10-02'
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='Reset to model version', exact=True).click()
            eventually(lambda: not server.client.get(path).json()['edited'])
            assert server.client.get(path).json()['date_of_service'] == original
            assert server.calls() == {}, 'Known-date demo must reuse every real extraction/summary'
            page.close()


def test_calendar_dates_detail_clamp_and_progress_labels(browser):
    """Exercise display contracts in a real LA browser, using controlled API data."""
    from playwright.sync_api import expect

    with scratch('polish-browser-') as directory, Server(directory) as server:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000},
                                timezone_id='America/Los_Angeles')
        summary = 'This is the complete synthetic source summary. ' * 60 + 'Final finding remains available.'
        record = dict(id='calendar', doc_id='fixture', filename='synthetic.pdf', start_page=1,
                      end_page=1, title='Effective reviewer title', date_of_service='2025-12-29',
                      date_confidence='approximate', record_type='office_visit', provider=None,
                      summary=summary, edited=True, model={'title': 'Original model title'}, pages=[])
        unknown = {**record, 'id': 'unknown', 'date_of_service': None,
                   'date_confidence': 'unknown', 'edited': False}
        documents = [dict(id='fixture', filename='synthetic.pdf', status='queued',
                          stage_done=3, stage_total=12, page_count=12)]
        page.route('**/api/documents', lambda route: route.fulfill(json=documents))
        page.route('**/api/timeline?*', lambda route: route.fulfill(json=[record, unknown]))
        page.route('**/api/records/calendar', lambda route: route.fulfill(json=record))
        page.route('**/api/records/unknown', lambda route: route.fulfill(json=unknown))
        page.goto(server.url)
        wait_cards(page, 2)
        expect(page.locator('.month').first).to_have_text('December 2025')
        assert page.locator('.month').first.evaluate('(el) => getComputedStyle(el).textTransform') == 'none'
        expect(page.locator('.month').last).to_have_text('Undated')
        expect(page.locator('.card[data-id=calendar] .date')).to_have_text('~Dec 29, 2025')
        expect(page.locator('.card[data-id=unknown] .date')).to_have_text('undated')
        expect(page.locator('#timeline-count')).to_have_text('2 records · 0 duplicate copies hidden · 1 edited')
        card_summary = page.locator('.card[data-id=calendar] .summary')
        assert card_summary.evaluate('(el) => getComputedStyle(el).webkitLineClamp') == '3'
        assert card_summary.evaluate('(el) => getComputedStyle(el).overflow') == 'hidden'
        assert card_summary.evaluate('(el) => el.scrollHeight > el.clientHeight')
        open_card(page, 'calendar')
        expect(page.locator('#detail h2')).to_have_text('Effective reviewer title')
        expect(page.locator('.record-date')).to_have_text('~Dec 29, 2025 · Approximate date')
        expect(page.locator('[name=date_of_service]')).to_have_value('2025-12-29')
        expect(page.locator('[name=summary]')).to_have_value(summary)
        assert page.locator('[name=summary]').evaluate('(el) => getComputedStyle(el).webkitLineClamp') == 'none'
        expect(page.locator('.model-value .v')).to_have_text('Original model title')
        open_card(page, 'unknown')
        expect(page.locator('.record-date')).to_have_text('undated · Date unknown')
        labels = [('queued', 'Queued'), ('rendering', 'Rendering pages'),
                  ('extracting', 'Reading pages 3/12'), ('grouping', 'Grouping records'),
                  ('summarizing', 'Writing summaries 3/12'), ('deduping', 'Checking duplicates'),
                  ('done', 'Done'), ('failed', 'Failed'), ('interrupted', 'Interrupted')]
        for status, label in labels:
            documents[0]['status'] = status
            expect(page.locator('#doc-list .stage')).to_have_text(label)
            row = page.locator('#doc-list .stage').locator('..')
            expect(row).to_have_text(label + ('3/12Retry' if status in ('failed', 'interrupted') else ''))
        assert server.calls() == {}
        page.close()


def test_duplicate_badge_waits_for_pending_timeline_and_preserves_filters(browser):
    from playwright.sync_api import expect
    from urllib.parse import parse_qs, urlparse

    with scratch('copy-navigation-browser-') as directory, Server(directory) as server:
        page = browser.new_page(viewport={'width': 1440, 'height': 700})
        base = dict(doc_id='fixture', filename='synthetic.pdf', start_page=1, end_page=1,
                    title='Synthetic record', date_of_service='2025-12-29', date_confidence='exact',
                    record_type='office_visit', summary='Synthetic text. ' * 30, pages=[])
        copy = {**base, 'id': 'copy', 'duplicate': {'is_canonical': False}}
        canonical = {**base, 'id': 'canonical', 'duplicate': {
            'is_canonical': True, 'copies': 2, 'others': [{'id': 'copy'}, {'id': 'copy'}]}}
        fillers = [{**base, 'id': f'filler-{i}'} for i in range(12)]
        held = []
        hold_next = [False]

        def timeline(route):
            query = parse_qs(urlparse(route.request.url).query)
            items = [canonical, *fillers]
            if query.get('include_duplicates') == ['1']:
                items.append(copy)
            if hold_next[0]:
                hold_next[0] = False
                held.append((route, items))
            else:
                route.fulfill(json=items)

        page.route('**/api/documents', lambda r: r.fulfill(json=[{
            'id': 'fixture', 'filename': 'synthetic.pdf', 'status': 'done'}]))
        page.route('**/api/timeline?*', timeline)
        page.route('**/api/records/canonical', lambda r: r.fulfill(json=canonical))
        page.goto(server.url)
        wait_cards(page, 13)
        expect(page.locator('#timeline-count')).to_have_text('13 records · 1 duplicate copies hidden · 0 edited')
        open_card(page, 'canonical')
        page.locator('[name=title]').fill('Unsaved selection stays intact')
        page.locator('#filter-type').select_option('office_visit')
        page.locator('#filter-doc').select_option('fixture')
        page.wait_for_timeout(100)
        hold_next[0] = True
        page.locator('#sort-btn').click()
        page.wait_for_timeout(100)
        assert held
        badge = page.locator('.card[data-id=canonical] button.copies')
        badge.focus()
        badge.press('Enter')
        expect(page.locator('#filter-dups')).to_be_checked()
        route, items = held.pop()
        route.fulfill(json=items)
        wait_cards(page, 14)
        expect(page.locator('#timeline-count')).to_have_text('14 records · 0 duplicate copies hidden · 0 edited')
        expect(page.locator('.card[data-id=copy]')).to_be_focused()
        expect(page.locator('.card[data-id=copy]')).to_be_in_viewport()
        expect(page.locator('[name=title]')).to_have_value('Unsaved selection stays intact')
        expect(page.locator('.card[data-id=canonical]')).to_have_class('card selected')
        expect(page.locator('#filter-type')).to_have_value('office_visit')
        expect(page.locator('#filter-doc')).to_have_value('fixture')
        badge.click()
        expect(page.locator('.card[data-id=copy]')).to_be_focused()
        expect(page.locator('#filter-dups')).to_be_checked()
        badge.focus()
        badge.press('Space')
        expect(page.locator('.card[data-id=copy]')).to_be_focused()
        expect(page.locator('#filter-dups')).to_be_checked()
        expect(page.locator('[name=title]')).to_have_value('Unsaved selection stays intact')
        assert server.calls() == {}
        page.close()
