#!/usr/bin/env bash
# One-time setup of the Indigo build on the VPS.
# Run from the folder that holds CLAUDE.md and record_1.pdf:   bash bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
test -f CLAUDE.md || { echo "CLAUDE.md is missing in $ROOT"; exit 1; }

# 1. Layout
mkdir -p samples prompts .claude eval/gold
shopt -s nullglob
for f in *.pdf; do mv "$f" samples/; done
cp CLAUDE.md AGENTS.md   # Codex reads AGENTS.md, Claude Code reads CLAUDE.md

cat > .gitignore <<'EOF'
data
samples
.env
.venv
__pycache__/
*.pyc
eval/results/
.pytest_cache/
EOF

cat > .env.example <<'EOF'
OPENAI_API_KEY=
OPENAI_MODEL=gpt-6-luna
MRT_DATA_DIR=./data
MRT_CONCURRENCY=8
MRT_MAX_PAGES=0
MRT_FAIL_AT_PAGE=0
MRT_RENDER_LONG_SIDE=1600
MRT_PORT=8000
EOF

cat > requirements.txt <<'EOF'
fastapi>=0.115
uvicorn[standard]>=0.30
pymupdf>=1.24
openai>=1.50
pydantic>=2.7
python-multipart>=0.0.9
httpx>=0.27
pytest>=8.2
pytest-asyncio>=0.23
EOF

# Claude Code project settings: no permission prompts for the tools the build needs.
cat > .claude/settings.json <<'EOF'
{
  "permissions": {
    "allow": ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"],
    "deny": ["Read(./.env)"]
  }
}
EOF

# 2. Session prompts (paste, or pass with: claude "$(cat prompts/main-claude.md)")
cat > prompts/main-claude.md <<'EOF'
Read CLAUDE.md in full before you do anything else. It is the context pack for this build. Its decisions are final. If one blocks you, make the smallest change that unblocks you and record the assumption in the README section "Assumptions".

Situation: timed build, code freeze 16:30 PT today. Working features only. The sample bundle is samples/record_1.pdf (188 scanned pages, no text layer). OPENAI_API_KEY is in the environment. The model is gpt-6-luna. A virtualenv with requirements.txt already exists in .venv.

You own: app/, scripts/, tests/, README.md. Another agent builds static/ (sections 9 and 10) on branch "ui" in a separate worktree and tests it against its own mock API. Do not create files under static/. The API contract in section 9 is fixed; implement it exactly so the UI plugs in without changes.

