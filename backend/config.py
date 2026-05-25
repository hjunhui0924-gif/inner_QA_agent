"""Application settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Central application configuration."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dashscope_api_key: str = Field(default="", validation_alias="DASHSCOPE_API_KEY")
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name: str = "qwen-plus"
    chroma_persist_dir: str = str(BASE_DIR / "data" / "chroma_db")
    sqlite_db_path: str = str(BASE_DIR / "data" / "memory.db")
    knowledge_base_path: str = str(BASE_DIR / "data" / "knowledge_base.json")
    upload_dir: str = str(BASE_DIR / "data" / "uploads")
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    max_retrieval_retries: int = 2
    max_hallucination_retries: int = 1
    retrieval_top_k: int = 4
    embedding_dim: int = 256
    knowledge_dedup_similarity_threshold: float = 0.65
    backend_url: str = "http://localhost:8000"


settings = Settings()
