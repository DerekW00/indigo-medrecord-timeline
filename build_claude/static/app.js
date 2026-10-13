/* Medical Record Timeline UI. Vanilla JS, fetch + polling, no framework.
   Every piece of text goes through textContent / createTextNode (see h()), never innerHTML. */
'use strict';

const POLL_MS = 1500;
const EDITABLE = ['title', 'date_of_service', 'record_type', 'provider', 'summary'];
const TYPE_LABELS = {
  progress_report: 'Progress report (PR-2)',
  permanent_stationary_report: 'P&S report (PR-4)',
  office_visit: 'Office visit',
  consultation: 'Consultation',
  operative_report: 'Operative report',
  hospital_record: 'Hospital record',
  imaging_report: 'Imaging report',
  imaging_order: 'Imaging order',
  physical_therapy: 'Physical therapy',
  lab_result: 'Lab result',
  medication: 'Medication',
  work_status: 'Work status',
  correspondence: 'Correspondence',
  cover_sheet: 'Cover sheet',
  billing: 'Billing',
  form_other: 'Other form',
  other: 'Other',
};
const DOC_TYPES = Object.keys(TYPE_LABELS);

const state = {
  documents: [],
  docSignature: null,      // null forces the first poll to render + load the timeline
  timelineSignature: null,
  items: [],
  seenTypes: new Set(),
  filters: { type: '', docId: '', includeDuplicates: false, showHidden: false, sort: 'asc' },
  selectedId: null,
  record: null,            // the record currently shown in the detail panel
  pollInFlight: false,
  serverDown: false,
  timelineInFlight: false,
  timelineDirty: false,
  pendingCopies: null,
};

// ---------- tiny DOM helpers ----------

function $(sel) { return document.querySelector(sel); }

/** h('div', {class: 'x', onclick: fn}, 'text', childNode, [more]) -> element. Strings become text nodes. */
function h(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (v === true) node.setAttribute(k, '');
      else node.setAttribute(k, String(v));
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function safeUrl(url) {
  const s = String(url || '');
  return /^(\/|https?:\/\/)/.test(s) ? s : '#';
}

// ---------- formatting (pure) ----------

function typeLabel(t) { return t ? (TYPE_LABELS[t] || t) : 'unknown type'; }

function pageRange(it) {
  const a = it.start_page, b = it.end_page;
  return a === b || b == null ? `p. ${a}` : `p. ${a}-${b}`;
}

function sourceText(it) { return `${it.filename || it.doc_id || '?'} ${pageRange(it)}`; }

// Format calendar components directly: never interpret a service date as an instant.
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
function calendarDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
  if (!match || !MONTHS[Number(match[2]) - 1]) return null;
  return { year: match[1], month: MONTHS[Number(match[2]) - 1], day: Number(match[3]) };
}

function dateText(it) {
  if (!it.date_of_service) return 'undated';
  const date = calendarDate(it.date_of_service);
  const label = date ? `${date.month.slice(0, 3)} ${date.day}, ${date.year}` : it.date_of_service;
  return (it.date_confidence === 'approximate' ? '~' : '') + label;
}

function monthKey(it) {
  const m = /^(\d{4})-(\d{2})/.exec(String(it.date_of_service || ''));
  return m ? `${m[1]}-${m[2]}` : null;
}

/** Dated items sorted by date (stable, direction from the sort filter), undated items last in server order. */
function sortItems(items, sort) {
  const dir = sort === 'desc' ? -1 : 1;
  const dated = items.map((it, i) => [it, i]).filter(([it]) => it.date_of_service);
  dated.sort((a, b) => {
    const c = String(a[0].date_of_service).localeCompare(String(b[0].date_of_service));
    return c !== 0 ? c * dir : a[1] - b[1];
  });
  return dated.map(([it]) => it).concat(items.filter((it) => !it.date_of_service));
}

function docSignature(docs) {
  return docs.map((d) => [d.id, d.status, d.stage_done, d.stage_total, d.error || ''].join('|')).join('\n');
}

