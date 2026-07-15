"""Configuration helpers."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_provider: str = "openai"  # openai | anthropic | mock
    data_dir: Path = ROOT / "data"
    store_dir: Path = ROOT / "data" / "store"
    chroma_dir: Path = ROOT / "data" / "chroma"
    sqlite_path: Path = ROOT / "data" / "store" / "ehr.db"
    synthetic_dir: Path = ROOT / "data" / "synthetic"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
settings.store_dir.mkdir(parents=True, exist_ok=True)
settings.chroma_dir.mkdir(parents=True, exist_ok=True)
