"""FHIR mapping tests."""

from __future__ import annotations

from datetime import date, datetime

from src.common.models import CleanPatientRecord, GenderCode, RecordType
from src.fhir.mapper import records_to_bundles, validation_report


def _sample_records():
    return [
        CleanPatientRecord(
            mrn="MRN-10021",
            first_name="Maria",
            last_name="Gonzalez",
            date_of_birth=date(1984, 3, 12),
            gender=GenderCode.FEMALE,
            record_id="REC-1",
            record_type=RecordType.LAB_RESULT,
            encounter_id="ENC-1",
            recorded_at=datetime(2024, 11, 1, 9, 0, 0),
            title="BNP",
            content="BNP 890 elevated",
            source_format="json",
        ),
        CleanPatientRecord(
            mrn="MRN-10021",
            first_name="Maria",
            last_name="Gonzalez",
            date_of_birth=date(1984, 3, 12),
            gender=GenderCode.FEMALE,
            record_id="REC-2",
            record_type=RecordType.DISCHARGE_SUMMARY,
            encounter_id="ENC-1",
            recorded_at=datetime(2024, 11, 2, 14, 0, 0),
            title="Discharge",
            content="CHF exacerbation",
            source_format="json",
        ),
    ]


def test_builds_patient_document_and_diagnostic_resources():
    result = records_to_bundles(_sample_records())
    assert "MRN-10021" in result.bundles
    report = validation_report(result)
    assert report["ok"] is True

    bundle = result.bundles["MRN-10021"]
    types = [e.resource.resource_type for e in bundle.entry]
    assert "Patient" in types
    assert "DocumentReference" in types
    assert "DiagnosticReport" in types

    # subject references point at Patient
    for entry in bundle.entry:
        res = entry.resource
        if hasattr(res, "subject") and res.subject is not None:
            assert res.subject.reference.startswith("Patient/")