/** The timeline only changes on a status change, or while records are being written (summarizing, deduping). */
function timelineSignature(docs) {
  return docs.map((d) => {
    const live = d.status === 'summarizing' || d.status === 'deduping';
    return d.id + ':' + d.status + (live ? ':' + d.stage_done : '');
  }).join('\n');
}

// ---------- API ----------

async function api(method, url, body, isForm) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText || 'request failed';
    try {
      const j = await res.json();
      if (j && j.detail !== undefined) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      else if (j && j.error) detail = String(j.error);
    } catch (e) { /* non-JSON error body */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

// ---------- documents (top bar) ----------

async function pollDocuments() {
  if (state.pollInFlight) return;
  state.pollInFlight = true;
  try {
    const raw = await api('GET', '/api/documents');
    const docs = Array.isArray(raw) ? raw : (raw && raw.documents) || [];
    if (state.serverDown) { state.serverDown = false; setUploadStatus(''); }
    const sig = docSignature(docs);
    if (sig !== state.docSignature) {
      state.docSignature = sig;
      state.documents = docs;
      renderDocuments();
      renderSourceOptions();
    }
    const tsig = timelineSignature(docs);
    if (tsig !== state.timelineSignature) {
      state.timelineSignature = tsig;
      await loadTimeline();
    }
  } catch (err) {
    state.serverDown = true;
    setUploadStatus('Server unreachable: ' + err.message, true);
  } finally {
    state.pollInFlight = false;
  }
}

function renderDocuments() {
  const root = $('#doc-list');
  clear(root);
  for (const d of state.documents) {
    const total = Number(d.stage_total) || 0;
    const done = Number(d.stage_done) || 0;
    const pct = d.status === 'done' ? 100 : total ? Math.min(100, Math.round(100 * done / total)) : 0;
    const canRetry = d.status === 'failed' || d.status === 'interrupted';
    root.append(h('li', { class: 'doc status-' + (d.status || 'unknown'), dataset: { id: d.id } },
      h('div', { class: 'doc-row' },
        h('span', { class: 'doc-name', title: d.filename }, d.filename || d.id),
        d.page_count ? h('span', { class: 'muted' }, `${d.page_count} pages`) : null,
      ),
      h('div', { class: 'progress', role: 'progressbar', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 },
        h('div', { class: 'progress-bar', style: `width:${pct}%` })),
      h('div', { class: 'doc-row' },
        h('span', { class: 'stage' }, ({
          queued: 'Queued', rendering: 'Rendering pages', extracting: `Reading pages ${done}/${total}`,
          grouping: 'Grouping records', summarizing: `Writing summaries ${done}/${total}`,
          deduping: 'Checking duplicates', done: 'Done', failed: 'Failed', interrupted: 'Interrupted',
        })[d.status] || 'Unknown'),
        canRetry && total ? h('span', { class: 'muted' }, `${done}/${total}`) : null,
        canRetry ? h('button', { type: 'button', class: 'btn small', onclick: () => retryDocument(d.id) }, 'Retry') : null,
      ),
      d.error ? h('div', { class: 'error', title: d.error }, d.error) : null,
    ));
  }
}

async function retryDocument(docId) {
  try {
    await api('POST', `/api/documents/${encodeURIComponent(docId)}/retry`);
    setUploadStatus('Retry queued.');
    state.docSignature = null; // force a re-render on the next poll
    await pollDocuments();
  } catch (err) {
    setUploadStatus('Retry failed: ' + err.message, true);
  }
}

function setUploadStatus(text, isError) {
  const node = $('#upload-status');
  node.textContent = text || '';
  node.className = 'status ' + (isError ? 'error' : 'muted');
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
  if (!files.length) { setUploadStatus('Only PDF files are accepted.', true); return; }
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  setUploadStatus(`Uploading ${files.length} file${files.length > 1 ? 's' : ''}…`);
  try {
    const res = await api('POST', '/api/documents', fd, true);
    const n = Array.isArray(res) ? res.length : files.length;
    setUploadStatus(`Queued ${n} document${n === 1 ? '' : 's'}.`);
    state.docSignature = null;
    await pollDocuments();
  } catch (err) {
    setUploadStatus('Upload failed: ' + err.message, true);
  }
}

// ---------- timeline ----------

async function loadTimeline() {
  if (state.timelineInFlight) { state.timelineDirty = true; return; }
  state.timelineInFlight = true;
  try {
    const f = state.filters;
    const params = new URLSearchParams({
      include_duplicates: f.includeDuplicates ? '1' : '0',
      include_hidden: f.showHidden ? '1' : '0',
      type: f.type,
      doc_id: f.docId,
      sort: f.sort,
    });
    const raw = await api('GET', '/api/timeline?' + params.toString());
    if (state.timelineDirty) return; // A newer filter/navigation request supersedes this response.
    state.items = Array.isArray(raw) ? raw : (raw && (raw.items || raw.records)) || [];
    for (const it of state.items) if (it.record_type) state.seenTypes.add(it.record_type);
    renderTypeOptions();
    renderTimeline();
    scrollToCopies();
  } catch (err) {
    const root = $('#timeline');
    clear(root);
    root.append(h('div', { class: 'empty error' }, 'Could not load the timeline: ' + err.message));
  } finally {
    state.timelineInFlight = false;
    if (state.timelineDirty) { state.timelineDirty = false; loadTimeline(); }
  }
}

function renderTypeOptions() {
  const select = $('#filter-type');
  const current = state.filters.type;
  const types = new Set(state.seenTypes);
  if (current) types.add(current);
  const sorted = Array.from(types).sort((a, b) => typeLabel(a).localeCompare(typeLabel(b)));
  clear(select);
  select.append(h('option', { value: '' }, 'All types'));
  for (const t of sorted) select.append(h('option', { value: t, selected: t === current }, typeLabel(t)));
}

function renderSourceOptions() {
  const select = $('#filter-doc');
  const current = state.filters.docId;
  const names = {};
  for (const d of state.documents) names[d.filename] = (names[d.filename] || 0) + 1;
  clear(select);
  select.append(h('option', { value: '' }, 'All PDFs'));
  let found = false;
  for (const d of state.documents) {
    const label = names[d.filename] > 1 ? `${d.filename} (${String(d.id).slice(0, 6)})` : d.filename;
    if (d.id === current) found = true;
    select.append(h('option', { value: d.id, selected: d.id === current }, label));
  }
  if (current && !found) select.append(h('option', { value: current, selected: true }, current));
}

function renderTimeline() {
  const root = $('#timeline');
  clear(root);
  let items = state.items;
  if (!state.filters.showHidden) items = items.filter((it) => !it.hidden);
  const ordered = sortItems(items, state.filters.sort);
  const visibleIds = new Set(ordered.map((it) => it.id));
  const omitted = new Set();
  if (!state.filters.includeDuplicates) {
    for (const it of ordered) {
      if (!it.duplicate || !it.duplicate.is_canonical) continue;
      for (const other of it.duplicate.others || []) {
        if (other.id && !visibleIds.has(other.id)) omitted.add(other.id);
      }
    }
  }
  $('#timeline-count').textContent = `${ordered.length} records · ${omitted.size} duplicate copies hidden · ${ordered.filter((it) => it.edited).length} edited`;
  if (!ordered.length) {
    const msg = state.documents.length ? 'No records to show yet. Records appear as each PDF is summarized.'
      : 'Drop a PDF bundle above to build the timeline.';
    root.append(h('div', { class: 'empty' }, msg));
    return;
  }
  let month = null;
  for (const it of ordered) {
    const key = monthKey(it) || 'Undated';
    if (key !== month) {
      month = key;
      const date = calendarDate(it.date_of_service);
      root.append(h('h3', { class: 'month' }, date ? `${date.month} ${date.year}` : 'Undated'));
    }
    root.append(renderCard(it));
  }
}

function renderCard(it) {
  const dup = it.duplicate || null;
  const classes = ['card'];
  if (it.id === state.selectedId) classes.push('selected');
  if (dup && !dup.is_canonical) classes.push('dup');
  if (it.hidden) classes.push('hidden-item');
  const open = () => selectRecord(it.id);
  return h('article', {
    class: classes.join(' '), tabindex: 0, dataset: { id: it.id }, onclick: open,
    onkeydown: (e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(); } },
  },
    h('div', { class: 'card-head' },
      h('span', { class: 'date' + (it.date_of_service ? '' : ' undated') }, dateText(it)),
      h('span', { class: 'chip type' }, typeLabel(it.record_type)),
      it.partial_start ? h('span', { class: 'chip partial', title: 'This record starts mid-document' }, 'partial') : null,
      it.edited ? h('span', { class: 'chip edited', title: 'Fields edited by the reviewer' }, 'edited') : null,
      it.hidden ? h('span', { class: 'chip hidden' }, 'hidden') : null,
      dup && dup.is_canonical && dup.copies > 1 ? h('button', { type: 'button', class: 'badge copies', title: 'Show duplicate copies',
        onclick: (e) => { e.stopPropagation(); revealCopies(dup.others || []); },
        onkeydown: (e) => e.stopPropagation(),
      }, `${dup.copies} copies`) : null,
      dup && !dup.is_canonical ? h('span', { class: 'chip dupchip', title: 'Duplicate of another record' }, 'duplicate') : null,
    ),
    h('h4', { class: 'title' }, it.title || '(untitled record)'),
    h('div', { class: 'meta' },
      it.provider ? h('span', { class: 'provider' }, it.provider) : null,
      h('span', { class: 'source' }, sourceText(it)),
    ),
    h('p', { class: 'summary' }, it.summary || ''),
  );
}

