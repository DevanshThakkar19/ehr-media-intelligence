"""FastAPI application: pipeline bootstrap + semantic search UI API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.common.config import settings
from src.fhir.mapper import extract_searchable_documents, records_to_bundles, validation_report
from src.fhir.store import BundleStore
from src.ingestion.pipeline import ingest_many
from src.search import SemanticSearchIndex, build_index_documents
from src.summarization import summarize_all, summarize_patient
from fhir.resources.R4B.bundle import Bundle

app = FastAPI(
    title="EHR Media Intelligence Platform",
    description="Ingest messy EHR media → FHIR R4 → AI summaries → semantic search",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

store = BundleStore(settings.sqlite_path)
search_index = SemanticSearchIndex(settings.chroma_dir)

# In-memory convenience for patient detail drawer
_STATE: dict[str, Any] = {
    "summaries": {},
    "validation": None,
    "ready": False,
}


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language clinical query")
    top_k: int = Field(5, ge=1, le=20)
    resource_type: str | None = None
    date_from: str | None = Field(None, description="YYYY-MM-DD")
    date_to: str | None = Field(None, description="YYYY-MM-DD")


class SearchHit(BaseModel):
    id: str
    mrn: str | None
    patient_name: str | None
    resource_type: str
    resource_id: str | None
    title: str | None
    recorded_at: str | None
    relevance_score: float
    snippet: str
    summary_snippet: str


class SearchResponse(BaseModel):
    query: str
    took_ms: float
    results: list[SearchHit]


def run_pipeline(*, reindex: bool = True) -> dict[str, Any]:
    paths = sorted(settings.synthetic_dir.glob("*"))
    paths = [p for p in paths if p.suffix.lower() in {".json", ".csv", ".txt"}]
    ingestion = ingest_many(paths)
    fhir_result = records_to_bundles(ingestion.records)
    report = validation_report(fhir_result)
    store.save_validation_report(report)

    for mrn, bundle in fhir_result.bundles.items():
        store.upsert_bundle(mrn, fhir_result.patient_ids[mrn], bundle)

    summaries = summarize_all(store)
    summaries_by_mrn = {s["mrn"]: s for s in summaries}
    _STATE["summaries"] = summaries_by_mrn
    _STATE["validation"] = report

    if reindex:
        search_index.reset()
        searchable: list[dict[str, Any]] = []
        for item in store.list_bundles():
            bundle_obj = Bundle.parse_obj(item["bundle"])
            docs = extract_searchable_documents(bundle_obj, item["mrn"])
            # ensure patient names filled from summaries if needed
            for d in docs:
                if not d.get("patient_name"):
                    # parse from patient resource already handled in extractor
                    pass
            searchable.extend(docs)
        indexed = build_index_documents(searchable, summaries_by_mrn)
        search_index.upsert_documents(indexed)

    _STATE["ready"] = True
    return {
        "records_ingested": len(ingestion.records),
        "duplicates_removed": ingestion.duplicates_removed,
        "skipped": ingestion.skipped,
        "bundles": len(fhir_result.bundles),
        "validation": report,
        "summaries": len(summaries),
        "indexed_documents": search_index._get_collection().count() if reindex else None,
    }


@app.on_event("startup")
def _startup() -> None:
    # Boot with synthetic data so the UI works out of the box
    try:
        run_pipeline(reindex=True)
    except Exception as exc:  # noqa: BLE001
        _STATE["ready"] = False
        _STATE["startup_error"] = str(exc)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if _STATE.get("ready") else "degraded",
        "ready": bool(_STATE.get("ready")),
        "startup_error": _STATE.get("startup_error"),
        "bundle_count": len(store.list_bundles()),
    }


@app.post("/api/pipeline/run")
def api_run_pipeline() -> dict[str, Any]:
    return run_pipeline(reindex=True)


@app.post("/search", response_model=SearchResponse)
@app.post("/api/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    resource_type: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
) -> SearchResponse:
    started = datetime.utcnow()
    rtype = body.resource_type or resource_type
    d_from = body.date_from or date_from
    d_to = body.date_to or date_to
    hits = search_index.search(
        body.query,
        top_k=body.top_k,
        resource_type=rtype,
        date_from=d_from,
        date_to=d_to,
    )
    took = (datetime.utcnow() - started).total_seconds() * 1000
    return SearchResponse(
        query=body.query,
        took_ms=round(took, 2),
        results=[SearchHit(**h) for h in hits],
    )


@app.get("/api/patients")
def list_patients() -> list[dict[str, Any]]:
    out = []
    for item in store.list_bundles():
        mrn = item["mrn"]
        summary = _STATE["summaries"].get(mrn) or store.get_summary(f"{mrn}:")
        # resolve patient name from bundle
        name = ""
        for entry in item["bundle"].get("entry") or []:
            res = entry.get("resource") or {}
            if res.get("resourceType") == "Patient":
                names = res.get("name") or []
                if names:
                    given = " ".join(names[0].get("given") or [])
                    name = f"{given} {names[0].get('family') or ''}".strip()
        out.append(
            {
                "mrn": mrn,
                "patient_name": name,
                "summary_snippet": ((summary or {}).get("summary") or "")[:180],
            }
        )
    return out


@app.get("/api/patients/{mrn}")
def patient_detail(mrn: str) -> dict[str, Any]:
    key = _resolve_mrn(mrn)
    if key is None:
        raise HTTPException(status_code=404, detail=f"Patient {mrn} not found")
    bundle = store.get_bundle(key)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Bundle missing for {mrn}")

    summary = _STATE["summaries"].get(key)
    if summary is None:
        summary = summarize_patient(key, bundle, store)

    patient_name = ""
    resources = []
    seen_media_titles: set[str] = set()
    for entry in bundle.get("entry") or []:
        res = entry.get("resource") or {}
        rtype = res.get("resourceType")
        if rtype == "Patient":
            names = res.get("name") or []
            if names:
                given = " ".join(names[0].get("given") or [])
                patient_name = f"{given} {names[0].get('family') or ''}".strip()
        display = (
            res.get("description")
            or (res.get("code") or {}).get("text")
            or res.get("id")
            or ""
        )
        if isinstance(display, str) and " | " in display:
            display = display.split(" | ", 1)[0].strip()
        # Labs/notes are emitted as both DocumentReference and DiagnosticReport —
        # show one row per clinical media item in the drawer.
        if rtype in {"DocumentReference", "DiagnosticReport"}:
            title_key = (display or "").strip().lower()
            if title_key and title_key in seen_media_titles:
                continue
            if title_key:
                seen_media_titles.add(title_key)
        resources.append(
            {
                "resourceType": rtype,
                "id": res.get("id"),
                "display": (display or "")[:180],
            }
        )
    return {
        "mrn": key,
        "patient_name": patient_name,
        "bundle": bundle,
        "summary": summary,
        "resources": resources,
        "validation": _STATE.get("validation"),
    }


def _resolve_mrn(mrn: str) -> str | None:
    if store.get_bundle(mrn):
        return mrn
    upper = mrn.upper()
    if store.get_bundle(upper):
        return upper
    if not upper.startswith("MRN-"):
        candidate = f"MRN-{upper}"
        if store.get_bundle(candidate):
            return candidate
    needle = upper.replace("MRN-", "")
    for item in store.list_bundles():
        if item["mrn"].replace("MRN-", "") == needle or item["mrn"] == upper:
            return item["mrn"]
    return None


@app.get("/api/validation")
def get_validation() -> dict[str, Any]:
    return _STATE.get("validation") or {"ok": None, "issues": []}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
