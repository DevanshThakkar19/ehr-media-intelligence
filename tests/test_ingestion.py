"""Unit tests for ingestion cleaning edge cases."""

from __future__ import annotations

from pathlib import Path

from src.ingestion.pipeline import clean_raw_dict, ingest_csv, ingest_json, ingest_many

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "synthetic"


def test_normalizes_inconsistent_dates_and_gender():
    raw = {
        "mrn": "10021",
        "first_name": "maria",
        "last_name": "gonzalez",
        "dob": "03/12/1984",
        "gender": "2",
        "record_id": "R1",
        "record_type": "lab",
        "encounter_id": "E1",
        "recorded_at": "11/02/2024",
        "title": "Lab",
        "content": "BNP elevated",
    }
    record, warnings = clean_raw_dict(raw, source_format="json")
    assert record is not None
    assert record.mrn == "MRN-10021"
    assert record.date_of_birth.isoformat() == "1984-03-12"
    assert record.gender.value == "female"
    assert record.recorded_at.year == 2024
    assert any(a.field == "mrn" for a in record.audit_log)
    assert any(a.field == "gender" for a in record.audit_log)


def test_deduplicates_near_identical_records():
    result = ingest_json(SYN / "ehr_export.json")
    ids = [r.record_id for r in result.records]
    assert ids.count("REC-JSON-001") == 1
    assert result.duplicates_removed >= 1


def test_resolves_missing_mrn_via_identity_and_skips_empty_content():
    rows = [
        {
            "mrn": "MRN-20001",
            "first_name": "Sam",
            "last_name": "Lee",
            "dob": "1991-01-01",
            "gender": "M",
            "record_id": "A",
            "record_type": "note",
            "encounter_id": "E",
            "recorded_at": "2024-01-01",
            "title": "Good",
            "content": "Present",
        },
        {
            "mrn": "",
            "first_name": "Sam",
            "last_name": "Lee",
            "dob": "1991-01-01",
            "gender": "male",
            "record_id": "B",
            "record_type": "note",
            "encounter_id": "E",
            "recorded_at": "2024-01-02",
            "title": "Recovered MRN",
            "content": "Also present",
        },
        {
            "mrn": "MRN-20001",
            "first_name": "Sam",
            "last_name": "Lee",
            "dob": "1991-01-01",
            "gender": "M",
            "record_id": "C",
            "record_type": "note",
            "encounter_id": "E",
            "recorded_at": "2024-01-03",
            "title": "Empty",
            "content": "",
        },
    ]
    import json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(rows, handle)
        path = Path(handle.name)

    result = ingest_json(path)
    path.unlink(missing_ok=True)

    assert any(r.record_id == "B" and r.mrn == "MRN-20001" for r in result.records)
    assert all(r.record_id != "C" for r in result.records)
    assert result.skipped >= 1


def test_ingest_csv_and_json_together():
    result = ingest_many([SYN / "ehr_export.json", SYN / "scanned_notes.csv"])
    assert len(result.records) >= 15
    mrns = {r.mrn for r in result.records}
    assert "MRN-10021" in mrns
    assert "MRN-10034" in mrns
