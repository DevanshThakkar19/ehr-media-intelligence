# EHR Media Intelligence Platform

Synthetic-data demo for an AI full-stack internship assessment: messy EHR media → cleaned IR → HL7 FHIR R4 Bundles → AI clinical summaries → semantic search UI.

No real patient data. Everything under `data/synthetic/` is fictional.

## Quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# optional: copy .env.example → .env and set OPENAI_API_KEY or ANTHROPIC_API_KEY
# without a key, summaries use a deterministic offline mock (still cached)

# run tests
pytest -q

# run API + UI (pipeline indexes on startup)
# Important: only watch src/frontend — never .venv (reload storms otherwise)
uvicorn src.api.main:app --reload --reload-dir src --reload-dir frontend --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Useful endpoints:
- `POST /search` or `POST /api/search` — natural language search (top-5)
- `GET /api/patients/{mrn}` — full AI summary + linked FHIR resources
- `POST /api/pipeline/run` — re-ingest / rebuild index
- `GET /api/health` — readiness

## What each task does

| Task | Location | Behavior |
|------|----------|----------|
| 1 Ingestion | `src/ingestion/` | JSON + CSV + free-text; MRN/DOB/gender/date normalization; dedupe; conflict resolution; Pydantic IR + audit log |
| 2 FHIR R4 | `src/fhir/` | Patient, DocumentReference, DiagnosticReport → Bundle per MRN; schema validation; SQLite store |
| 3 Summaries | `src/summarization/` | OpenAI / Anthropic / mock; ≤200 words; SQLite cache keyed by `mrn + bundle hash`; disclaimer |
| 4 Search | `src/search/` | `all-MiniLM-L6-v2` + ChromaDB; filters for resource type + date range |
| 5 UI | `frontend/` | Tailwind + vanilla JS; result cards, filters, accessible drawer |

## Design decisions

**Canonical IR before FHIR.** Mapping straight from messy exports into FHIR usually hides data-quality bugs. A small Pydantic intermediate model makes cleaning testable and keeps the FHIR layer honest.

**Collection Bundles, not transaction Bundles.** This assessment is about retrieval/search, not writing back to Epic. `type=collection` is the right shape for “everything we know about this patient media set.”

**Mock summarizer when no API key.** Recruiters and CI should be able to run the demo offline. The mock is intentionally boring and sourced only from the text — live LLM path is a config flip.

**Chroma over FAISS.** Local persistence without a separate service, and good enough latency for ~50 records.

**Vanilla JS + Tailwind CDN.** Meets the brief without a Node toolchain. For a production clinician console I’d still pick React, but the assessment grades product clarity more than bundler setup.

## Tradeoffs

- Document narrative lives in `DocumentReference.description` / `DiagnosticReport.conclusion` rather than base64 `Attachment.data`, so search and summaries stay simple.
- Identity resolution fills a missing MRN when name+DOB match a known patient; conflicting MRNs for the same person are *not* merged (safer clinically).
- Embeddings recompute on pipeline run; no incremental index updates.
- Startup downloads the MiniLM model on first run (one-time).

## What I’d improve with more time

- US Core profiles + Encounter resources instead of dangling Encounter references
- OCR path for real scanned PDFs
- Eval harness for summary faithfulness (checklist vs. source spans)
- AuthN/Z and audit logging suitable for a hospital VPC
- Hybrid BM25 + vector retrieval for MRN / accession exact matches

## FHIR / clinical concepts researched

- FHIR R4 `Patient`, `DocumentReference`, `DiagnosticReport`, and `Bundle` reference rules (`subject`, `encounter`)
- LOINC document codes for discharge, imaging, lab, and progress notes
- Gender coding toward administrative gender (`male`/`female`/`other`/`unknown`) rather than clinical sex

## How AI summary quality was validated

1. Offline mock: assert structured sections exist, word count ≤200, disclaimer present, and cache hits on re-run (`tests/test_summarization.py`).
2. Manual spot-checks on each synthetic patient (CHF/BNP, pulmonary nodule, appendicitis, CAP, allergy) confirming summaries do not invent diagnoses absent from source text.
3. With a live API key: same spot-checks plus temperature 0.2 and a system prompt that forbids fabrication.

## Commit history

One commit per major task (ingestion → FHIR → summarization → search → UI), plus README/write-up.

## License

MIT — assessment portfolio use.
