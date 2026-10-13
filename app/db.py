"""SQLite store: schema, one connection per thread, small DAO functions."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, filename TEXT, page_count INTEGER, status TEXT,
  stage_done INTEGER DEFAULT 0, stage_total INTEGER DEFAULT 0,
  error TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pages(
  doc_id TEXT, page_no INTEGER, image_path TEXT, status TEXT,
  attempts INTEGER DEFAULT 0, error TEXT, signals_json TEXT, text TEXT, text_hash TEXT,
  PRIMARY KEY(doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS records(
  id TEXT PRIMARY KEY, doc_id TEXT, start_page INTEGER, end_page INTEGER,
  boundary_reason TEXT, partial_start INTEGER DEFAULT 0, status TEXT, error TEXT,
  record_type TEXT, title TEXT, date_of_service TEXT, date_confidence TEXT,
  provider TEXT, facility TEXT, summary TEXT, key_findings_json TEXT, flags_json TEXT,
  content_hash TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS records_doc ON records(doc_id, start_page);
CREATE TABLE IF NOT EXISTS record_overrides(
  record_id TEXT PRIMARY KEY, title TEXT, date_of_service TEXT, record_type TEXT,
  provider TEXT, summary TEXT, hidden INTEGER DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS duplicate_groups(
  record_id TEXT PRIMARY KEY, group_id TEXT, is_canonical INTEGER, reason TEXT, score REAL
);
"""

_local = threading.local()
_write_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(config.DB_PATH), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init_db() -> None:
    with _write_lock:
        connect().executescript(SCHEMA)


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _exec(sql: str, params=()) -> None:
    with _write_lock:
        conn = connect()
        conn.execute(sql, params)
        conn.commit()


# ---------- documents ----------

def upsert_document(doc_id: str, filename: str, page_count: int, status: str = "queued") -> dict:
    ts = now()
    _exec(
        "INSERT INTO documents(id, filename, page_count, status, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
        (doc_id, filename, page_count, status, ts, ts),
    )
    return get_document(doc_id)


def get_document(doc_id: str) -> dict | None:
    r = connect().execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(r) if r else None


def list_documents() -> list[dict]:
    return _rows(connect().execute("SELECT * FROM documents ORDER BY created_at"))


