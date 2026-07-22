"""Reranker ports and DashScope adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from langchain_core.documents import Document


@dataclass(frozen=True)
class RerankScore:
    """One reranked candidate identified by its original list index."""

    index: int
    score: float


class Reranker(Protocol):
    """Port implemented by production and deterministic test adapters."""

    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_n: int,
    ) -> list[RerankScore]: ...


class DashScopeReranker:
    """Call a DashScope text rerank endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gte-rerank-v2",
        endpoint: str = (
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/"
            "text-rerank/text-rerank"
        ),
        api_style: Literal["native", "compatible"] = "native",
        timeout_seconds: float = 10.0,
        max_document_chars: int = 6000,
        instruct: str = "",
    ) -> None:
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for reranking.")
        if timeout_seconds <= 0 or max_document_chars <= 0:
            raise ValueError("Reranker timeout and document limit must be positive.")
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._api_style = api_style
        self._timeout_seconds = timeout_seconds
        self._max_document_chars = max_document_chars
        self._instruct = instruct

    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_n: int,
    ) -> list[RerankScore]:
        if not query.strip() or not documents or top_n <= 0:
            return []
        texts = [
            document.page_content[: self._max_document_chars]
            for document in documents
        ]
        top_n = min(top_n, len(texts))
        payload = self._payload(query, texts, top_n)
        response = httpx.post(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        raw_results = (
            data.get("output", {}).get("results", [])
            if self._api_style == "native"
            else data.get("results", [])
        )
        results: list[RerankScore] = []
        seen: set[int] = set()
        for item in raw_results:
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or index < 0
                or index >= len(documents)
                or index in seen
            ):
                continue
            seen.add(index)
            results.append(RerankScore(index=index, score=float(score)))
        if not results:
            raise ValueError("Reranker returned no valid candidates.")
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_n]

    def _payload(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> dict[str, object]:
        if self._api_style == "compatible":
            payload: dict[str, object] = {
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            }
            if self._instruct:
                payload["instruct"] = self._instruct
            return payload
        return {
            "model": self._model,
            "input": {"query": query, "documents": documents},
            "parameters": {
                "return_documents": False,
                "top_n": top_n,
            },
        }
