"""SQLite persistence for FHIR Bundles and metadata."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fhir.resources.R4B.bundle import Bundle

from src.fhir.mapper import bundle_to_dict


class BundleStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fhir_bundles (
                    mrn TEXT PRIMARY KEY,
                    patient_fhir_id TEXT NOT NULL,
                    bundle_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS validation_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS summary_cache (
                    cache_key TEXT PRIMARY KEY,
                    mrn TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def upsert_bundle(self, mrn: str, patient_fhir_id: str, bundle: Bundle) -> None:
        payload = json.dumps(bundle_to_dict(bundle))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fhir_bundles (mrn, patient_fhir_id, bundle_json, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(mrn) DO UPDATE SET
                    patient_fhir_id=excluded.patient_fhir_id,
                    bundle_json=excluded.bundle_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (mrn, patient_fhir_id, payload),
            )

    def save_validation_report(self, report: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO validation_reports (report_json) VALUES (?)",
                (json.dumps(report),),
            )

    def get_bundle(self, mrn: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT bundle_json FROM fhir_bundles WHERE mrn = ?", (mrn,)
            ).fetchone()
        if not row:
            return None
        return json.loads(row["bundle_json"])

    def list_bundles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT mrn, patient_fhir_id, bundle_json FROM fhir_bundles ORDER BY mrn"
            ).fetchall()
        return [
            {
                "mrn": r["mrn"],
                "patient_fhir_id": r["patient_fhir_id"],
                "bundle": json.loads(r["bundle_json"]),
            }
            for r in rows
        ]

    def get_summary(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary_json FROM summary_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["summary_json"])

    def put_summary(self, cache_key: str, mrn: str, record_hash: str, summary: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO summary_cache (cache_key, mrn, record_hash, summary_json, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(cache_key) DO UPDATE SET
                    summary_json=excluded.summary_json,
                    record_hash=excluded.record_hash,
                    created_at=CURRENT_TIMESTAMP
                """,
                (cache_key, mrn, record_hash, json.dumps(summary)),
            )
