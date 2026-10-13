# Medical Record Timeline

A local web app for reviewing scanned medical-record bundles. Drop one or more PDF bundles
in, and every page is transcribed by a vision model, grouped into individual records by
deterministic rules, summarized, de-duplicated, and shown as one chronological timeline
across all bundles. Click a card to see the source pages, the transcription, and edit the fields.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env          # set OPENAI_API_KEY
python -m app                # http://127.0.0.1:8000
```

Drop `samples/record_1.pdf` (or any scanned bundle) on the page. Processing runs in the
server process; the page polls progress every 1.5 s.

Useful environment variables (all in `env.example`): `MRT_MAX_PAGES=12` for a fast dev run,
`MRT_FAIL_AT_PAGE=40` to demo retry, `MRT_FAKE_LLM=1` to run everything offline with a
deterministic fake model (tests, UI work), `MRT_PORT` to run several checkouts side by side.

Without the UI:

```bash
python -m scripts.probe_model samples/record_1.pdf 1     # one vision call, prints JSON
python -m scripts.process samples/record_1.pdf           # full pipeline, prints the spans
python -m pytest                                         # unit tests, no network
```

## How it works

(filled in at M2)

## Bonus features

(filled in at M3/M4)

## Design decisions

(filled in at M5)

## Assumptions

- `gpt-6-luna` accepts image input through Chat Completions with `json_schema` strict mode.
  `app/llm.py` falls back to `json_object` and then to the Responses API if the API rejects
  either; all calls live in that one file.
- The timeline date is the date of service. Fax, print, birth and injury dates never become
  the timeline date; when a record carries no service date the report or fax date is used and
  marked approximate (`~`).
- A bundle can start mid-record (the sample starts at fax page 2 of 6). Such records are
  flagged `partial`.
- All PDFs dropped in belong to one patient; duplicate detection runs across bundles.
- A dropped PDF is identified by the sha256 of its bytes. Dropping the same file twice returns
  the existing document instead of re-processing it.
- Record ids are deterministic (`sha256(doc_id:start:end)[:16]`), so a re-run that produces
  the same boundaries keeps the same ids and the reviewer's edits survive.
- The env template is `env.example` (a `.env.example` file is blocked by a tooling rule in the
  build environment).
- The first 188-page run was not possible during the build because no API key was available
  in the build session; every stage was exercised end to end with `MRT_FAKE_LLM=1`.

## What was cut

(filled in at M5)

## Demo script

(filled in at M5)
