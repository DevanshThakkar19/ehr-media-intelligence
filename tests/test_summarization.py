"""Summary cache behavior."""

from __future__ import annotations

from pathlib import Path

from src.fhir.store import BundleStore
from src.summarization import summarize_patient


def test_summary_cache_avoids_recomputation(tmp_path: Path):
    store = BundleStore(tmp_path / "test.db")
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "patient-mrn-1",
                    "name": [{"family": "Test", "given": ["Pat"]}],
                }
            },
            {
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "doc-1",
                    "description": "Chief complaint dyspnea. Diagnosis HFrEF. BNP elevated.",
                }
            },
        ],
    }
    first = summarize_patient("MRN-1", bundle, store)
    second = summarize_patient("MRN-1", bundle, store)
    assert first["cached"] is False
    assert second["cached"] is True
    assert "disclaimer" in first
    assert first["word_count"] <= 200
