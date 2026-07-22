"""Run retrieval ablations on the official policy corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from langchain_chroma import Chroma
from langchain_core.documents import Document

from backend.config import settings
from backend.evaluation.retrieval import (
    parse_cases,
    run_retrieval_ablation,
    validate_cases_against_corpus,
)
from backend.knowledge.chunking import split_text
from backend.knowledge.embeddings import EmbeddingProfile, create_embeddings
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine
from backend.retrieval.reranker import DashScopeReranker


CORPUS_DIR = BASE_DIR / "data" / "eval_corpus"
PROVENANCE_PATH = CORPUS_DIR / "provenance.json"
EVAL_PATH = BASE_DIR / "data" / "evals" / "official_policy_retrieval_eval.json"
INDEX_DIR = BASE_DIR / "data" / "eval_indexes" / "official_policy"
REPORT_PATH = (
    BASE_DIR / "data" / "eval_reports" / "official_policy_retrieval_benchmark.json"
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_documents(provenance: list[dict[str, Any]]) -> list[Document]:
    documents: list[Document] = []
    for source in provenance:
        path = BASE_DIR / source["local_path"]
        content = path.read_text(encoding="utf-8")
        for index, chunk in enumerate(
            split_text(
                content,
                chunk_size=settings.knowledge_chunk_size,
                overlap=settings.knowledge_chunk_overlap,
            )
        ):
            chunk_id = f"{source['content_sha256']}:{index}"
            documents.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "chunk_id": chunk_id,
                        "chunk_index": index,
                        "source_id": source["id"],
                        "title": source["title"],
                        "publisher": source["publisher"],
                        "source_url": source["url"],
                    },
                )
            )
    return documents


def _build_engine(
    documents: list[Document],
    provenance: list[dict[str, Any]],
    *,
    enable_rerank: bool,
) -> tuple[RetrievalEngine, dict[str, Any]]:
    profile = EmbeddingProfile(
        provider=settings.embedding_provider,  # type: ignore[arg-type]
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        index_version=settings.embedding_index_version,
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
    )
    corpus_identity = hashlib.sha256(
        "|".join(item["content_sha256"] for item in provenance).encode("utf-8")
    ).hexdigest()
    collection_name = profile.collection_name(f"official-{corpus_identity[:10]}")
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    store = Chroma(
        collection_name=collection_name,
        embedding_function=create_embeddings(profile),
        persist_directory=str(INDEX_DIR),
    )
    existing_ids = set(store.get(include=[]).get("ids", []))
    missing = [
        document
        for document in documents
        if str(document.metadata["chunk_id"]) not in existing_ids
    ]
    if missing:
        store.add_documents(
            missing,
            ids=[str(document.metadata["chunk_id"]) for document in missing],
        )

    reranker = None
    if enable_rerank:
        reranker = DashScopeReranker(
            api_key=settings.dashscope_api_key,
            model=settings.reranker_model,
            endpoint=settings.reranker_endpoint,
            api_style=settings.reranker_api_style,  # type: ignore[arg-type]
            timeout_seconds=settings.reranker_timeout_seconds,
            max_document_chars=settings.reranker_max_document_chars,
        )
    config = RetrievalConfig(
        dense_candidate_k=settings.retrieval_dense_candidate_k,
        lexical_candidate_k=settings.retrieval_lexical_candidate_k,
        rerank_candidate_k=settings.retrieval_rerank_candidate_k,
        rrf_k=settings.retrieval_rrf_k,
        dense_weight=settings.retrieval_dense_weight,
        lexical_weight=settings.retrieval_lexical_weight,
        production_strategy=settings.retrieval_strategy,  # type: ignore[arg-type]
    )
    engine = RetrievalEngine(
        store,
        documents,
        config=config,
        reranker=reranker,
    )
    metadata = {
        "corpus_identity": corpus_identity,
        "collection_name": collection_name,
        "document_count": len(provenance),
        "chunk_count": len(documents),
        "embedding_provider": profile.provider,
        "embedding_model": profile.model,
        "embedding_dimensions": profile.dimensions,
        "retrieval_config": {
            "dense_candidate_k": config.dense_candidate_k,
            "lexical_candidate_k": config.lexical_candidate_k,
            "rerank_candidate_k": config.rerank_candidate_k,
            "rrf_k": config.rrf_k,
            "dense_weight": config.dense_weight,
            "lexical_weight": config.lexical_weight,
        },
        "reranker_model": settings.reranker_model if enable_rerank else None,
    }
    return engine, metadata


def run_benchmark(
    *,
    top_k: int = 5,
    strategies: list[str] | None = None,
    enable_rerank: bool = True,
) -> dict[str, Any]:
    provenance = _load_json(PROVENANCE_PATH)
    documents = _build_documents(provenance)
    cases = parse_cases(_load_json(EVAL_PATH))
    validate_cases_against_corpus(cases, documents)
    engine, metadata = _build_engine(
        documents,
        provenance,
        enable_rerank=enable_rerank,
    )
    selected = strategies or ["rerank", "fusion", "dense", "lexical"]
    report = {
        "eval_file": str(EVAL_PATH.relative_to(BASE_DIR)),
        "provenance_file": str(PROVENANCE_PATH.relative_to(BASE_DIR)),
        "top_k": top_k,
        **metadata,
        "strategies": run_retrieval_ablation(
            engine,
            cases,
            strategies=selected,  # type: ignore[arg-type]
            top_k=top_k,
        ),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--strategies",
        default="rerank,fusion,dense,lexical",
        help="Comma-separated subset of rerank,fusion,dense,lexical",
    )
    parser.add_argument("--disable-rerank", action="store_true")
    args = parser.parse_args()
    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    allowed = {"rerank", "fusion", "dense", "lexical"}
    if not strategies or not set(strategies) <= allowed:
        raise ValueError(f"Unknown strategies: {strategies}")
    report = run_benchmark(
        top_k=args.top_k,
        strategies=strategies,
        enable_rerank=not args.disable_rerank,
    )
    summary = {
        strategy: {
            key: value
            for key, value in metrics.items()
            if key
            in {
                "source_hit_at_k",
                "mrr_at_k",
                "ndcg_at_k",
                "evidence_recall_at_k",
                "full_evidence_hit_rate",
                "latency_p50_ms",
                "latency_p95_ms",
                "rerank_degradation_count",
                "answerable_min_top_rerank_score",
                "no_answer_max_top_rerank_score",
            }
        }
        for strategy, metrics in report["strategies"].items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Report written to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
