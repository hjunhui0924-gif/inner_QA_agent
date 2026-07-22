"""Application settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
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
    retrieval_strategy: str = "rerank"
    retrieval_dense_candidate_k: int = 20
    retrieval_lexical_candidate_k: int = 20
    retrieval_rerank_candidate_k: int = 12
    retrieval_rrf_k: int = 60
    retrieval_dense_weight: float = 0.5
    retrieval_lexical_weight: float = 0.5
    reranker_enabled: bool = True
    reranker_model: str = "gte-rerank-v2"
    reranker_endpoint: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/"
        "text-rerank/text-rerank"
    )
    reranker_api_style: str = "native"
    reranker_timeout_seconds: float = 10.0
    reranker_max_document_chars: int = 6000
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    embedding_index_version: str = "v3"
    chroma_collection_prefix: str = "enterprise-knowledge"
    knowledge_chunk_size: int = 800
    knowledge_chunk_overlap: int = 120
    knowledge_near_duplicate_threshold: float = 0.92
    knowledge_near_duplicate_min_length_ratio: float = 0.85
    knowledge_near_duplicate_min_length: int = 200
    max_upload_bytes: int = 20 * 1024 * 1024
    backend_url: str = "http://localhost:8000"

    @field_validator("embedding_provider")
    @classmethod
    def validate_embedding_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"dashscope", "hashing"}:
            raise ValueError("EMBEDDING_PROVIDER must be dashscope or hashing.")
        return normalized

    @field_validator("retrieval_strategy")
    @classmethod
    def validate_retrieval_strategy(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"dense", "lexical", "fusion", "rerank"}:
            raise ValueError(
                "RETRIEVAL_STRATEGY must be dense, lexical, fusion, or rerank."
            )
        return normalized

    @field_validator("reranker_api_style")
    @classmethod
    def validate_reranker_api_style(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"native", "compatible"}:
            raise ValueError("RERANKER_API_STYLE must be native or compatible.")
        return normalized


settings = Settings()
