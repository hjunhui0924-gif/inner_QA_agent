"""Run a minimal RAG evaluation for one uploaded enterprise document."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from langchain_core.messages import HumanMessage

from backend.agent.memory import ensure_vectorstore, search_documents
from backend.agent.nodes import generate

EVAL_DIR = BASE_DIR / "data" / "evals"
REPORT_DIR = BASE_DIR / "data" / "eval_reports"


@dataclass
class EvalCaseResult:
    """Evaluation result for one question."""

    id: str
    question: str
    gold_answer: str
    hit_at_k: bool
    matched_source: bool
    keyword_match_ratio: float
    retrieved_titles: list[str]
    generated_answer: str
    answer_keyword_match_ratio: float
    answer_contains_gold: bool


def _load_eval_cases(path: Path) -> list[dict[str, Any]]:
    """Load evaluation cases from JSON."""

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Evaluation file must contain a list.")
    return data


def _keyword_match_ratio(expected_keywords: list[str], contexts: list[str]) -> float:
    """Compute a simple keyword coverage ratio."""

    if not expected_keywords:
        return 1.0
    combined = "\n".join(contexts)
    matched = 0
    for keyword in expected_keywords:
        if keyword and keyword in combined:
            matched += 1
    return matched / len(expected_keywords)


def _answer_contains_gold(answer: str, gold_answer: str) -> bool:
    """Return whether the answer contains the gold answer or a core fragment."""

    normalized_answer = " ".join(answer.split())
    normalized_gold = " ".join(gold_answer.split())
    if normalized_gold and normalized_gold in normalized_answer:
        return True
    gold_fragments = [fragment for fragment in normalized_gold.split("。") if fragment]
    return any(fragment in normalized_answer for fragment in gold_fragments)


async def _generate_answer_for_eval(
    question: str,
    documents: list[Any],
) -> str:
    """Generate an answer using the existing agent generate node."""

    state: dict[str, Any] = {
        "query": question,
        "messages": [HumanMessage(content=question)],
        "retrieved_docs": documents,
        "tool_output": "",
        "route": "rag",
    }
    try:
        result = await generate(state)
    except Exception as exc:
        return f"[GENERATION_ERROR] {exc}"
    return str(result.get("answer", "")).strip()


async def _run_eval(eval_file: Path, top_k: int = 4) -> dict[str, Any]:
    """Run retrieval-oriented evaluation and return a report."""

    await ensure_vectorstore()
    cases = _load_eval_cases(eval_file)
    results: list[EvalCaseResult] = []

    for case in cases:
        question = str(case.get("question", "")).strip()
        gold_answer = str(case.get("gold_answer", "")).strip()
        source_title = str(case.get("source_title", "")).strip()
        expected_keywords = case.get("expected_keywords", [])
        if not isinstance(expected_keywords, list):
            expected_keywords = []

        documents = await search_documents(question, top_k=top_k)
        retrieved_titles = [
            str(document.metadata.get("title", "")).strip() for document in documents
        ]
        contexts = [document.page_content for document in documents]
        matched_source = any(title == source_title for title in retrieved_titles)
        keyword_ratio = _keyword_match_ratio(
            [str(item) for item in expected_keywords],
            contexts,
        )
        generated_answer = await _generate_answer_for_eval(question, documents)
        answer_keyword_ratio = _keyword_match_ratio(
            [str(item) for item in expected_keywords],
            [generated_answer],
        )
        answer_contains_gold = _answer_contains_gold(generated_answer, gold_answer)

        results.append(
            EvalCaseResult(
                id=str(case.get("id", "")),
                question=question,
                gold_answer=gold_answer,
                hit_at_k=matched_source,
                matched_source=matched_source,
                keyword_match_ratio=keyword_ratio,
                retrieved_titles=retrieved_titles,
                generated_answer=generated_answer,
                answer_keyword_match_ratio=answer_keyword_ratio,
                answer_contains_gold=answer_contains_gold,
            )
        )

    hit_count = sum(1 for result in results if result.hit_at_k)
    avg_keyword_ratio = (
        sum(result.keyword_match_ratio for result in results) / len(results)
        if results
        else 0.0
    )
    avg_answer_keyword_ratio = (
        sum(result.answer_keyword_match_ratio for result in results) / len(results)
        if results
        else 0.0
    )
    answer_contains_gold_rate = (
        sum(1 for result in results if result.answer_contains_gold) / len(results)
        if results
        else 0.0
    )
    report = {
        "eval_file": str(eval_file.relative_to(BASE_DIR)),
        "question_count": len(results),
        "hit_at_k_rate": hit_count / len(results) if results else 0.0,
        "average_keyword_match_ratio": avg_keyword_ratio,
        "average_answer_keyword_match_ratio": avg_answer_keyword_ratio,
        "answer_contains_gold_rate": answer_contains_gold_rate,
        "results": [asdict(result) for result in results],
    }
    return report


def _write_report(report: dict[str, Any], eval_file: Path) -> Path:
    """Persist the evaluation report as JSON."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REPORT_DIR / f"{eval_file.stem}_report.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return output_path


async def main() -> None:
    """Run the default evaluation."""

    eval_file = EVAL_DIR / "dongshan_legal_opinion_eval.json"
    report = await _run_eval(eval_file)
    output_path = _write_report(report, eval_file)
    summary = {
        "eval_file": report["eval_file"],
        "question_count": report["question_count"],
        "hit_at_k_rate": report["hit_at_k_rate"],
        "average_keyword_match_ratio": report["average_keyword_match_ratio"],
        "average_answer_keyword_match_ratio": report["average_answer_keyword_match_ratio"],
        "answer_contains_gold_rate": report["answer_contains_gold_rate"],
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print(f"\nReport written to: {output_path}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
