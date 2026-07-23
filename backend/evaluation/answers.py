"""Deterministic answer, quote provenance, overlap, and abstention metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.documents import Document

from backend.agent.citations import (
    Citation,
    citation_markers,
    extractive_clause_match,
    semantic_relations_match,
)


FailureType = Literal[
    "none",
    "parse_failure",
    "retrieval_miss",
    "ranking_error",
    "generation_error",
    "gold_phrase_mismatch",
    "citation_error",
    "abstention_error",
]


@dataclass(frozen=True)
class AnswerEvalCase:
    answer: str
    expected_facts: list[str]
    answerable: bool
    retrieval_hit: bool = True
    ranking_hit: bool = True
    parse_succeeded: bool = True
    generation_succeeded: bool = True


@dataclass(frozen=True)
class AnswerEvalResult:
    gold_phrase_match_rate: float
    numeric_match_rate: float
    citation_provenance_accuracy: float
    citation_completeness: float
    extractive_overlap_rate: float
    abstention_accuracy: float
    failure_type: FailureType


_ABSTENTION_PHRASES = (
    "not enough information",
    "do not contain enough information",
    "does not contain",
    "cannot answer",
    "unable to answer",
    "insufficient information",
    "没有足够",
    "未找到",
    "无法回答",
    "信息不足",
    "未包含",
    "未规定",
    "未对",
    "不是由",
    "不存在",
)

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}


def evaluate_answer(
    case: AnswerEvalCase,
    citations: list[Citation],
    documents: list[Document],
) -> AnswerEvalResult:
    """Evaluate one answer without relying on an opaque judge model."""

    answer = case.answer.strip()
    abstained = _is_abstention(answer)
    abstention_accuracy = float(abstained if not case.answerable else not abstained)
    fact_scores = [_fact_present(fact, answer) for fact in case.expected_facts]
    gold_phrase_match_rate = _mean(fact_scores, default=1.0)
    numeric_facts = [fact for fact in case.expected_facts if _numbers(fact)]
    numeric_scores = [_numeric_fact_present(fact, answer) for fact in numeric_facts]
    numeric_accuracy = _mean(numeric_scores, default=1.0)
    citation_provenance_accuracy = _citation_provenance_accuracy(citations, documents)
    extractive_overlap_rate = _extractive_overlap_rate(answer, citations, documents)
    citation_completeness = _citation_completeness(answer) if case.answerable else 1.0

    failure: FailureType = "none"
    if not case.parse_succeeded:
        failure = "parse_failure"
    elif not case.generation_succeeded:
        failure = "generation_error"
    elif case.answerable and not case.retrieval_hit:
        failure = "retrieval_miss"
    elif case.answerable and not case.ranking_hit:
        failure = "ranking_error"
    elif abstention_accuracy < 1.0:
        failure = "abstention_error"
    elif case.answerable and (
        gold_phrase_match_rate < 1.0 or numeric_accuracy < 1.0
    ):
        failure = "gold_phrase_mismatch"
    elif case.answerable and (
        citation_provenance_accuracy < 1.0 or citation_completeness < 1.0
    ):
        failure = "citation_error"

    return AnswerEvalResult(
        gold_phrase_match_rate=gold_phrase_match_rate,
        numeric_match_rate=numeric_accuracy,
        citation_provenance_accuracy=citation_provenance_accuracy,
        citation_completeness=citation_completeness,
        extractive_overlap_rate=extractive_overlap_rate,
        abstention_accuracy=abstention_accuracy,
        failure_type=failure,
    )


def _canonical_text(text: str) -> str:
    value = text.casefold()
    for word, number in _NUMBER_WORDS.items():
        value = re.sub(rf"\b{word}\b", number, value)
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    for month, number in months.items():
        match = re.search(rf"{month}\s+(\d{{1,2}}),?\s+(\d{{4}})", value)
        if match:
            value += f" {match.group(2)}-{number}-{int(match.group(1)):02d}"
    value = re.sub(
        r"[零〇一二两三四五六七八九十百千万亿]+",
        lambda match: str(_chinese_number(match.group(0))),
        value,
    )
    return value


def _chinese_number(value: str) -> int:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}
    if all(character in digits for character in value):
        return int("".join(str(digits[character]) for character in value))
    total = section = current = 0
    for character in value:
        if character in digits:
            current = digits[character]
            continue
        unit = units[character]
        if unit < 10_000:
            section += (current or 1) * unit
        else:
            section = (section + current) * unit
            total += section
            section = 0
        current = 0
    return total + section + current


def _normalize(text: str) -> str:
    value = _canonical_text(text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def _fact_present(fact: str, answer: str) -> float:
    if not semantic_relations_match(fact, answer):
        return 0.0
    normalized_fact = _normalize(fact)
    normalized_answer = _normalize(answer)
    if normalized_fact and normalized_fact in normalized_answer:
        return 1.0
    return float(extractive_clause_match(fact, answer))


def _numbers(text: str) -> set[str]:
    values: set[str] = set()
    for item in re.findall(r"\d+(?:\.\d+)?", _canonical_text(text)):
        values.add(str(float(item)).rstrip("0").rstrip(".") if "." in item else str(int(item)))
    return values


def _numeric_fact_present(fact: str, answer: str) -> float:
    expected = _numbers(fact)
    return float(bool(expected) and expected <= _numbers(answer))


def _extractive_overlap_rate(
    answer: str,
    citations: list[Citation],
    documents: list[Document],
) -> float:
    if not citations:
        return 0.0
    by_chunk = {
        str(document.metadata.get("chunk_id", "")): document.page_content
        for document in documents
    }
    valid_citations = {
        citation["citation_id"]: citation
        for citation in citations
        if (
            citation["quote"]
            and citation["quote"]
            in " ".join(by_chunk.get(citation["chunk_id"], "").split())
        )
    }
    cited_claims = [claim for claim in _claims(answer) if citation_markers(claim)]
    if not cited_claims:
        return 0.0
    supported = 0
    for claim in cited_claims:
        quotes = [
            valid_citations[marker]["quote"]
            for marker in citation_markers(claim)
            if marker in valid_citations
        ]
        if quotes and _claim_supported(claim, " ".join(quotes)):
            supported += 1
    return supported / len(cited_claims)


def _citation_provenance_accuracy(
    citations: list[Citation], documents: list[Document]
) -> float:
    if not citations:
        return 0.0
    by_chunk = {
        str(document.metadata.get("chunk_id", "")): " ".join(document.page_content.split())
        for document in documents
    }
    valid = sum(
        1
        for citation in citations
        if citation["quote"]
        and citation["quote"] in by_chunk.get(citation["chunk_id"], "")
    )
    return valid / len(citations)


def _citation_completeness(answer: str) -> float:
    claims = [item for item in _claims(answer) if not _is_abstention(item)]
    if not claims:
        return 1.0
    cited = sum(1 for claim in claims if citation_markers(claim))
    return cited / len(claims)


def _claims(answer: str) -> list[str]:
    claims: list[str] = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-*]\s+|\d+[.、)]\s*)", "", line).strip()
        if not line or line.endswith(("：", ":")):
            continue
        chinese_parts = [
            item.strip()
            for item in re.findall(r"[^。！？!?]+[。！？!?]?", line)
            if item.strip()
        ]
        for part in chinese_parts:
            claims.extend(
                item.strip()
                for item in re.split(r"(?<=[.!?])\s+", part)
                if item.strip()
            )
    return claims


def _claim_supported(claim: str, quote: str) -> bool:
    clean_claim = re.sub(r"\[\s*C\s*\d+\s*\]", "", claim, flags=re.I)
    clean_claim = clean_claim.replace("**", "").strip()
    if not semantic_relations_match(clean_claim, quote):
        return False
    claim_numbers = _numbers(clean_claim)
    if claim_numbers and not claim_numbers <= _numbers(quote):
        return False
    return extractive_clause_match(clean_claim, quote)


def _is_abstention(answer: str) -> bool:
    lowered = answer.casefold()
    return any(phrase in lowered for phrase in _ABSTENTION_PHRASES)


def _mean(values: list[float], *, default: float) -> float:
    return sum(values) / len(values) if values else default
