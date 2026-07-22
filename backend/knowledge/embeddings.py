"""Embedding adapters and index identity management.

The embedding profile is part of the persisted index identity. Changing the
provider, model, dimensions, or index version therefore creates a new Chroma
collection instead of accidentally reading vectors produced by another model.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.embeddings import Embeddings


EmbeddingProvider = Literal["dashscope", "hashing"]


@dataclass(frozen=True)
class EmbeddingProfile:
    """Configuration that fully identifies one embedding index."""

    provider: EmbeddingProvider
    model: str
    dimensions: int
    index_version: str
    api_key: str = ""
    base_url: str = ""

    def validate(self) -> None:
        if self.dimensions <= 0:
            raise ValueError("Embedding dimensions must be greater than zero.")
        if not self.model.strip():
            raise ValueError("Embedding model must not be empty.")
        if not self.index_version.strip():
            raise ValueError("Embedding index version must not be empty.")
        if self.provider == "dashscope" and not self.api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is required when EMBEDDING_PROVIDER=dashscope. "
                "Use EMBEDDING_PROVIDER=hashing only for offline development."
            )

    def collection_name(self, prefix: str) -> str:
        """Return a stable Chroma collection name for this exact profile."""

        safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", prefix).strip("-_")
        safe_prefix = safe_prefix[:40] or "knowledge"
        identity = (
            f"{self.provider}|{self.model}|{self.dimensions}|{self.index_version}"
        )
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        return f"{safe_prefix}-{suffix}"


class HashingEmbeddings(Embeddings):
    """Deterministic lexical embedding adapter for tests and offline use.

    This adapter deliberately is not a semantic model. Production retrieval
    should use the DashScope adapter created by :func:`create_embeddings`.
    """

    def __init__(self, dimensions: int = 1024) -> None:
        if dimensions <= 0:
            raise ValueError("Embedding dimensions must be greater than zero.")
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokenize_for_hashing(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def create_embeddings(profile: EmbeddingProfile) -> Embeddings:
    """Create the embedding adapter described by ``profile``."""

    profile.validate()
    if profile.provider == "hashing":
        return HashingEmbeddings(dimensions=profile.dimensions)
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=profile.model,
        api_key=profile.api_key,
        base_url=profile.base_url,
        dimensions=profile.dimensions,
        chunk_size=10,
        check_embedding_ctx_length=False,
    )


def _tokenize_for_hashing(text: str) -> list[str]:
    """Tokenize mixed Chinese/ASCII text for the offline adapter."""

    lowered = text.casefold()
    ascii_tokens = re.findall(r"[a-z0-9_]+", lowered)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    tokens = list(ascii_tokens)
    for run in cjk_runs:
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        if len(run) >= 3:
            tokens.extend(run[index : index + 3] for index in range(len(run) - 2))
    return tokens