Order of work, commit at the end of each milestone with the milestone name in the message:
1. M0: app/config.py, app/llm.py, scripts/probe_model.py. Send page 1 of the sample as an image and ask for JSON. Confirm image input and JSON output work with this model and adapt llm.py to what the API accepts.
2. M1: app/db.py, app/pipeline/*, app/worker.py, scripts/process.py. Run MRT_MAX_PAGES=12 on the sample. Print the spans with boundary reasons, titles and dates. Render pages 1 to 12 and look at them. Compare. Fix rules or prompts before moving on. Write tests/test_group.py with the cases in section 7. Then start the full 188-page run in the background with nohup and keep working.
3. M2: app/api.py and app/__main__.py per section 9. When the "ui" branch is merged into main (I will tell you), serve static/ and verify the page works against the real store.
4. Stop after M2 and report. Phase 2 (editing, retry, dedupe, eval) runs in separate worktrees.

Rules: develop with MRT_MAX_PAGES=12; run pytest before each commit; one page failure never kills a document, one document failure never kills the worker; keep every model call in app/llm.py; log every model call in one line; do not ask me questions unless blocked, make the call and write it in README "Assumptions".

After each milestone report in at most 12 lines: Works (with the command or URL to see it), Next, Assumptions, Time (now, and ahead or behind the section 13 time boxes). If you are more than 20 minutes behind, apply the cut list at the end of section 13 and say which cuts you applied.

Start with M0 now.
EOF

cat > prompts/ui-codex.md <<'EOF'
Read AGENTS.md in full first. It is the context pack for this build; its decisions are final.

You are on branch "ui" in this worktree. You build the front end only: sections 9 (API contract, as the consumer) and 10 (UI). Another agent builds the pipeline and the real API in app/ on branch main. Do not create or edit anything under app/. Files you own: static/index.html, static/app.js, static/style.css, static/fixtures/*.json, scripts/mock_api.py, tests/test_mock_api.py.

Deliver, in this order, and commit after each step:
1. scripts/mock_api.py: a FastAPI app on port MRT_PORT (default 8001) that serves static/ at "/" and implements every route in section 9 from fixture JSON in static/fixtures/. It must simulate progress: after POST /api/documents, the new document moves through rendering, extracting (stage_done counting up to stage_total over about 20 seconds), grouping, summarizing, done. One fixture document must end in status "failed" at page 40 with the error text from section 6, and POST /api/documents/{id}/retry must resume it to done. PATCH /api/records/{id} stores overrides in memory and the GET routes return the merged values with "edited": true. DELETE /api/records/{id}/overrides resets. Serve /pages/{doc_id}/{page_no}.jpg with one placeholder JPEG.
2. static/fixtures/timeline.json with about 25 realistic records that mirror the sample described in section 3: PR-2 progress reports (4 to 6 pages each, dated 2024-10 to 2025-12), one 57-page admission record, one physical therapy note, two imaging reports, a PR-4, one undated record, one approximate date, one partial_start, and one duplicate group with two copies. Summaries of 3 to 5 sentences, plausible but clearly synthetic (no real names).
3. The UI per section 10: drop zone and file input, document list with progress bar, stage name, counts, Retry button and error text; timeline with month headers, cards, filters (type, source PDF, show duplicates), sort toggle; detail panel with inline edit form (Save, Reset), boundary reason, key findings, flags, lazy-loaded page images with the transcription under each. Vanilla JS, fetch, polling every 1.5 s, no build step, no framework, small readable CSS.
4. tests/test_mock_api.py with httpx: upload moves to done, retry resumes a failed document, PATCH then GET shows the edit, reset clears it.

Rules: keep each file small and single purpose; no external JS libraries; the UI must work from a cold "python scripts/mock_api.py" plus a browser at http://127.0.0.1:8001. Do not ask questions; make the call and note assumptions in a short NOTES-ui.md. Report when done in at most 10 lines: what works, the command to run it, assumptions.
EOF

cat > prompts/review-codex.md <<'EOF'
Read AGENTS.md first. You are a reviewer, not an implementer. Do not edit files.

Review the current diff on main against the context pack: git diff <context-pack-commit>..HEAD. Check sections 6 (pipeline), 7 (grouping rules), 8 (duplicates) and 14 (engineering rules) line by line against the code.

Look for, in this order of importance:
1. Checkpoint gaps: any unit of work (page, record) whose result is not written to SQLite the moment it is produced, or any stage that does not skip units already done.
2. Silent failures: exceptions swallowed without a status write and a log line; a page failure that kills a document run; a document failure that kills the worker.
3. Event loop blocking: PyMuPDF rendering, hashing, or sqlite calls in the async path without to_thread, or a sync OpenAI client.
4. Grouping rule mismatches between section 7 and app/pipeline/group.py, including rule order and the partial_start rule.
5. Prompt drift: differences between app/prompts/*.md and the appendices.
6. JSON validation gaps: model output used without pydantic validation and retry.

Output: a ranked list of findings. Each finding: file and line, what is wrong, why it matters, the smallest fix. Then a one-line verdict: merge, or fix first. No praise, no summary of what the code does.
EOF

cat > prompts/edits-retry-claude.md <<'EOF'
Read CLAUDE.md first. You are on branch "edits-retry" in this worktree, started from main after M2. Build bonus features 1 and 2 only (section 11 items 1 and 2): record_overrides and PATCH plus reset, the "edited" state and Reset button in static/app.js, retry and startup resume in app/worker.py, the MRT_FAIL_AT_PAGE demo hook in app/pipeline/extract.py.

Rules: add code in clearly marked sections (comment "# edits-retry" or "// edits-retry"); never reorder or reformat existing code, another branch edits the same files in parallel; schema changes are additive only (CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN); write tests for the resume logic (a document with pages 1 to 39 done resumes at 40); verify the retry demo end to end with MRT_FAIL_AT_PAGE=40 and MRT_MAX_PAGES=50 on the sample; commit when it works; report in at most 10 lines.
EOF

cat > prompts/dedupe-eval-claude.md <<'EOF'
Read CLAUDE.md first. You are on branch "dedupe-eval" in this worktree, started from main after M2. Build bonus features 3 and 4 only (sections 8 and 12): app/pipeline/dedupe.py with tests/test_dedupe.py, the duplicate badge and "show duplicates" toggle in static/app.js, eval/run_eval.py, scripts/export_gold.py, app/prompts/summary_judge.md. The gold file is eval/gold/record_1.json; if it is missing, label pages 1 to 60 yourself from the rendered pages and set labeled_pages to [1, 60].

Rules: add code in clearly marked sections (comment "# dedupe-eval" or "// dedupe-eval"); never reorder or reformat existing code, another branch edits the same files in parallel; schema changes are additive only; run the eval on the warm database of the main checkout (copy its data/ directory, do not re-run the model on 188 pages); write the numbers into README.md under "Eval"; commit when it works; report in at most 10 lines.
EOF

# 3. Git and the first worktree
if [ ! -d .git ]; then
  git init -b main >/dev/null 2>&1 || { git init >/dev/null; git checkout -b main >/dev/null 2>&1 || true; }
fi
git add -A
git commit -q -m "chore: context pack, settings, prompts, gitignore" || true
CONTEXT_COMMIT=$(git rev-parse --short HEAD)

if [ ! -d ../Indigo-ui ]; then
  git worktree add -b ui ../Indigo-ui main >/dev/null
  ln -sfn "$ROOT/samples" ../Indigo-ui/samples
fi

# 4. Virtualenvs (main and ui), so the agents start on code, not on setup
for d in "$ROOT" "$ROOT/../Indigo-ui"; do
  if [ ! -d "$d/.venv" ]; then
    python3 -m venv "$d/.venv"
    "$d/.venv/bin/pip" install -q -U pip
    "$d/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"
  fi
done

echo
echo "Done. Context pack commit: $CONTEXT_COMMIT (use it in prompts/review-codex.md)"
echo "Next: create .env from .env.example with your key, then follow SETUP.md step 3."
