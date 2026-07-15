"""Map cleaned records to HL7 FHIR R4 resources and Bundles."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import (
    DocumentReference,
    DocumentReferenceContent,
    DocumentReferenceContext,
)
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from src.common.models import CleanPatientRecord, RecordType

DOC_TYPE_CODING = {
    RecordType.DISCHARGE_SUMMARY: ("http://loinc.org", "18842-5", "Discharge summary"),
    RecordType.IMAGING_NOTE: ("http://loinc.org", "18748-4", "Diagnostic imaging study"),
    RecordType.LAB_RESULT: ("http://loinc.org", "11502-2", "Laboratory report"),
    RecordType.LAB_PDF: ("http://loinc.org", "11502-2", "Laboratory report"),
    RecordType.DIAGNOSTIC_REPORT: ("http://loinc.org", "11502-2", "Laboratory report"),
    RecordType.SCANNED_NOTE: ("http://loinc.org", "34109-9", "Note"),
    RecordType.PROGRESS_NOTE: ("http://loinc.org", "11506-3", "Progress note"),
    RecordType.ALLERGY_NOTE: ("http://loinc.org", "48765-2", "Allergies and adverse reactions Document"),
    RecordType.OTHER: ("http://loinc.org", "34133-9", "Summary of episode note"),
}

DIAGNOSTIC_TYPES = {
    RecordType.LAB_RESULT,
    RecordType.LAB_PDF,
    RecordType.DIAGNOSTIC_REPORT,
    RecordType.IMAGING_NOTE,
}


@dataclass
class ValidationIssue:
    patient_mrn: str
    resource_type: str
    resource_id: str
    severity: str
    message: str


@dataclass
class FhirBuildResult:
    bundles: dict[str, Bundle]
    issues: list[ValidationIssue] = field(default_factory=list)
    patient_ids: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


def _patient_id(mrn: str) -> str:
    return f"patient-{mrn.lower().replace('_', '-')}"


def _encounter_ref(encounter_id: str) -> str:
    return f"Encounter/{encounter_id.lower()}"


def _resource_type(resource: Resource) -> str:
    return getattr(resource, "resource_type", None) or resource.__class__.__name__


def _fhir_instant(dt: datetime) -> str:
    """FHIR instant as UTC ISO-8601 with Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_patient(records: list[CleanPatientRecord]) -> Patient:
    seed = records[0]
    return Patient(
        id=_patient_id(seed.mrn),
        meta=Meta(profile=["http://hl7.org/fhir/StructureDefinition/Patient"]),
        identifier=[
            Identifier(
                system="http://hospital.example.org/mrn",
                value=seed.mrn,
            )
        ],
        name=[HumanName(family=seed.last_name, given=[seed.first_name], use="official")],
        gender=seed.gender.value,
        birthDate=seed.date_of_birth.isoformat(),
    )


def build_document_reference(record: CleanPatientRecord, patient_fhir_id: str) -> DocumentReference:
    system, code, display = DOC_TYPE_CODING.get(
        record.record_type, DOC_TYPE_CODING[RecordType.OTHER]
    )
    return DocumentReference(
        id=f"doc-{record.record_id.lower()}",
        status="current",
        type=CodeableConcept(
            coding=[Coding(system=system, code=code, display=display)],
            text=record.title,
        ),
        category=[
            CodeableConcept(
                coding=[
                    Coding(
                        system="http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
                        code="clinical-note",
                        display="Clinical Note",
                    )
                ]
            )
        ],
        subject=Reference(reference=f"Patient/{patient_fhir_id}", display=record.patient_display_name),
        date=_fhir_instant(record.recorded_at),
        description=f"{record.title} | {record.content}",
        content=[
            DocumentReferenceContent(
                attachment=Attachment(
                    contentType="text/plain",
                    title=record.title,
                    url=f"urn:uuid:{record.record_id.lower()}",
                )
            )
        ],
        context=DocumentReferenceContext(
            encounter=[Reference(reference=_encounter_ref(record.encounter_id))],
            period=Period(start=_fhir_instant(record.recorded_at)),
        ),
    )


def build_diagnostic_report(record: CleanPatientRecord, patient_fhir_id: str) -> DiagnosticReport:
    system, code, display = DOC_TYPE_CODING.get(
        record.record_type, DOC_TYPE_CODING[RecordType.OTHER]
    )
    return DiagnosticReport(
        id=f"dr-{record.record_id.lower()}",
        status="final",
        code=CodeableConcept(
            coding=[Coding(system=system, code=code, display=display)],
            text=record.title,
        ),
        subject=Reference(reference=f"Patient/{patient_fhir_id}", display=record.patient_display_name),
        encounter=Reference(reference=_encounter_ref(record.encounter_id)),
        effectiveDateTime=_fhir_instant(record.recorded_at),
        issued=_fhir_instant(record.recorded_at),
        conclusion=record.content[:4000],
    )


