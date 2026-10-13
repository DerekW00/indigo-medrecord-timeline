"""OpenAI adapter. Every model call in the app goes through this file.

vision_json(): one page image + prompt -> validated pydantic object.
text_json():   text prompt -> validated pydantic object.

Chat Completions with json_schema strict mode; falls back to json_object if the API
rejects the schema; falls back to the Responses API if Chat rejects image input.
3 attempts with exponential backoff (2 s, 4 s, 8 s). FAKE_LLM=1 returns deterministic
objects without any network call (tests, UI work without a key).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import config
from .models import strict_schema

log = logging.getLogger("mrt.llm")
T = TypeVar("T", bound=BaseModel)

ATTEMPTS = 3
BACKOFF = (2, 4, 8)
_client = None
_mode = {"schema": "json_schema", "api": "chat"}  # mutated on fallback, process-wide


class LLMError(Exception):
    """Raised after the last failed attempt."""


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        if not config.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not set (put it in .env)")
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY, timeout=180.0, max_retries=0)
    return _client


def _image_data_url(image_path: str | Path) -> str:
    data = Path(image_path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _response_format(model_cls: type[BaseModel]) -> dict:
    if _mode["schema"] == "json_schema":
        return {"type": "json_schema",
                "json_schema": {"name": model_cls.__name__, "schema": strict_schema(model_cls), "strict": True}}
    return {"type": "json_object"}


def _is_retryable(exc: Exception) -> bool:
    import openai
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError,
                        openai.InternalServerError)):
        return True
    status = getattr(exc, "status_code", None)
    return status is not None and (status == 429 or status >= 500)


async def _call_chat(system: str, user_parts: list[dict], model_cls: type[BaseModel],
                     max_completion_tokens: int):
    client = _get_client()
    resp = await client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_parts}],
        response_format=_response_format(model_cls),
        max_completion_tokens=max_completion_tokens,
    )
    choice = resp.choices[0]
    usage = resp.usage
    return choice.message.content or "", (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)


async def _call_responses(system: str, user_parts: list[dict], model_cls: type[BaseModel],
                          max_completion_tokens: int):
    client = _get_client()
    content = []
    for p in user_parts:
        if p["type"] == "text":
            content.append({"type": "input_text", "text": p["text"]})
        else:
            content.append({"type": "input_image", "image_url": p["image_url"]["url"], "detail": "high"})
    text_cfg = {"format": {"type": "json_schema", "name": model_cls.__name__,
                           "schema": strict_schema(model_cls), "strict": True}}
    if _mode["schema"] != "json_schema":
        text_cfg = {"format": {"type": "json_object"}}
    resp = await client.responses.create(
        model=config.OPENAI_MODEL,
        instructions=system,
        input=[{"role": "user", "content": content}],
        text=text_cfg,
        max_output_tokens=max_completion_tokens,
    )
    usage = resp.usage
    return resp.output_text or "", (usage.input_tokens if usage else 0), (usage.output_tokens if usage else 0)


async def _complete_json(system: str, user_parts: list[dict], model_cls: type[T], tag: str,
                         max_completion_tokens: int) -> T:
    if config.FAKE_LLM:
        return _fake(model_cls, user_parts, tag)
    import openai
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.monotonic()
        try:
            if _mode["api"] == "chat":
                raw, p_tok, c_tok = await _call_chat(system, user_parts, model_cls, max_completion_tokens)
            else:
                raw, p_tok, c_tok = await _call_responses(system, user_parts, model_cls, max_completion_tokens)
            obj = model_cls.model_validate_json(raw)
            log.info("llm ok   %-22s api=%s latency=%.1fs prompt=%d completion=%d attempt=%d",
                     tag, _mode["api"], time.monotonic() - t0, p_tok, c_tok, attempt)
            return obj
        except openai.BadRequestError as exc:
            msg = str(exc).lower()
            # Switch strategy once, then retry immediately (does not consume an attempt).
            if _mode["schema"] == "json_schema" and ("schema" in msg or "response_format" in msg):
                log.warning("llm: API rejected json_schema (%s); falling back to json_object", exc)
                _mode["schema"] = "json_object"
                continue
            if _mode["api"] == "chat" and ("image" in msg or "vision" in msg or "content" in msg):
                log.warning("llm: Chat Completions rejected the request (%s); switching to Responses API", exc)
                _mode["api"] = "responses"
                continue
            last = exc
            log.error("llm fail %-22s attempt=%d non-retryable: %s", tag, attempt, exc)
            break
        except (ValidationError, json.JSONDecodeError) as exc:
            last = exc
            log.warning("llm bad  %-22s attempt=%d invalid JSON: %s", tag, attempt, str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_retryable(exc):
                log.error("llm fail %-22s attempt=%d non-retryable: %s", tag, attempt, exc)
                break
            log.warning("llm err  %-22s attempt=%d latency=%.1fs %s", tag, attempt, time.monotonic() - t0,
                        str(exc)[:200])
        if attempt < ATTEMPTS:
            await asyncio.sleep(BACKOFF[attempt - 1])
    raise LLMError(f"{tag}: {type(last).__name__}: {str(last)[:300]}")


async def vision_json(system: str, user: str, image_path: str | Path, model_cls: type[T], tag: str,
                      max_completion_tokens: int = 6000) -> T:
    """One page image + instructions -> validated model object."""
    if config.FAKE_LLM:
        return _fake(model_cls, [{"type": "text", "text": user}], tag, image_path=image_path)
    data_url = await asyncio.to_thread(_image_data_url, image_path)
    parts = [{"type": "text", "text": user},
             {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}}]
    return await _complete_json(system, parts, model_cls, tag, max_completion_tokens)


async def text_json(system: str, user: str, model_cls: type[T], tag: str,
                    max_completion_tokens: int = 4000) -> T:
    """Text instructions -> validated model object."""
    parts = [{"type": "text", "text": user}]
    return await _complete_json(system, parts, model_cls, tag, max_completion_tokens)


# ---------- offline fake model ----------

def _fake(model_cls: type[T], user_parts: list[dict], tag: str, image_path=None) -> T:
    """Deterministic stand-in: every 4th page starts a new 'record' with a fax header."""
    name = model_cls.__name__
    seed = int(hashlib.sha256(tag.encode()).hexdigest()[:8], 16)
    if name == "PageSignals":
        page_no = int(tag.rsplit("p", 1)[-1]) if tag.rsplit("p", 1)[-1].isdigit() else seed % 50
        rec = (page_no - 1) // 4
        k = (page_no - 1) % 4 + 2  # stripped cover: fax pages start at 2
        date = f"2025-{(rec % 12) + 1:02d}-{(rec % 27) + 1:02d}"
        return model_cls.model_validate({
            "transcription": f"KAISER PERMANENTE on-the-job\nPRIMARY TREATING PHYSICIAN'S PROGRESS REPORT (PR-2)\n"
                             f"Date of exam: {date}\nFake transcription of page {page_no} (record {rec + 1}). "
                             f"Patient seen for left ankle fracture follow-up. Plan: continue physical therapy.",
            "fax_header": f"10/{(rec % 28) + 1:02d}/2025 3:17:27 PM PDT PAGE {k}/005 Fax Server",
            "fax_page": {"k": k, "n": 5},
            "page_label": {"k": k - 1, "n": 4},
            "starts_new_record": k == 2,
            "boundary_confidence": "high",
            "doc_type": "progress_report",
            "date_of_service": date,
            "other_dates": [{"date": "2024-09-30", "label": "date of injury"}],
            "provider": "A. Example, MD",
            "facility": "Kaiser Permanente Occupational Health",
            "patient_name": None,
            "gist": f"Fake page {page_no}: PR-2 progress report page {k - 1}.",
            "is_blank_or_cover": False,
        })
    if name == "RecordSummary":
        text = user_parts[0]["text"]
        import re
        m = re.search(r"Date of exam: (\d{4}-\d{2}-\d{2})", text)
        return model_cls.model_validate({
            "title": "PR-2 progress report: left ankle fracture follow-up (fake)",
            "record_type": "progress_report",
            "date_of_service": m.group(1) if m else None,
            "date_confidence": "exact" if m else "unknown",
            "provider": "A. Example, MD",
            "facility": "Kaiser Permanente Occupational Health",
            "summary": "Fake summary. Follow-up visit for a left ankle fracture after ORIF. "
                       "Patient continues physical therapy. Work status: modified duty.",
            "key_findings": ["Left ankle fracture status post ORIF", "Continue physical therapy"],
            "flags": [],
        })
    if name == "JudgeResult":
        return model_cls.model_validate({"claims": [{"claim": "fake claim", "label": "supported", "evidence": "fake"}]})
    raise LLMError(f"fake model has no stand-in for {name}")