def set_document(doc_id: str, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    _exec(f"UPDATE documents SET {cols} WHERE id=?", (*fields.values(), doc_id))


def delete_document(doc_id: str) -> None:
    with _write_lock:
        conn = connect()
        conn.execute("DELETE FROM record_overrides WHERE record_id IN (SELECT id FROM records WHERE doc_id=?)", (doc_id,))
        conn.execute("DELETE FROM duplicate_groups WHERE record_id IN (SELECT id FROM records WHERE doc_id=?)", (doc_id,))
        conn.execute("DELETE FROM records WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.commit()


# ---------- pages ----------

def upsert_page(doc_id: str, page_no: int, image_path: str) -> None:
    _exec(
        "INSERT INTO pages(doc_id, page_no, image_path, status, attempts) VALUES(?,?,?,'pending',0) "
        "ON CONFLICT(doc_id, page_no) DO UPDATE SET image_path=excluded.image_path",
        (doc_id, page_no, image_path),
    )


def get_pages(doc_id: str) -> list[dict]:
    return _rows(connect().execute("SELECT * FROM pages WHERE doc_id=? ORDER BY page_no", (doc_id,)))


def get_page(doc_id: str, page_no: int) -> dict | None:
    r = connect().execute("SELECT * FROM pages WHERE doc_id=? AND page_no=?", (doc_id, page_no)).fetchone()
    return dict(r) if r else None


def save_page_result(doc_id: str, page_no: int, signals: dict, text: str, text_hash: str, attempts: int) -> None:
    _exec(
        "UPDATE pages SET status='done', signals_json=?, text=?, text_hash=?, attempts=?, error=NULL "
        "WHERE doc_id=? AND page_no=?",
        (json.dumps(signals), text, text_hash, attempts, doc_id, page_no),
    )


def save_page_failure(doc_id: str, page_no: int, error: str, attempts: int) -> None:
    _exec(
        "UPDATE pages SET status='failed', error=?, attempts=? WHERE doc_id=? AND page_no=?",
        (error[:2000], attempts, doc_id, page_no),
    )


def reset_failed_pages(doc_id: str) -> None:
    _exec("UPDATE pages SET status='pending', error=NULL WHERE doc_id=? AND status='failed'", (doc_id,))


# ---------- records ----------

def sync_records(doc_id: str, spans: list[dict]) -> None:
    """Make the records of a document match `spans` (dicts with id, start_page, end_page,
    boundary_reason, partial_start). Existing ids keep their summary and status; new ids are
    pending; ids no longer present are deleted (overrides for them are kept, harmless)."""
    ts = now()
    with _write_lock:
        conn = connect()
        keep = [s["id"] for s in spans]
        existing = {r[0] for r in conn.execute("SELECT id FROM records WHERE doc_id=?", (doc_id,))}
        for rid in existing - set(keep):
            conn.execute("DELETE FROM records WHERE id=?", (rid,))
            conn.execute("DELETE FROM duplicate_groups WHERE record_id=?", (rid,))
        for s in spans:
            if s["id"] in existing:
                conn.execute(
                    "UPDATE records SET boundary_reason=?, partial_start=?, updated_at=? WHERE id=?",
                    (s["boundary_reason"], int(s["partial_start"]), ts, s["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO records(id, doc_id, start_page, end_page, boundary_reason, partial_start, "
                    "status, created_at, updated_at) VALUES(?,?,?,?,?,?,'pending',?,?)",
                    (s["id"], doc_id, s["start_page"], s["end_page"], s["boundary_reason"],
                     int(s["partial_start"]), ts, ts),
                )
        conn.commit()


def get_records(doc_id: str) -> list[dict]:
    return _rows(connect().execute("SELECT * FROM records WHERE doc_id=? ORDER BY start_page", (doc_id,)))


def get_record(record_id: str) -> dict | None:
    r = connect().execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    return dict(r) if r else None


def save_record_summary(record_id: str, summary: dict, content_hash: str) -> None:
    _exec(
        "UPDATE records SET status='done', error=NULL, record_type=?, title=?, date_of_service=?, "
        "date_confidence=?, provider=?, facility=?, summary=?, key_findings_json=?, flags_json=?, "
        "content_hash=?, updated_at=? WHERE id=?",
        (summary["record_type"], summary["title"], summary.get("date_of_service"),
         summary["date_confidence"], summary.get("provider"), summary.get("facility"),
         summary["summary"], json.dumps(summary.get("key_findings", [])),
         json.dumps(summary.get("flags", [])), content_hash, now(), record_id),
    )


def save_record_failure(record_id: str, error: str) -> None:
    _exec("UPDATE records SET status='failed', error=?, updated_at=? WHERE id=?",
          (error[:2000], now(), record_id))


def all_done_records() -> list[dict]:
    return _rows(connect().execute(
        "SELECT r.*, d.filename, d.created_at AS doc_created_at FROM records r "
        "JOIN documents d ON d.id=r.doc_id WHERE r.status='done' ORDER BY d.created_at, r.start_page"))


# ---------- overrides ----------

def get_override(record_id: str) -> dict | None:
    r = connect().execute("SELECT * FROM record_overrides WHERE record_id=?", (record_id,)).fetchone()
    return dict(r) if r else None


def get_overrides() -> dict[str, dict]:
    return {r["record_id"]: r for r in _rows(connect().execute("SELECT * FROM record_overrides"))}


def upsert_override(record_id: str, fields: dict) -> None:
    """fields: subset of title, date_of_service, record_type, provider, summary, hidden.
    A None value clears that single override field."""
    allowed = ("title", "date_of_service", "record_type", "provider", "summary", "hidden")
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    with _write_lock:
        conn = connect()
        conn.execute("INSERT INTO record_overrides(record_id, updated_at) VALUES(?, ?) "
                     "ON CONFLICT(record_id) DO NOTHING", (record_id, now()))
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [int(v) if k == "hidden" and v is not None else v for k, v in fields.items()]
        conn.execute(f"UPDATE record_overrides SET {cols}, updated_at=? WHERE record_id=?",
                     (*vals, now(), record_id))
        conn.commit()


def delete_override(record_id: str) -> None:
    _exec("DELETE FROM record_overrides WHERE record_id=?", (record_id,))


# ---------- duplicates ----------

def replace_duplicate_groups(rows: list[dict]) -> None:
    """rows: {record_id, group_id, is_canonical, reason, score}. Replaces the whole table."""
    with _write_lock:
        conn = connect()
        conn.execute("DELETE FROM duplicate_groups")
        conn.executemany(
            "INSERT INTO duplicate_groups(record_id, group_id, is_canonical, reason, score) VALUES(?,?,?,?,?)",
            [(r["record_id"], r["group_id"], int(r["is_canonical"]), r["reason"], r["score"]) for r in rows],
        )
        conn.commit()


def get_duplicate_groups() -> dict[str, dict]:
    return {r["record_id"]: r for r in _rows(connect().execute("SELECT * FROM duplicate_groups"))}