function revealCopies(others) {
  state.filters.includeDuplicates = true;
  $('#filter-dups').checked = true;
  state.pendingCopies = { ids: others.map((other) => other.id), widened: false };
  loadTimeline();
}

function scrollToCopies() {
  if (!state.pendingCopies) return;
  const pending = state.pendingCopies;
  const ids = pending.ids;
  const card = Array.from(document.querySelectorAll('#timeline .card'))
    .find((node) => ids.includes(node.dataset.id));
  // An explicit copy request may widen source/type filters once to reveal its target.
  if (!card && !pending.widened && (state.filters.docId || state.filters.type)) {
    pending.widened = true;
    state.filters.docId = '';
    state.filters.type = '';
    renderSourceOptions();
    renderTypeOptions();
    loadTimeline();
    return;
  }
  state.pendingCopies = null;
  if (card) {
    card.scrollIntoView({ block: 'center' });
    card.focus({ preventScroll: true });
  }
}

function highlightSelected() {
  for (const card of document.querySelectorAll('.card')) {
    card.classList.toggle('selected', card.dataset.id === state.selectedId);
  }
}

// ---------- detail panel ----------

async function selectRecord(id) {
  state.selectedId = id;
  highlightSelected();
  const root = $('#detail');
  clear(root);
  root.append(h('div', { class: 'empty' }, 'Loading record…'));
  try {
    const rec = await api('GET', `/api/records/${encodeURIComponent(id)}`);
    if (state.selectedId !== id) return; // the user clicked another card meanwhile
    state.record = rec;
    renderDetail(rec);
  } catch (err) {
    clear(root);
    root.append(h('div', { class: 'empty error' }, 'Could not load the record: ' + err.message));
  }
}