def _validate_resource(resource: Resource, mrn: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    rtype = _resource_type(resource)
    rid = getattr(resource, "id", None) or "?"
    try:
        # Round-trip through JSON using fhir.resources validation (pydantic v1 API in R4B)
        resource.__class__.parse_raw(resource.json())
    except Exception as exc:  # noqa: BLE001
        issues.append(
            ValidationIssue(
                patient_mrn=mrn,
                resource_type=rtype,
                resource_id=rid,
                severity="error",
                message=str(exc),
            )
        )
        return issues

    if hasattr(resource, "subject") and resource.subject is not None:
        ref = getattr(resource.subject, "reference", None)
        if not ref or not str(ref).startswith("Patient/"):
            issues.append(
                ValidationIssue(
                    patient_mrn=mrn,
                    resource_type=rtype,
                    resource_id=rid,
                    severity="error",
                    message=f"subject must reference Patient/*, got {ref!r}",
                )
            )
    return issues


def records_to_bundles(records: list[CleanPatientRecord]) -> FhirBuildResult:
    by_mrn: dict[str, list[CleanPatientRecord]] = defaultdict(list)
    for rec in records:
        by_mrn[rec.mrn].append(rec)

    bundles: dict[str, Bundle] = {}
    issues: list[ValidationIssue] = []
    patient_ids: dict[str, str] = {}

    for mrn, group in sorted(by_mrn.items()):
        patient = build_patient(group)
        patient_ids[mrn] = patient.id
        issues.extend(_validate_resource(patient, mrn))

        entries: list[BundleEntry] = [
            BundleEntry(fullUrl=f"urn:uuid:{patient.id}", resource=patient)
        ]

        for rec in sorted(group, key=lambda r: r.recorded_at):
            doc = build_document_reference(rec, patient.id)
            issues.extend(_validate_resource(doc, mrn))
            entries.append(BundleEntry(fullUrl=f"urn:uuid:{doc.id}", resource=doc))

            if rec.record_type in DIAGNOSTIC_TYPES:
                report = build_diagnostic_report(rec, patient.id)
                issues.extend(_validate_resource(report, mrn))
                entries.append(BundleEntry(fullUrl=f"urn:uuid:{report.id}", resource=report))

        bundle = Bundle(
            id=f"bundle-{mrn.lower()}",
            type="collection",
            timestamp=_fhir_instant(datetime.utcnow()),
            entry=entries,
        )
        try:
            Bundle.parse_raw(bundle.json())
        except Exception as exc:  # noqa: BLE001
            issues.append(
                ValidationIssue(
                    patient_mrn=mrn,
                    resource_type="Bundle",
                    resource_id=bundle.id or "?",
                    severity="error",
                    message=str(exc),
                )
            )
        bundles[mrn] = bundle

    return FhirBuildResult(bundles=bundles, issues=issues, patient_ids=patient_ids)


def validation_report(result: FhirBuildResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "bundle_count": len(result.bundles),
        "error_count": sum(1 for i in result.issues if i.severity == "error"),
        "warning_count": sum(1 for i in result.issues if i.severity == "warning"),
        "issues": [
            {
                "patient_mrn": i.patient_mrn,
                "resource_type": i.resource_type,
                "resource_id": i.resource_id,
                "severity": i.severity,
                "message": i.message,
            }
            for i in result.issues
        ],
    }


def bundle_to_dict(bundle: Bundle) -> dict[str, Any]:
    return json.loads(bundle.json())


def extract_searchable_documents(bundle: Bundle, mrn: str) -> list[dict[str, Any]]:
    """Flatten FHIR Bundle resources into text units for embeddings/search."""
    patient_name = ""
    docs: list[dict[str, Any]] = []
    for entry in bundle.entry or []:
        resource = entry.resource
        rtype = _resource_type(resource)
        if rtype == "Patient":
            names = resource.name or []
            if names:
                given = " ".join(names[0].given or [])
                patient_name = f"{given} {names[0].family or ''}".strip()
        elif rtype == "DocumentReference":
            text = resource.description or (
                resource.type.text if resource.type else ""
            )
            recorded = None
            if resource.date:
                recorded = (
                    resource.date.isoformat()
                    if hasattr(resource.date, "isoformat")
                    else str(resource.date)
                )
            title = None
            if resource.type and resource.type.text:
                title = resource.type.text
            elif resource.description:
                title = resource.description.split("|", 1)[0].strip()
            docs.append(
                {
                    "mrn": mrn,
                    "patient_name": patient_name,
                    "resource_type": "DocumentReference",
                    "resource_id": resource.id,
                    "title": title or resource.id,
                    "text": text or "",
                    "recorded_at": recorded,
                }
            )
        elif rtype == "DiagnosticReport":
            text = resource.conclusion or (resource.code.text if resource.code else "")
            recorded = None
            if resource.effectiveDateTime:
                dt = resource.effectiveDateTime
                recorded = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
            docs.append(
                {
                    "mrn": mrn,
                    "patient_name": patient_name,
                    "resource_type": "DiagnosticReport",
                    "resource_id": resource.id,
                    "title": (resource.code.text if resource.code else None) or resource.id,
                    "text": text or "",
                    "recorded_at": recorded,
                }
            )
    for doc in docs:
        if not doc["patient_name"]:
            doc["patient_name"] = patient_name
    return docs
