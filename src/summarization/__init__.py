"""AI clinical summarization with SQLite cache and offline mock fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from src.common.config import settings
from src.common.normalize import content_hash
from src.fhir.store import BundleStore


DISCLAIMER = (
    "AI-generated summary for orientation only — not a clinical decision aid. "
    "Verify against source EHR documentation before any care decision."
)

SYSTEM_PROMPT = """You are a clinical documentation assistant summarizing FHIR patient media records.
Return concise, structured plain text under 200 words with these labeled sections:
Chief concern:
Key diagnoses:
Recent media (imaging/labs):
Flagged anomalies:
Confidence: (high|moderate|low) — brief rationale

Stay faithful to the source text. Do not invent labs, imaging, or diagnoses.
If information is missing, say so explicitly.
"""


def _bundle_record_hash(bundle: dict[str, Any]) -> str:
    return content_hash(json.dumps(bundle, sort_keys=True, default=str))


def _extract_texts(bundle: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") or {}
        rtype = resource.get("resourceType")
        if rtype == "Patient":
            names = resource.get("name") or []
            if names:
                given = " ".join(names[0].get("given") or [])
                family = names[0].get("family") or ""
                chunks.append(f"Patient: {given} {family}".strip())
        elif rtype == "DocumentReference":
            chunks.append(resource.get("description") or "")
        elif rtype == "DiagnosticReport":
            title = (resource.get("code") or {}).get("text") or ""
            conclusion = resource.get("conclusion") or ""
            chunks.append(f"{title}: {conclusion}".strip())
    return [c for c in chunks if c]


def _mock_summary(mrn: str, texts: list[str]) -> dict[str, Any]:
    joined = " ".join(texts).lower()
    concerns: list[str] = []
    diagnoses: list[str] = []
    media: list[str] = []
    anomalies: list[str] = []

    patterns = [
        ("dyspnea", "dyspnea / volume overload"),
        ("chest pain", "chest pain"),
        ("cough", "cough"),
        ("rlq", "right lower quadrant pain"),
        ("appendicitis", "suspected appendicitis"),
        ("weight loss", "weight loss"),
        ("allergic", "allergic symptoms"),
        ("postpartum", "postpartum care"),
        ("pneumonia", "pneumonia symptoms"),
    ]
    for needle, label in patterns:
        if needle in joined and label not in concerns:
            concerns.append(label)

    dx_patterns = [
        ("hfref", "HFrEF / heart failure"),
        ("heart failure", "heart failure"),
        ("hypertension", "hypertension"),
        ("diabetes", "type 2 diabetes"),
        ("lung", "possible pulmonary malignancy"),
        ("appendicitis", "acute appendicitis"),
        ("pneumonia", "community-acquired pneumonia"),
        ("allergic rhinitis", "allergic rhinitis"),
    ]
    for needle, label in dx_patterns:
        if needle in joined and label not in diagnoses:
            diagnoses.append(label)

    if "bnp" in joined or "lab" in joined:
        media.append("Recent laboratory media reviewed")
    if "ct" in joined or "cxr" in joined or "pet" in joined or "imaging" in joined:
        media.append("Imaging notes present")
    if "us abdomen" in joined or "ultrasound" in joined:
        media.append("Abdominal ultrasound")

    if "spiculated" in joined or "malignancy" in joined:
        anomalies.append("Suspicious pulmonary nodule — expedite oncology workup")
    if "bnp 890" in joined or "bnp" in joined and "elevated" in joined:
        anomalies.append("Elevated BNP consistent with decompensation")
    if "non-compressible" in joined:
        anomalies.append("Imaging supports acute appendicitis")

    if not concerns:
        concerns = ["Insufficient explicit chief concern in available media"]
    if not diagnoses:
        diagnoses = ["No clear diagnosis statement extracted"]
    if not media:
        media = ["Limited structured media descriptors"]
    if not anomalies:
        anomalies = ["None clearly flagged"]

    body = (
        f"Chief concern: {'; '.join(concerns[:3])}.\n"
        f"Key diagnoses: {'; '.join(diagnoses[:4])}.\n"
        f"Recent media (imaging/labs): {'; '.join(media[:4])}.\n"
        f"Flagged anomalies: {'; '.join(anomalies[:3])}.\n"
        f"Confidence: moderate — heuristic offline summary for {mrn}; prefer live LLM when API key is set."
    )
    words = body.split()
    if len(words) > 190:
        body = " ".join(words[:190]) + "…"

    return {
        "mrn": mrn,
        "summary": body,
        "word_count": len(body.split()),
        "confidence": "moderate",
        "disclaimer": DISCLAIMER,
        "provider": "mock",
        "cached": False,
    }


def _call_openai(prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _call_anthropic(prompt: str) -> str:
    import urllib.request

    payload = json.dumps(
        {
            "model": "claude-3-5-haiku-latest",
            "max_tokens": 400,
            "temperature": 0.2,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - trusted API URL
        data = json.loads(resp.read().decode("utf-8"))
    parts = data.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def _parse_confidence(text: str) -> str:
    match = re.search(r"Confidence:\s*(high|moderate|low)", text, re.I)
    return (match.group(1).lower() if match else "moderate")


def summarize_patient(
    mrn: str,
    bundle: dict[str, Any],
    store: BundleStore,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    record_hash = _bundle_record_hash(bundle)
    cache_key = f"{mrn}:{record_hash}"

    if not force_refresh:
        cached = store.get_summary(cache_key)
        if cached:
            cached = dict(cached)
            cached["cached"] = True
            return cached

    texts = _extract_texts(bundle)
    prompt = (
        f"Patient MRN: {mrn}\n"
        f"Document count: {len(texts)}\n"
        f"Source excerpts:\n- " + "\n- ".join(texts[:40])
    )

    provider = settings.llm_provider
    if provider == "openai" and settings.openai_api_key:
        summary_text = _call_openai(prompt)
        used = "openai"
    elif provider == "anthropic" and settings.anthropic_api_key:
        summary_text = _call_anthropic(prompt)
        used = "anthropic"
    elif settings.openai_api_key:
        summary_text = _call_openai(prompt)
        used = "openai"
    elif settings.anthropic_api_key:
        summary_text = _call_anthropic(prompt)
        used = "anthropic"
    else:
        result = _mock_summary(mrn, texts)
        store.put_summary(cache_key, mrn, record_hash, result)
        return result

    words = summary_text.split()
    if len(words) > 200:
        summary_text = " ".join(words[:200]) + "…"

    result = {
        "mrn": mrn,
        "summary": summary_text,
        "word_count": len(summary_text.split()),
        "confidence": _parse_confidence(summary_text),
        "disclaimer": DISCLAIMER,
        "provider": used,
        "cached": False,
    }
    store.put_summary(cache_key, mrn, record_hash, result)
    return result


def summarize_all(store: BundleStore) -> list[dict[str, Any]]:
    summaries = []
    for item in store.list_bundles():
        summaries.append(summarize_patient(item["mrn"], item["bundle"], store))
    return summaries