async function refreshRecord(id) {
  const rec = await api('GET', `/api/records/${encodeURIComponent(id)}`);
  if (state.selectedId !== id) return;
  state.record = rec;
  renderDetail(rec);
}

function closeDetail() {
  state.selectedId = null;
  state.record = null;
  highlightSelected();
  const root = $('#detail');
  clear(root);
  root.append(h('div', { class: 'empty' }, 'Select a record on the timeline to read its pages and edit its fields.'));
}

function section(title, ...children) {
  return h('div', { class: 'section' }, h('h3', { class: 'section-title' }, title), ...children);
}

/** A labeled control; shows the model's value underneath when the effective value differs from it. */
function field(name, label, control, rec, note) {
  const model = rec.model || {};
  const wrap = h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), control);
  const hasModel = Object.prototype.hasOwnProperty.call(model, name);
  if (rec.edited && hasModel && (model[name] ?? '') !== (rec[name] ?? '')) {
    wrap.append(h('span', { class: 'model-value' }, 'model value: ',
      h('span', { class: 'v' }, model[name] == null || model[name] === '' ? '(empty)' : String(model[name]))));
  }
  if (note) wrap.append(h('span', { class: 'note' }, note));
  return wrap;
}

function typeSelect(current) {
  const types = DOC_TYPES.slice();
  if (current && !types.includes(current)) types.push(current);
  const select = h('select', { name: 'record_type' });
  if (!current) select.append(h('option', { value: '', selected: true }, '(unknown)'));
  for (const t of types) select.append(h('option', { value: t, selected: t === current }, typeLabel(t)));
  return select;
}

