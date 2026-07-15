"""Shared types for the EHR media pipeline."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class GenderCode(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


class RecordType(str, Enum):
    DISCHARGE_SUMMARY = "discharge_summary"
    LAB_RESULT = "lab_result"
    LAB_PDF = "lab_pdf"
    IMAGING_NOTE = "imaging_note"
    DIAGNOSTIC_REPORT = "diagnostic_report"
    SCANNED_NOTE = "scanned_note"
    PROGRESS_NOTE = "progress_note"
    ALLERGY_NOTE = "allergy_note"
    OTHER = "other"


class AuditChange(BaseModel):
    field: str
    action: str
    before: Any = None
    after: Any = None
    note: str = ""


class CleanPatientRecord(BaseModel):
    """Canonical intermediate representation after ingestion cleanup."""

    mrn: str
    first_name: str
    last_name: str
    date_of_birth: date
    gender: GenderCode
    record_id: str
    record_type: RecordType
    encounter_id: str
    recorded_at: datetime
    title: str
    content: str
    source_format: str
    source_path: str = ""
    audit_log: list[AuditChange] = Field(default_factory=list)

    @field_validator("mrn")
    @classmethod
    def normalize_mrn_present(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("mrn is required")
        return value.strip().upper()

    @property
    def patient_display_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def content_hash_material(self) -> str:
        return "|".join(
            [
                self.mrn,
                self.record_id,
                self.recorded_at.isoformat(),
                self.title,
                self.content,
            ]
        )


class IngestionResult(BaseModel):
    records: list[CleanPatientRecord]
    duplicates_removed: int = 0
    skipped: int = 0
    warnings: list[str] = Field(default_factory=list)
