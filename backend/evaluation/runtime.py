"""Helpers for consuming stage-one generation outputs in evaluations."""

from __future__ import annotations

from typing import Any, Mapping


def extract_generation_output(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Read committed outputs and stage-one candidate outputs consistently.

    The full graph exposes ``answer``/``citations`` after ``commit_answer``;
    direct generation benchmarks expose ``candidate_answer``/
    ``candidate_citations``.  Evaluation code should accept both contracts
    while keeping the committed fields authoritative when present.
    """

    answer_value = result.get("answer")
    committed = isinstance(answer_value, str) and bool(answer_value.strip())
    if not committed:
        answer_value = result.get("candidate_answer", "")
    answer = str(answer_value or "").strip()

    citations_value = result.get("citations") if committed else None
    if not isinstance(citations_value, list):
        citations_value = result.get("candidate_citations", [])
    if not isinstance(citations_value, list):
        citations_value = []
    citations = [item for item in citations_value if isinstance(item, dict)]
    return answer, citations