function duplicateMatchText(duplicate) {
  if (duplicate.is_canonical) return 'Shown on the timeline';
  const labels = {
    similar: 'Near duplicate (model uncertain)',
    similar_confirmed: 'Near duplicate (model confirmed)',
    partial_exact: 'Exact partial copy',
    exact_image: 'Exact page-image copy',
    exact: 'Exact text copy',
  };
  const label = labels[duplicate.reason] || 'Duplicate copy';
  const near = duplicate.reason === 'similar' || duplicate.reason === 'similar_confirmed';
  return near && Number.isFinite(duplicate.score)
    ? `${label} · ${(duplicate.score * 100).toFixed(1)}% word-set similarity to the timeline copy`
    : label;
}

function renderDetail(rec) {
  const root = $('#detail');
  clear(root);
  const dup = rec.duplicate || null;
  const pages = Array.isArray(rec.pages) ? rec.pages : [];

  root.append(h('div', { class: 'detail-head' },
    h('div', { class: 'detail-title' },
      h('span', { class: 'source' }, sourceText(rec)),
      h('span', { class: 'chip type' }, typeLabel(rec.record_type)),
      rec.edited ? h('span', { class: 'chip edited' }, 'edited') : null,
      rec.partial_start ? h('span', { class: 'chip partial' }, 'partial') : null,
      rec.hidden ? h('span', { class: 'chip hidden' }, 'hidden') : null,
      dup && dup.is_canonical && dup.copies > 1 ? h('span', { class: 'badge copies' }, `${dup.copies} copies`) : null,
      dup && !dup.is_canonical ? h('span', { class: 'chip dupchip' }, 'duplicate') : null,
    ),
    h('button', { type: 'button', class: 'icon-btn', title: 'Close', 'aria-label': 'Close', onclick: closeDetail }, '×'),
  ));

  root.append(h('h2', { class: 'record-heading' }, rec.title || '(untitled record)'));
  root.append(h('p', { class: 'record-date muted' }, `${dateText(rec)} · ${
    !rec.date_of_service ? 'Date unknown' : rec.date_confidence === 'approximate'
      ? 'Approximate date' : rec.date_confidence === 'exact' ? 'Exact date' : 'Date confidence unknown'}`));

  const dateNote = rec.date_confidence === 'approximate' ? 'Approximate: taken from a report or fax date.'
    : rec.date_confidence === 'unknown' ? 'No date found on the pages.' : null;
  const form = h('form', { class: 'edit-form', onsubmit: onSave },
    field('title', 'Title', h('input', { name: 'title', type: 'text', value: rec.title || '', maxlength: 200 }), rec),
    field('date_of_service', 'Date of service',
      h('input', { name: 'date_of_service', type: 'text', value: rec.date_of_service || '', placeholder: 'YYYY-MM-DD',
        pattern: '\\d{4}-\\d{2}-\\d{2}', title: 'YYYY-MM-DD' }), rec, dateNote),
    field('record_type', 'Type', typeSelect(rec.record_type), rec),
    field('provider', 'Provider', h('input', { name: 'provider', type: 'text', value: rec.provider || '' }), rec),
    field('summary', 'Summary', h('textarea', { name: 'summary', rows: 7 }, rec.summary || ''), rec),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn primary', name: 'save' }, 'Save'),
      h('button', { type: 'button', class: 'btn', name: 'reset', disabled: !rec.edited, onclick: onReset }, 'Reset to model version'),
      h('button', { type: 'button', class: 'btn', name: 'hide', onclick: onToggleHidden }, rec.hidden ? 'Unhide' : 'Hide from timeline'),
      h('span', { class: 'status muted', name: 'form-status' }),
    ),
    h('div', { class: 'form-hint' }, 'Edits are saved as overrides and survive a restart. An empty field goes back to the model value.'),
  );
  root.append(section('Record', form));

  const dl = h('dl', { class: 'kv' },
    h('dt', {}, 'Facility'), h('dd', {}, rec.facility || '—'),
    h('dt', {}, 'Pages'), h('dd', {}, `${rec.start_page}–${rec.end_page} of ${rec.filename || rec.doc_id || '?'}`),
    h('dt', {}, 'Boundary reason'), h('dd', {}, rec.boundary_reason || '—'),
    h('dt', {}, 'Date confidence'), h('dd', {}, rec.date_confidence || '—'),
  );
  root.append(section('Why this record starts here', dl));

  const findings = Array.isArray(rec.key_findings) ? rec.key_findings : [];
  root.append(section('Key findings', findings.length
    ? h('ul', {}, findings.map((f) => h('li', {}, String(f))))
    : h('p', { class: 'muted' }, 'None listed.')));

  const flags = Array.isArray(rec.flags) ? rec.flags : [];
  if (flags.length) root.append(section('Flags', h('div', { class: 'chips' }, flags.map((f) => h('span', { class: 'chip' }, String(f))))));

  if (dup) {
    const others = Array.isArray(dup.others) ? dup.others : [];
    root.append(section('Duplicates',
      h('p', {}, dup.is_canonical
        ? `${dup.copies} copies of this record were found; this one is shown on the timeline.`
        : 'This record is a copy of another record. It is hidden from the timeline unless "Show duplicates" is on.'),
      !dup.is_canonical ? h('p', {}, duplicateMatchText(dup)) : null,
      others.length ? h('ul', { class: 'dup-list' }, others.map((o) =>
        h('li', {}, h('button', { type: 'button', class: 'link', onclick: () => selectRecord(o.id) }, sourceText(o)),
          h('span', { class: 'muted' }, ` — ${duplicateMatchText(o)}`)))) : null,
    ));
  }

  const pageNodes = pages.map((p) => {
    const url = safeUrl(p.image_url);
    return h('figure', { class: 'page' },
      h('a', { href: url, target: '_blank', rel: 'noopener', title: 'Open full size in a new tab' },
        h('img', { src: url, loading: 'lazy', alt: `Page ${p.page_no}` })),
      h('figcaption', {}, h('strong', {}, `Page ${p.page_no}`), p.gist ? ` · ${p.gist}` : ''),
      h('details', { class: 'transcription' },
        h('summary', {}, 'Transcription'),
        h('pre', {}, p.transcription || '(no transcription)')),
    );
  });
  root.append(section(`Pages (${pages.length})`, pageNodes.length ? pageNodes : h('p', { class: 'muted' }, 'No pages.')));
  root.scrollTop = 0;
}

