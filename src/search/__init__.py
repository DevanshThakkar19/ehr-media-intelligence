"""Semantic search over FHIR documents and AI summaries."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.common.config import settings


class SemanticSearchIndex:
    """Chroma-backed vector index with lazy model loading."""

    def __init__(self, persist_dir: Path | None = None, collection_name: str = "ehr_media"):
        self.persist_dir = Path(persist_dir or settings.chroma_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self._model = None
        self._collection = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(settings.embedding_model)
        return self._model

    def _get_collection(self):
        if self._collection is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def reset(self) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        try:
            client.delete_collection(self.collection_name)
        except Exception:  # noqa: BLE001
            pass
        self._collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def upsert_documents(self, documents: list[dict[str, Any]]) -> int:
        if not documents:
            return 0
        collection = self._get_collection()
        ids = [d["id"] for d in documents]
        texts = [d["text"] for d in documents]
        embeddings = self.embed(texts)
        metadatas = []
        for d in documents:
            body = (d.get("body") or d.get("text") or "")[:400]
            metadatas.append(
                {
                    "mrn": d.get("mrn") or "",
                    "patient_name": d.get("patient_name") or "",
                    "resource_type": d.get("resource_type") or "",
                    "resource_id": d.get("resource_id") or "",
                    "title": (d.get("title") or "")[:200],
                    "recorded_at": d.get("recorded_at") or "",
                    "body_snippet": body[:280],
                    "summary_snippet": (d.get("summary_snippet") or "")[:180],
                }
            )
        collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
        return len(documents)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        resource_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._get_collection()
        if collection.count() == 0:
            return []

        query_emb = self.embed([query])[0]
        # Over-fetch then filter/dedupe — DocRef + DiagnosticReport often share content
        n_results = min(max(top_k * 10, top_k), max(collection.count(), 1))
        raw = collection.query(
            query_embeddings=[query_emb],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        results: list[dict[str, Any]] = []
        seen_media: set[str] = set()
        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        dists = (raw.get("distances") or [[]])[0]

        for doc_id, document, meta, dist in zip(ids, docs, metas, dists):
            meta = meta or {}
            rtype = meta.get("resource_type") or ""
            if resource_type and rtype.lower() != resource_type.lower():
                continue
            recorded = meta.get("recorded_at") or ""
            if date_from and recorded and recorded[:10] < date_from[:10]:
                continue
            if date_to and recorded and recorded[:10] > date_to[:10]:
                continue

            # cosine distance -> relevance score in [0,1]
            score = max(0.0, 1.0 - float(dist))
            body = meta.get("body_snippet") or ""
            if not body and document:
                body = document.split("\n")[0][:280] if "\n" in document else document[:280]
            title = meta.get("title") or ""

            # Prefer primary clinical media over synthetic AI summary hits
            if rtype == "PatientSummary":
                score = max(0.0, score - 0.22)

            # Lexical boost when query tokens appear in title/body
            q_tokens = [t for t in query.lower().replace("/", " ").split() if len(t) > 2]
            hay = f"{title}\n{body}".lower()
            if q_tokens:
                hits = sum(1 for t in q_tokens if t in hay)
                score = min(1.0, score + 0.08 * hits)

            # Collapse twin FHIR projections of the same clinical media
            media_key = "|".join(
                [
                    meta.get("mrn") or "",
                    (title or "").strip().lower(),
                    recorded[:10],
                    (body or "")[:80].strip().lower(),
                ]
            )
            if media_key in seen_media:
                continue
            seen_media.add(media_key)

            results.append(
                {
                    "id": doc_id,
                    "mrn": meta.get("mrn"),
                    "patient_name": meta.get("patient_name"),
                    "resource_type": rtype,
                    "resource_id": meta.get("resource_id"),
                    "title": title,
                    "recorded_at": recorded,
                    "relevance_score": round(score, 4),
                    "snippet": body or (document or "")[:280],
                    "summary_snippet": meta.get("summary_snippet") or "",
                }
            )
            # Keep a larger candidate pool; rank after score adjustments
            if len(results) >= top_k * 3:
                break
        results.sort(key=lambda h: h["relevance_score"], reverse=True)
        return results[:top_k]


def build_index_documents(
    searchable_docs: list[dict[str, Any]],
    summaries_by_mrn: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for doc in searchable_docs:
        mrn = doc["mrn"]
        summary = summaries_by_mrn.get(mrn) or {}
        summary_text = summary.get("summary") or ""
        body = (doc.get("text") or "").strip()
        # Description may be "Title | content" — use content portion for snippet
        if " | " in body:
            body = body.split(" | ", 1)[-1].strip()
        # Embed title + media body only (summary is indexed separately as PatientSummary)
        text = f"{doc.get('title') or ''}\n{body}".strip()
        indexed.append(
            {
                "id": f"{mrn}:{doc.get('resource_type')}:{doc.get('resource_id')}",
                "mrn": mrn,
                "patient_name": doc.get("patient_name") or "",
                "resource_type": doc.get("resource_type") or "",
                "resource_id": doc.get("resource_id") or "",
                "title": doc.get("title") or "",
                "recorded_at": doc.get("recorded_at") or "",
                "text": text,
                "body": body,
                "summary_snippet": summary_text[:240],
            }
        )

    # Also index the summary itself as a PatientSummary virtual resource
    for mrn, summary in summaries_by_mrn.items():
        indexed.append(
            {
                "id": f"{mrn}:PatientSummary:summary",
                "mrn": mrn,
                "patient_name": next(
                    (d.get("patient_name") for d in searchable_docs if d.get("mrn") == mrn),
                    "",
                ),
                "resource_type": "PatientSummary",
                "resource_id": "summary",
                "title": f"AI clinical summary — {mrn}",
                "recorded_at": datetime.utcnow().isoformat(),
                "text": summary.get("summary") or "",
                "body": summary.get("summary") or "",
                "summary_snippet": (summary.get("summary") or "")[:240],
            }
        )
    return indexed
