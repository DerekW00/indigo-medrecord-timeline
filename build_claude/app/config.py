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
CONCURRENCY = _int("MRT_CONCURRENCY", 8)
MAX_PAGES = _int("MRT_MAX_PAGES", 0)
FAIL_AT_PAGE = _int("MRT_FAIL_AT_PAGE", 0)
RENDER_LONG_SIDE = _int("MRT_RENDER_LONG_SIDE", 1600)
PORT = _int("MRT_PORT", 8000)
FAKE_LLM = os.environ.get("MRT_FAKE_LLM", "0").strip().lower() in ("1", "true", "yes")

DB_PATH = DATA_DIR / "app.db"
PAGES_DIR = DATA_DIR / "pages"
UPLOADS_DIR = DATA_DIR / "uploads"