function setFormStatus(form, text, isError) {
  const node = form.querySelector('[name="form-status"]');
  if (!node) return;
  node.textContent = text || '';
  node.className = 'status ' + (isError ? 'error' : 'muted');
}

async function onSave(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const rec = state.record;
  if (!rec) return;
  const patch = {};
  for (const name of EDITABLE) {
    const ctrl = form.elements[name];
    if (!ctrl) continue;
    const raw = String(ctrl.value ?? '').trim();
    const current = rec[name] == null ? '' : String(rec[name]);
    if (raw !== current) patch[name] = raw === '' ? null : raw;
  }
  if (!Object.keys(patch).length) { setFormStatus(form, 'Nothing changed.'); return; }
  const save = form.elements.save;
  if (save) save.disabled = true;
  setFormStatus(form, 'Saving…');
  try {
    await api('PATCH', `/api/records/${encodeURIComponent(rec.id)}`, patch);
    await refreshRecord(rec.id);
    await loadTimeline();
  } catch (err) {
    setFormStatus(form, 'Save failed: ' + err.message, true);
    if (save) save.disabled = false;
  }
}

async function onReset(e) {
  const rec = state.record;
  if (!rec) return;
  if (!window.confirm('Discard your edits and restore the model version of this record?')) return;
  const form = e.currentTarget.closest('form');
  try {
    await api('DELETE', `/api/records/${encodeURIComponent(rec.id)}/overrides`);
    await refreshRecord(rec.id);
    await loadTimeline();
  } catch (err) {
    if (form) setFormStatus(form, 'Reset failed: ' + err.message, true);
  }
}

