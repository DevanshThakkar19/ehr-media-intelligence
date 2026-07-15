"""Search endpoint smoke tests (embeddings may be heavy — kept narrow)."""

from __future__ import annotations

from src.search import SemanticSearchIndex, build_index_documents


def test_build_index_documents_includes_summary_virtual_resource():
    docs = [
        {
            "mrn": "MRN-1",
            "patient_name": "Pat Test",
            "resource_type": "DocumentReference",
            "resource_id": "doc-1",
            "title": "Note",
            "text": "dyspnea and edema",
            "recorded_at": "2024-01-01T00:00:00",
        }
    ]
    summaries = {
        "MRN-1": {
            "summary": "Chief concern: dyspnea. Key diagnoses: heart failure.",
            "disclaimer": "AI",
        }
    }
    indexed = build_index_documents(docs, summaries)
    types = {d["resource_type"] for d in indexed}
    assert "DocumentReference" in types
    assert "PatientSummary" in types


def test_search_filters_by_resource_type(tmp_path):
    index = SemanticSearchIndex(persist_dir=tmp_path / "chroma", collection_name="test")
    index.reset()
    docs = [
        {
            "id": "a",
            "mrn": "MRN-1",
            "patient_name": "A",
            "resource_type": "DiagnosticReport",
            "resource_id": "dr1",
            "title": "Lab",
            "recorded_at": "2024-06-01T00:00:00",
            "text": "elevated BNP congestive heart failure volume overload",
            "summary_snippet": "CHF",
        },
        {
            "id": "b",
            "mrn": "MRN-2",
            "patient_name": "B",
            "resource_type": "DocumentReference",
            "resource_id": "doc1",
            "title": "Allergy",
            "recorded_at": "2024-06-02T00:00:00",
            "text": "seasonal allergic rhinitis birch ragweed",
            "summary_snippet": "allergy",
        },
    ]
    index.upsert_documents(docs)
    hits = index.search("heart failure BNP", top_k=5, resource_type="DiagnosticReport")
    assert hits
    assert all(h["resource_type"] == "DiagnosticReport" for h in hits)


def test_search_dedupes_twin_fhir_projections(tmp_path):
    index = SemanticSearchIndex(persist_dir=tmp_path / "chroma", collection_name="dedupe")
    index.reset()
    shared = "Sodium 138 BNP 890 pg/mL elevated volume overload HFrEF"
    docs = [
        {
            "id": "docref",
            "mrn": "MRN-1",
            "patient_name": "Maria",
            "resource_type": "DocumentReference",
            "resource_id": "d1",
            "title": "BMP & BNP panel",
            "recorded_at": "2024-11-01T00:00:00",
            "text": shared,
            "body": shared,
        },
        {
            "id": "diag",
            "mrn": "MRN-1",
            "patient_name": "Maria",
            "resource_type": "DiagnosticReport",
            "resource_id": "r1",
            "title": "BMP & BNP panel",
            "recorded_at": "2024-11-01T00:00:00",
            "text": shared,
            "body": shared,
        },
        {
            "id": "cxr",
            "mrn": "MRN-1",
            "patient_name": "Maria",
            "resource_type": "DocumentReference",
            "resource_id": "d2",
            "title": "CXR 2-view",
            "recorded_at": "2024-11-02T00:00:00",
            "text": "Cardiomegaly pulmonary vascular congestion pleural effusions",
            "body": "Cardiomegaly pulmonary vascular congestion pleural effusions",
        },
    ]
    index.upsert_documents(docs)
    hits = index.search("elevated BNP failure", top_k=5)
    titles = [h["title"] for h in hits]
    assert titles.count("BMP & BNP panel") == 1
