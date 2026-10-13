"""Pydantic models for every JSON boundary (model output, API payloads)."""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

DOC_TYPES = (
    "progress_report", "permanent_stationary_report", "office_visit", "consultation",
    "operative_report", "hospital_record", "imaging_report", "imaging_order",
    "physical_therapy", "lab_result", "medication", "work_status",
    "correspondence", "cover_sheet", "billing", "form_other", "other",
)
DocType = Literal[
    "progress_report", "permanent_stationary_report", "office_visit", "consultation",
    "operative_report", "hospital_record", "imaging_report", "imaging_order",
    "physical_therapy", "lab_result", "medication", "work_status",
    "correspondence", "cover_sheet", "billing", "form_other", "other",
]
Confidence = Literal["high", "medium", "low"]
DateConfidence = Literal["exact", "approximate", "unknown"]
DOC_STATUSES = ("queued", "rendering", "extracting", "grouping", "summarizing", "deduping",
                "done", "failed", "interrupted")


class FaxPage(BaseModel):
    k: int
    n: int | None = None


class OtherDate(BaseModel):
    date: str
    label: str


class PageSignals(BaseModel):
    """Per-page output of the vision call (stage 2)."""
    transcription: str
    fax_header: str | None = None
    fax_page: FaxPage | None = None
    page_label: FaxPage | None = None
    starts_new_record: bool
    boundary_confidence: Confidence
    doc_type: DocType
    date_of_service: str | None = None
    other_dates: list[OtherDate] = Field(default_factory=list)
    provider: str | None = None
    facility: str | None = None
    patient_name: str | None = None
    gist: str
    is_blank_or_cover: bool = False


class RecordSpan(BaseModel):
    """Output of the pure grouping function (stage 3)."""
    start_page: int
    end_page: int
    boundary_reason: str
    partial_start: bool = False


class RecordSummary(BaseModel):
    """Per-record output of the text call (stage 4)."""
    title: str
    record_type: DocType
    date_of_service: str | None = None
    date_confidence: DateConfidence
    provider: str | None = None
    facility: str | None = None
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class JudgeClaim(BaseModel):
    claim: str
    label: Literal["supported", "unsupported", "unclear"]
    evidence: str = ""


class JudgeResult(BaseModel):
    claims: list[JudgeClaim]


class RecordPatch(BaseModel):
    """PATCH /api/records/{id} body. Absent field = untouched; null = clear that override."""
    title: str | None = None
    date_of_service: str | None = None
    record_type: str | None = None
    provider: str | None = None
    summary: str | None = None
    hidden: bool | None = None


def record_id_for(doc_id: str, start_page: int, end_page: int) -> str:
    """Deterministic record id: same boundaries -> same id -> overrides survive a re-run."""
    return hashlib.sha256(f"{doc_id}:{start_page}:{end_page}".encode()).hexdigest()[:16]


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON schema for OpenAI strict mode: additionalProperties false, every key required."""
    schema = model.model_json_schema()

    def fix(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
                for prop in node["properties"].values():
                    prop.pop("default", None)
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema
