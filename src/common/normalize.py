"""Normalization utilities for messy EHR fields."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any

from dateutil import parser as date_parser

from src.common.models import GenderCode, RecordType

GENDER_MAP = {
    "m": GenderCode.MALE,
    "male": GenderCode.MALE,
    "man": GenderCode.MALE,
    "1": GenderCode.MALE,
    "f": GenderCode.FEMALE,
    "female": GenderCode.FEMALE,
    "woman": GenderCode.FEMALE,
    "2": GenderCode.FEMALE,
    "o": GenderCode.OTHER,
    "other": GenderCode.OTHER,
    "x": GenderCode.OTHER,
    "3": GenderCode.OTHER,
    "u": GenderCode.UNKNOWN,
    "unknown": GenderCode.UNKNOWN,
    "unk": GenderCode.UNKNOWN,
    "0": GenderCode.UNKNOWN,
}

RECORD_TYPE_ALIASES = {
    "discharge_summary": RecordType.DISCHARGE_SUMMARY,
    "discharge": RecordType.DISCHARGE_SUMMARY,
    "lab_result": RecordType.LAB_RESULT,
    "lab": RecordType.LAB_RESULT,
    "lab_pdf": RecordType.LAB_PDF,
    "imaging_note": RecordType.IMAGING_NOTE,
    "imaging": RecordType.IMAGING_NOTE,
    "diagnostic_report": RecordType.DIAGNOSTIC_REPORT,
    "scanned_note": RecordType.SCANNED_NOTE,
    "progress_note": RecordType.PROGRESS_NOTE,
    "allergy_note": RecordType.ALLERGY_NOTE,
    "allergy": RecordType.ALLERGY_NOTE,
}


def normalize_mrn(raw: Any) -> tuple[str | None, list[str]]:
    """Canonical MRN form: MRN-<digits>."""
    notes: list[str] = []
    if raw is None:
        return None, ["missing mrn"]
    text = str(raw).strip()
    if not text:
        return None, ["empty mrn"]

    cleaned = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return None, [f"could not parse mrn from {raw!r}"]

    # strip leading MRN prefix if already present in alnum form
    if cleaned.startswith("MRN"):
        digits = re.sub(r"\D", "", cleaned[3:]) or digits

    mrn = f"MRN-{digits.zfill(5) if len(digits) < 5 else digits}"
    if mrn != text.upper() and mrn != text:
        notes.append(f"normalized MRN {raw!r} -> {mrn}")
    return mrn, notes


def normalize_gender(raw: Any) -> tuple[GenderCode, list[str]]:
    notes: list[str] = []
    if raw is None or str(raw).strip() == "":
        notes.append("missing gender -> unknown")
        return GenderCode.UNKNOWN, notes
    key = str(raw).strip().lower()
    gender = GENDER_MAP.get(key)
    if gender is None:
        notes.append(f"unrecognized gender {raw!r} -> unknown")
        return GenderCode.UNKNOWN, notes
    if key not in {"male", "female", "other", "unknown"}:
        notes.append(f"normalized gender {raw!r} -> {gender.value}")
    return gender, notes


def normalize_date_of_birth(raw: Any) -> tuple[date | None, list[str]]:
    notes: list[str] = []
    if raw is None or str(raw).strip() == "":
        return None, ["missing date of birth"]
    try:
        parsed = date_parser.parse(str(raw), dayfirst=False, yearfirst=False)
        # two-digit years from free text sometimes land in the future
        if parsed.year > date.today().year:
            parsed = parsed.replace(year=parsed.year - 100)
        dob = parsed.date()
        if str(raw).strip() != dob.isoformat():
            notes.append(f"normalized DOB {raw!r} -> {dob.isoformat()}")
        return dob, notes
    except (ValueError, OverflowError, TypeError):
        return None, [f"unparseable DOB {raw!r}"]


def normalize_datetime(raw: Any) -> tuple[datetime | None, list[str]]:
    notes: list[str] = []
    if raw is None or str(raw).strip() == "":
        return None, ["missing recorded_at"]
    try:
        parsed = date_parser.parse(str(raw))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        if str(raw).strip() != parsed.isoformat():
            notes.append(f"normalized datetime {raw!r} -> {parsed.isoformat()}")
        return parsed, notes
    except (ValueError, OverflowError, TypeError):
        return None, [f"unparseable datetime {raw!r}"]


def normalize_record_type(raw: Any) -> tuple[RecordType, list[str]]:
    notes: list[str] = []
    if raw is None or str(raw).strip() == "":
        notes.append("missing record_type -> other")
        return RecordType.OTHER, notes
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    mapped = RECORD_TYPE_ALIASES.get(key)
    if mapped is None:
        notes.append(f"unknown record_type {raw!r} -> other")
        return RecordType.OTHER, notes
    if key != mapped.value:
        notes.append(f"normalized record_type {raw!r} -> {mapped.value}")
    return mapped, notes


def record_fingerprint(mrn: str, record_id: str, title: str, content: str) -> str:
    material = f"{mrn}|{record_id}|{title.strip().lower()}|{content.strip().lower()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def content_hash(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
