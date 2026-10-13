"""Environment configuration with defaults. See env.example."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Project .env first, then the parent checkout's .env (shared key during the build).
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
DATA_DIR = Path(os.environ.get("MRT_DATA_DIR", str(ROOT / "data"))).resolve()
CONCURRENCY = _int("MRT_CONCURRENCY", 16)  # measured: 24 parallel, zero 429s
MAX_PAGES = _int("MRT_MAX_PAGES", 0)
FAIL_AT_PAGE = _int("MRT_FAIL_AT_PAGE", 0)
RENDER_LONG_SIDE = _int("MRT_RENDER_LONG_SIDE", 1600)
PORT = _int("MRT_PORT", 8000)
FAKE_LLM = os.environ.get("MRT_FAKE_LLM", "0").strip().lower() in ("1", "true", "yes")
# Reasoning effort per call kind ("" = API default). Measured: "low" transcribes faithfully at ~2x speed.
EXTRACT_EFFORT = os.environ.get("MRT_EXTRACT_EFFORT", "low").strip()
SUMMARY_EFFORT = os.environ.get("MRT_SUMMARY_EFFORT", "").strip()

DB_PATH = DATA_DIR / "app.db"
PAGES_DIR = DATA_DIR / "pages"
UPLOADS_DIR = DATA_DIR / "uploads"
