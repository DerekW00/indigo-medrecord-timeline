# Final demo — October 13, 16:30 PT

Freeze is **16:20 PT**. The demo runs from a clean `uv` checkout at `/Users/dw/indigo-demo`, through Mac SSH forwarding to VPS loopback port 8000. The retry server uses port 8006 and a separate synthetic store. No command below re-extracts the full sample.

The live store also contains the completed `record_1_shuffled.pdf` (72 pages) and
`record_1_tail.pdf` (40 pages) uploaded during rehearsal. Their records and reviewer
edits are preserved. The published evaluation below uses the isolated original
188-page sample, not this expanded rehearsal store.

## 1. Start and connect

The prepared servers use tmux windows `final:demo` and `final:retry`. If the main server is stopped, run on the VPS in `final:demo`:

```bash
cd /Users/dw/indigo-demo/build_claude
MRT_DATA_DIR=/Users/dw/indigo-integration/build_claude/data/final/mac-upload-ready \
  MRT_FAKE_LLM=0 OPENAI_MODEL=gpt-6-luna MRT_MAX_PAGES=0 MRT_CONCURRENCY=16 MRT_PORT=8000 \
  .venv/bin/python -m app
```

On the Mac, keep this terminal open:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 -L 8006:127.0.0.1:8006 vps
```

Open http://127.0.0.1:8000. If another tunnel already owns a local port, use that tunnel or close it before starting this one.

## 2. Drop both PDFs and show progress

If the files are not already on the Mac, in another Mac terminal:

```bash
mkdir -p ~/Downloads/indigo-demo
scp vps:/Users/dw/indigo/samples/record_1.pdf ~/Downloads/indigo-demo/
scp vps:/Users/dw/indigo/samples/record_1_part.pdf ~/Downloads/indigo-demo/
scp vps:/Users/dw/indigo-integration/build_claude/data/final/synthetic-retry-12.pdf ~/Downloads/indigo-demo/
open ~/Downloads/indigo-demo
```

Drag `record_1.pdf` and `record_1_part.pdf` together into the main page. The first is a completed cached document. The second is 36 pages selected from original pages 5–40; its extraction is cached after pixel/JPEG equality checks, while grouping and summaries run normally. Show each PDF's progress. On later uploads both completed documents are reused.

Terminal fallback on the VPS, if needed:

```bash
curl --fail --silent --show-error \
  -F files=@/Users/dw/indigo/samples/record_1.pdf \
  -F files=@/Users/dw/indigo/samples/record_1_part.pdf \
  http://127.0.0.1:8000/api/documents
```

## 3. Review the combined timeline

Enable **Show duplicates** to see records from both source PDFs together in ascending date order. Open the pages 37–41 record: it remains one record in the full bundle. Show an image, its transcription, high-level summary, date confidence, and boundary reason. The part PDF ends at original page 40, so its last record is a partial copy of the full source.

## 4. Show duplicates and retained records

Turn Show duplicates off. Inspect these pairs through the copy-source links:

- Original pages 182–184 collapse under 166–178: exact partial image copy.
- Original pages 155–156 collapse under 153–154: near copy, **88.1%** word-set similarity, original validated model decision `uncertain` accepted under the stated rule.
- The part PDF's eight records collapse under their original sources, including its clipped last record.

The two PT visits at original pages 28–31 and 76–78 have different dates and remain separate. The operative parts at 151–152 and 157–165 contain complementary content and remain separate.

## 5. Correct r24, restart, verify, reset

Open original pages **151–152**, set Date of service to **2024-10-02**, and click Save. Refresh the page. On the VPS, restart only the main app in its existing tmux window:

```bash
tmux send-keys -t final:demo C-c
tmux send-keys -t final:demo 'MRT_DATA_DIR=/Users/dw/indigo-integration/build_claude/data/final/mac-upload-ready MRT_FAKE_LLM=0 OPENAI_MODEL=gpt-6-luna MRT_MAX_PAGES=0 MRT_CONCURRENCY=16 MRT_PORT=8000 .venv/bin/python -m app' C-m
```

Reload and verify **2024-10-02** remains with the edited marker. Click **Reset to model version**: the actual cached model date is **2024-11-12**. Neither the model value nor gold was changed by the reviewer override.

## 6. Demonstrate retry from page 6

The prepared `final:retry` server uses this command, with a fresh store:

```bash
cd /Users/dw/indigo-demo/build_claude
MRT_DATA_DIR=/Users/dw/indigo-integration/build_claude/data/final/retry6-ready \
  MRT_FAKE_LLM=1 MRT_CONCURRENCY=1 MRT_FAIL_AT_PAGE=6 MRT_MAX_PAGES=0 MRT_PORT=8006 \
  .venv/bin/python -m app
```

Open http://127.0.0.1:8006 on the Mac. Drop **synthetic-retry-12.pdf**. It is synthetic and does not make OpenAI calls. Show five completed pages and the injected page-6 failure; click Retry **without restarting** that server. It completes 12 pages while pages 1–5 retain their saved results and attempt counts.

To inspect progress from the VPS:

```bash
cd /Users/dw/indigo-demo/build_claude
.venv/bin/python - <<'PYTHON'
import sqlite3
path = '/Users/dw/indigo-integration/build_claude/data/final/retry6-ready/app.db'
with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
    print('page status attempts')
    for row in db.execute('SELECT page_no,status,attempts FROM pages ORDER BY page_no'):
        print(*row)
PYTHON
```

The hook fires once per process. For another full failure rehearsal, prepare a new empty data directory and restart the retry server; do not reuse the completed store and expect it to fail again.

## 7. Show evaluation and gold

On the VPS, from the clean clone:

```bash
cd /Users/dw/indigo-demo/build_claude
sed -n '/^| Metric /,/^$/p' README.md
.venv/bin/python -m json.tool ../eval/results/final-eval.json
.venv/bin/python -m json.tool ../eval/gold/record_1.json
```

State: 31/31 matched gold spans, 188/188 assigned pages, 30/31 dates, 31/31 types, 2/2 duplicate pairs, zero false pairs. The fresh judge supported 468/472 claims, with two unsupported, two unclear, and no call errors. Thirty-five predictions include permitted internal splits; the one date miss remains the editing example.

## 8. State assumptions and cuts

All bundles belong to one patient. Data storage is local; model processing uses OpenAI. A failed extraction page delays that PDF's timeline until Retry succeeds. Source conflicts and two unsupported summary claims remain visible limitations. No auth, cloud storage, OCR engine, framework rewrite, or broad schema rewrite was added. See README's **Assumptions, known failures, and cuts**.

**Review now:** inspect steps 4–6 and the README evaluation table. Confirm the Mac tunnel displays both ports and that you can complete the date-edit/restart and Retry actions. Server and Linux browser checks do not count as Mac confirmation.
