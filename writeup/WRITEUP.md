# EHR Media Intelligence — Design Write-up

**Devansh Thakkar** · AI Full-Stack Internship Assessment

## Approach

I treated this as a clinical data pipeline with a thin product surface, not a chat demo bolted onto raw files. Messy exports are cleaned into a validated intermediate model, mapped to FHIR R4 Bundles, summarized with an LLM (or a faithful offline mock), embedded for semantic retrieval, and exposed through a FastAPI + Tailwind clinician UI.

## Tradeoffs and why

- **IR before FHIR.** Cleaning bugs are easier to unit-test on Pydantic models than after LOINC coding. The cost is one extra hop; the win is clearer audit logs and safer mapping.
- **SQLite + Chroma instead of a heavier stack.** Fits a local assessment, keeps setup to `pip install` + `uvicorn`, and still hits sub-2s search on ~50 records.
- **Mock summarizer without API keys.** Guarantees reviewers can run everything. Live OpenAI/Anthropic paths are enabled via `.env`.
- **Vanilla JS UI.** Matches the brief and avoids hiding the API behind a SPA build. Production would use a design-system React console.

## What I researched

FHIR R4 reference integrity (`Patient`/`DocumentReference`/`DiagnosticReport`), Bundle `collection` vs `transaction`, LOINC document type codes, and administrative gender coding. Clinically, I kept summaries orientation-only and labeled them as non-decisional.

## Validating AI summary quality

Cached outputs are pinned by `patient MRN + bundle hash`. Tests enforce section structure, ≤200 words, and an explicit disclaimer. I manually compared each synthetic patient’s summary to source media (CHF/BNP, nodule/PET, appendicitis US, CAP, allergy) and required that diagnoses appear only when present in source text. With a live key, the same checklist applies at temperature 0.2.

## With more time

US Core profiles and real Encounter resources; PDF/OCR ingestion; a faithfulness eval harness; role-based access; and hybrid lexical+vector search for identifiers.