async function onToggleHidden(e) {
  const rec = state.record;
  if (!rec) return;
  const form = e.currentTarget.closest('form');
  try {
    await api('PATCH', `/api/records/${encodeURIComponent(rec.id)}`, { hidden: !rec.hidden });
    await refreshRecord(rec.id);
    await loadTimeline();
  } catch (err) {
    if (form) setFormStatus(form, 'Update failed: ' + err.message, true);
  }
}

// ---------- wiring ----------

function updateSortButton() {
  $('#sort-btn').textContent = state.filters.sort === 'desc' ? 'Newest first' : 'Oldest first';
}

function init() {
  const dropzone = $('#dropzone');
  const input = $('#file-input');
  $('#browse-btn').addEventListener('click', (e) => { e.stopPropagation(); input.click(); });
  dropzone.addEventListener('click', () => input.click());
  dropzone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  input.addEventListener('change', () => { uploadFiles(input.files); input.value = ''; });
  for (const type of ['dragenter', 'dragover']) {
    dropzone.addEventListener(type, (e) => { e.preventDefault(); dropzone.classList.add('over'); });
  }
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('over'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('over');
    uploadFiles(e.dataTransfer && e.dataTransfer.files);
  });
  // A PDF dropped outside the zone must not navigate the page away.
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());

  $('#filter-type').addEventListener('change', (e) => { state.filters.type = e.target.value; loadTimeline(); });
  $('#filter-doc').addEventListener('change', (e) => { state.filters.docId = e.target.value; loadTimeline(); });
  $('#filter-dups').addEventListener('change', (e) => { state.filters.includeDuplicates = e.target.checked; loadTimeline(); });
  $('#filter-hidden').addEventListener('change', (e) => { state.filters.showHidden = e.target.checked; loadTimeline(); });
  $('#sort-btn').addEventListener('click', () => {
    state.filters.sort = state.filters.sort === 'asc' ? 'desc' : 'asc';
    updateSortButton();
    loadTimeline();
  });
  updateSortButton();
  document.addEventListener('keydown', (e) => {
    const tag = document.activeElement ? document.activeElement.tagName : '';
    const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    if (e.key === 'Escape' && state.selectedId && !typing) closeDetail();
  });

  pollDocuments();
  setInterval(pollDocuments, POLL_MS);
}

if (typeof document !== 'undefined' && typeof window !== 'undefined' && !window.__MRT_TEST__) {
  document.addEventListener('DOMContentLoaded', init);
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { state, h, typeLabel, pageRange, sourceText, dateText, monthKey, sortItems, docSignature,
    timelineSignature, pollDocuments, loadTimeline, renderDocuments, renderTimeline, renderDetail, selectRecord,
    onSave, onReset, onToggleHidden, uploadFiles, init, renderTypeOptions, renderSourceOptions, closeDetail };
}
