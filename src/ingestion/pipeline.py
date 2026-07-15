"""Messy EHR ingestion: JSON, CSV, and free-text notes."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

from src.common.models import (
    AuditChange,
    CleanPatientRecord,
    IngestionResult,
)
from src.common.normalize import (
    normalize_date_of_birth,
    normalize_datetime,
    normalize_gender,
    normalize_mrn,
    normalize_record_type,
    record_fingerprint,
)

# Field aliases seen across EHR exports and scanned OCR dumps
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "mrn": ("mrn", "patient_id", "patientid", "id_mrn"),
    "first_name": ("first_name", "firstname", "given", "given_name"),
    "last_name": ("last_name", "lastname", "family", "family_name"),
    "dob": ("dob", "date_of_birth", "birthdate", "birth_date", "born"),
    "gender": ("gender", "sex", "gendercode", "gender_code"),
    "record_id": ("record_id", "id", "document_id", "doc_id"),
    "record_type": ("record_type", "type", "kind", "media_type"),
    "encounter_id": ("encounter_id", "encounter", "visit", "visit_id"),
    "recorded_at": ("recorded_at", "date", "datetime", "recorded", "authored"),
    "title": ("title", "name", "heading"),
    "content": ("content", "text", "body", "note", "narrative"),
}


def _pick(raw: dict[str, Any], canonical: str) -> Any:
    keys = FIELD_ALIASES[canonical]
    lower_map = {str(k).lower().replace("-", "_"): v for k, v in raw.items()}
    for key in keys:
        if key in lower_map and lower_map[key] not in (None, ""):
            return lower_map[key]
    return None


def _audit(field: str, action: str, before: Any, after: Any, note: str = "") -> AuditChange:
    return AuditChange(field=field, action=action, before=before, after=after, note=note)


def clean_raw_dict(
    raw: dict[str, Any],
    *,
    source_format: str,
    source_path: str = "",
    mrn_fallback: str | None = None,
) -> tuple[CleanPatientRecord | None, list[str]]:
    """Clean one heterogeneous record into the canonical schema."""
    warnings: list[str] = []
    audit: list[AuditChange] = []

    mrn_raw = _pick(raw, "mrn")
    mrn, mrn_notes = normalize_mrn(mrn_raw)
    for note in mrn_notes:
        audit.append(_audit("mrn", "normalize", mrn_raw, mrn, note))
    if mrn is None and mrn_fallback:
        mrn = mrn_fallback
        audit.append(
            _audit(
                "mrn",
                "resolve_conflict",
                mrn_raw,
                mrn,
                "filled missing/conflicting MRN from sibling patient identity",
            )
        )
    if mrn is None:
        return None, [f"skipped record missing MRN: {raw.get('record_id') or raw.get('id') or raw}"]

    first = str(_pick(raw, "first_name") or "").strip().title() or "Unknown"
    last = str(_pick(raw, "last_name") or "").strip().title() or "Unknown"
    if first == "Unknown" or last == "Unknown":
        warnings.append(f"{mrn}: incomplete name defaulted")
        audit.append(_audit("name", "default", None, f"{first} {last}", "missing name parts"))

    dob_raw = _pick(raw, "dob")
    dob, dob_notes = normalize_date_of_birth(dob_raw)
    for note in dob_notes:
        audit.append(_audit("date_of_birth", "normalize", dob_raw, dob.isoformat() if dob else None, note))
    if dob is None:
        return None, [f"skipped {mrn}: unusable DOB {dob_raw!r}"]

    gender_raw = _pick(raw, "gender")
    gender, gender_notes = normalize_gender(gender_raw)
    for note in gender_notes:
        audit.append(_audit("gender", "normalize", gender_raw, gender.value, note))

    record_id = str(_pick(raw, "record_id") or "").strip()
    if not record_id:
        record_id = f"AUTO-{record_fingerprint(mrn, first, last, str(_pick(raw, 'content') or ''))[:12]}"
        audit.append(_audit("record_id", "generate", None, record_id, "missing record id"))

    type_raw = _pick(raw, "record_type")
    record_type, type_notes = normalize_record_type(type_raw)
    for note in type_notes:
        audit.append(_audit("record_type", "normalize", type_raw, record_type.value, note))

    encounter = str(_pick(raw, "encounter_id") or "").strip() or f"ENC-{mrn[-4:]}"
    if not _pick(raw, "encounter_id"):
        audit.append(_audit("encounter_id", "default", None, encounter, "missing encounter id"))

    dt_raw = _pick(raw, "recorded_at")
    recorded_at, dt_notes = normalize_datetime(dt_raw)
    for note in dt_notes:
        audit.append(
            _audit(
                "recorded_at",
                "normalize",
                dt_raw,
                recorded_at.isoformat() if recorded_at else None,
                note,
            )
        )
    if recorded_at is None:
        return None, [f"skipped {mrn}/{record_id}: unusable recorded_at {dt_raw!r}"]

    title = str(_pick(raw, "title") or "Untitled clinical document").strip()
    content = str(_pick(raw, "content") or "").strip()
    if not content:
        return None, [f"skipped {mrn}/{record_id}: empty content"]

    cleaned = CleanPatientRecord(
        mrn=mrn,
        first_name=first,
        last_name=last,
        date_of_birth=dob,
        gender=gender,
        record_id=record_id.upper(),
        record_type=record_type,
        encounter_id=encounter.upper(),
        recorded_at=recorded_at,
        title=title,
        content=content,
        source_format=source_format,
        source_path=source_path,
        audit_log=audit,
    )
    return cleaned, warnings


def _dedupe(records: Iterable[CleanPatientRecord]) -> tuple[list[CleanPatientRecord], int]:
    """Drop exact/near-duplicate media rows while keeping the richest audit trail."""
    seen: dict[str, CleanPatientRecord] = {}
    removed = 0
    for record in records:
        key = record_fingerprint(record.mrn, record.record_id, record.title, record.content)
        if key in seen:
            removed += 1
            existing = seen[key]
            existing.audit_log.append(
                _audit(
                    "record",
                    "dedupe",
                    record.record_id,
                    existing.record_id,
                    f"dropped duplicate from {record.source_format}",
                )
            )
            continue
        seen[key] = record
    return list(seen.values()), removed


def _resolve_patient_identity_conflicts(
    records: list[CleanPatientRecord],
) -> tuple[list[CleanPatientRecord], list[str]]:
    """
    When the same MRN has conflicting demographics, prefer the modal values
    and log the resolution. Records that share name+DOB with a different MRN
    form keep their own MRN (identifiers are not silently overwritten).
    """
    warnings: list[str] = []
    by_mrn: dict[str, list[CleanPatientRecord]] = {}
    for rec in records:
        by_mrn.setdefault(rec.mrn, []).append(rec)

    resolved: list[CleanPatientRecord] = []
    for mrn, group in by_mrn.items():
        genders = [r.gender for r in group]
        dobs = [r.date_of_birth for r in group]
        firsts = [r.first_name for r in group]
        lasts = [r.last_name for r in group]

        canon_gender = max(set(genders), key=genders.count)
        canon_dob = max(set(dobs), key=dobs.count)
        canon_first = max(set(firsts), key=firsts.count)
        canon_last = max(set(lasts), key=lasts.count)

        for rec in group:
            if rec.gender != canon_gender:
                rec.audit_log.append(
                    _audit("gender", "resolve_conflict", rec.gender.value, canon_gender.value, f"MRN {mrn}")
                )
                warnings.append(f"{mrn}: gender conflict resolved to {canon_gender.value}")
                rec.gender = canon_gender
            if rec.date_of_birth != canon_dob:
                rec.audit_log.append(
                    _audit(
                        "date_of_birth",
                        "resolve_conflict",
                        rec.date_of_birth.isoformat(),
                        canon_dob.isoformat(),
                        f"MRN {mrn}",
                    )
                )
                warnings.append(f"{mrn}: DOB conflict resolved to {canon_dob.isoformat()}")
                rec.date_of_birth = canon_dob
            if rec.first_name != canon_first or rec.last_name != canon_last:
                before = f"{rec.first_name} {rec.last_name}"
                rec.first_name, rec.last_name = canon_first, canon_last
                rec.audit_log.append(
                    _audit("name", "resolve_conflict", before, f"{canon_first} {canon_last}", f"MRN {mrn}")
                )
            resolved.append(rec)
    return resolved, warnings


def ingest_json(path: Path) -> IngestionResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("records") or payload.get("entry") or [payload]
    if not isinstance(payload, list):
        raise ValueError(f"Unsupported JSON shape in {path}")

    cleaned: list[CleanPatientRecord] = []
    warnings: list[str] = []
    skipped = 0

    # First pass: learn MRN by name+DOB when MRN blank
    identity_index: dict[tuple[str, str, str], str] = {}
    pending: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        pending.append(item)
        mrn, _ = normalize_mrn(_pick(item, "mrn"))
        dob, _ = normalize_date_of_birth(_pick(item, "dob"))
        first = str(_pick(item, "first_name") or "").strip().title()
        last = str(_pick(item, "last_name") or "").strip().title()
        if mrn and dob and first and last:
            identity_index[(first, last, dob.isoformat())] = mrn

    for item in pending:
        mrn_raw = _pick(item, "mrn")
        mrn, _ = normalize_mrn(mrn_raw)
        fallback = None
        if mrn is None:
            dob, _ = normalize_date_of_birth(_pick(item, "dob"))
            first = str(_pick(item, "first_name") or "").strip().title()
            last = str(_pick(item, "last_name") or "").strip().title()
            if dob:
                fallback = identity_index.get((first, last, dob.isoformat()))
        record, warns = clean_raw_dict(
            item, source_format="json", source_path=str(path), mrn_fallback=fallback
        )
        warnings.extend(warns)
        if record is None:
            skipped += 1
            continue
        cleaned.append(record)

    cleaned, dupes = _dedupe(cleaned)
    cleaned, conflict_warns = _resolve_patient_identity_conflicts(cleaned)
    warnings.extend(conflict_warns)
    return IngestionResult(
        records=cleaned,
        duplicates_removed=dupes,
        skipped=skipped,
        warnings=warnings,
    )


def ingest_csv(path: Path) -> IngestionResult:
    cleaned: list[CleanPatientRecord] = []
    warnings: list[str] = []
    skipped = 0

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    identity_index: dict[tuple[str, str, str], str] = {}
    for row in rows:
        mrn, _ = normalize_mrn(_pick(row, "mrn"))
        dob, _ = normalize_date_of_birth(_pick(row, "dob"))
        first = str(_pick(row, "first_name") or "").strip().title()
        last = str(_pick(row, "last_name") or "").strip().title()
        if mrn and dob and first and last:
            identity_index[(first, last, dob.isoformat())] = mrn

    for row in rows:
        mrn, _ = normalize_mrn(_pick(row, "mrn"))
        fallback = None
        if mrn is None:
            dob, _ = normalize_date_of_birth(_pick(row, "dob"))
            first = str(_pick(row, "first_name") or "").strip().title()
            last = str(_pick(row, "last_name") or "").strip().title()
            if dob:
                fallback = identity_index.get((first, last, dob.isoformat()))
        record, warns = clean_raw_dict(
            row, source_format="csv", source_path=str(path), mrn_fallback=fallback
        )
        warnings.extend(warns)
        if record is None:
            skipped += 1
            continue
        cleaned.append(record)

    cleaned, dupes = _dedupe(cleaned)
    cleaned, conflict_warns = _resolve_patient_identity_conflicts(cleaned)
    warnings.extend(conflict_warns)
    return IngestionResult(
        records=cleaned,
        duplicates_removed=dupes,
        skipped=skipped,
        warnings=warnings,
    )



def ingest_plaintext(path: Path) -> IngestionResult:
    """Parse lightly structured free-text clinical note dumps."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n---\n", text)
    cleaned: list[CleanPatientRecord] = []
    warnings: list[str] = []
    skipped = 0

    for idx, block in enumerate(blocks):
        if "DOB" not in block.upper() and "BORN" not in block.upper():
            continue
        raw: dict[str, Any] = {}

        mrn_match = re.search(
            r"(?:MRN|Patient\s+ID|mrn)\s*[:/]?\s*([A-Za-z0-9\-]+)", block, re.I
        )
        if mrn_match:
            raw["mrn"] = mrn_match.group(1)

        name_match = re.search(
            r"Name:\s*([^|\n]+)|([A-Z][a-z]+)\s+([A-Z][a-z]+)\s*\||/\s*([A-Z][a-z]+)\s+([A-Z][a-z]+)\s*/",
            block,
        )
        if name_match:
            if name_match.group(1):
                name = name_match.group(1).strip()
                if "," in name:
                    last, first = [p.strip() for p in name.split(",", 1)]
                else:
                    parts = name.split()
                    first, last = parts[0], parts[-1]
                raw["first_name"], raw["last_name"] = first, last
            elif name_match.group(2):
                raw["first_name"], raw["last_name"] = name_match.group(2), name_match.group(3)
            else:
                raw["first_name"], raw["last_name"] = name_match.group(4), name_match.group(5)

        # "Robert Chen | Born ..." style
        inline_name = re.search(
            r"\|\s*([A-Z][a-z]+)\s+([A-Z][a-z]+)\s*\|\s*Born", block
        )
        if inline_name and "first_name" not in raw:
            raw["first_name"], raw["last_name"] = inline_name.group(1), inline_name.group(2)

        slash_name = re.search(r"/\s*([A-Z][a-z]+)\s+([A-Z][a-z]+)\s*/\s*DOB", block)
        if slash_name and "first_name" not in raw:
            raw["first_name"], raw["last_name"] = slash_name.group(1), slash_name.group(2)

        dob_match = re.search(
            r"(?:DOB|Born|date of birth)\s*:?\s*([A-Za-z0-9,\-/\s]+?)(?:\s*\||\s*$|\s*Gender|\s*Sex|\s*Encounter|\n)",
            block,
            re.I | re.M,
        )
        if dob_match:
            raw["dob"] = dob_match.group(1).strip(" /")

        gender_match = re.search(r"(?:Sex|Gender|gender)\s*:?\s*([A-Za-z])", block, re.I)
        if gender_match:
            raw["gender"] = gender_match.group(1)

        enc_match = re.search(
            r"(?:Encounter|Visit|ENC)[:\s-]*([A-Za-z0-9\-]+)", block, re.I
        )
        if enc_match:
            raw["encounter_id"] = enc_match.group(1)

        date_match = re.search(
            r"(?:Date|Recorded)\s*:?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[T\s]?[0-9:]*)",
            block,
            re.I,
        )
        if not date_match:
            date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", block)
        if date_match:
            raw["recorded_at"] = date_match.group(1)

        type_match = re.search(
            r"(?:Type|Kind)\s*:?\s*([A-Za-z_]+)", block, re.I
        )
        if type_match:
            raw["record_type"] = type_match.group(1)
        elif "allergy" in block.lower():
            raw["record_type"] = "allergy_note"
        else:
            raw["record_type"] = "progress_note"

        title_match = re.search(
            r"(?m)^\s*(?:Title|Heading)\s*:\s*(.+)$", block, re.I
        )
        if title_match:
            raw["title"] = title_match.group(1).strip()
        else:
            raw["title"] = f"Free-text note {idx + 1}"

        body_match = re.search(
            r"(?ms)^\s*(?:Text|Body)\s*:\s*(.+?)(?:\n\s*\n|\Z)", block
        )
        if body_match:
            raw["content"] = body_match.group(1).strip()
        else:
            # last non-header lines
            lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
            raw["content"] = lines[-1] if lines else ""

        raw["record_id"] = f"TXT-{idx + 1:03d}"

        record, warns = clean_raw_dict(
            raw, source_format="plaintext", source_path=str(path)
        )
        warnings.extend(warns)
        if record is None:
            skipped += 1
            continue
        cleaned.append(record)

    cleaned, dupes = _dedupe(cleaned)
    cleaned, conflict_warns = _resolve_patient_identity_conflicts(cleaned)
    warnings.extend(conflict_warns)
    return IngestionResult(
        records=cleaned,
        duplicates_removed=dupes,
        skipped=skipped,
        warnings=warnings,
    )


def ingest_path(path: Path) -> IngestionResult:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return ingest_json(path)
    if suffix == ".csv":
        return ingest_csv(path)
    if suffix in {".txt", ".text", ".note"}:
        return ingest_plaintext(path)
    raise ValueError(f"Unsupported input format: {path}")


def ingest_many(paths: Iterable[Path]) -> IngestionResult:
    merged: list[CleanPatientRecord] = []
    warnings: list[str] = []
    skipped = 0
    dupes = 0
    for path in paths:
        result = ingest_path(path)
        merged.extend(result.records)
        warnings.extend(result.warnings)
        skipped += result.skipped
        dupes += result.duplicates_removed
    merged, extra_dupes = _dedupe(merged)
    merged, conflict_warns = _resolve_patient_identity_conflicts(merged)
    warnings.extend(conflict_warns)
    return IngestionResult(
        records=sorted(merged, key=lambda r: (r.mrn, r.recorded_at, r.record_id)),
        duplicates_removed=dupes + extra_dupes,
        skipped=skipped,
        warnings=warnings,
    )
