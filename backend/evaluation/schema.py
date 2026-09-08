"""Shared schema and validation for RAG evaluation datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.documents import Document


EvalSplit = Literal["development", "regression", "held_out"]
Answerability = Literal["answerable", "unanswerable"]

VALID_SPLITS = frozenset({"development", "regression", "held_out"})
VALID_ANSWERABILITY = frozenset({"answerable", "unanswerable"})


@dataclass(frozen=True)
class EvalCase:
    """One retrieval/answer evaluation case.

    The first five fields intentionally preserve the constructor shape used by
    the original 34-case corpus.  New datasets should fill every metadata
    field explicitly.
    """

    id: str
    category: str
    question: str
    source_ids: list[str]
    evidence_phrases: list[str]
    split: EvalSplit = "development"
    domain: str = "unknown"
    difficulty: str = "medium"
    expected_facts: list[str] = field(default_factory=list)
    must_cite: bool = True
    expected_refusal_reason: str | None = None
    tags: list[str] = field(default_factory=list)
    answerability: Answerability | None = None

    @property
    def answerable(self) -> bool:
        """Return the explicit label, falling back to legacy source IDs."""

        if self.answerability is not None:
            return self.answerability == "answerable"
        return bool(self.source_ids)

    @property
    def facts(self) -> list[str]:
        """Return answer facts, falling back to evidence phrases for legacy data."""

        return self.expected_facts or list(self.evidence_phrases)


def parse_eval_cases(
    data: Any,
    *,
    require_structured: bool = False,
) -> list[EvalCase]:
    """Parse and validate a JSON array of old or structured cases."""

    if not isinstance(data, list):
        raise ValueError("Evaluation dataset must be a JSON array.")
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"Evaluation case {index} must be a JSON object.")

        case_id = _required_text(raw, "id", index)
        category = _required_text(raw, "category", index)
        question = _required_text(raw, "question", index)
        source_ids = _string_list(raw, "source_ids", index)
        evidence_phrases = _string_list(raw, "evidence_phrases", index)
        expected_facts = _string_list(raw, "expected_facts", index, default=[])
        tags = _string_list(raw, "tags", index, default=[])
        split = str(raw.get("split", "development")).strip().lower()
        domain = str(raw.get("domain", "unknown")).strip() or "unknown"
        difficulty = str(raw.get("difficulty", "medium")).strip() or "medium"
        answerability_raw = raw.get("answerability")
        answerability: Answerability | None
        if answerability_raw is None or str(answerability_raw).strip() == "":
            answerability = None
        else:
            answerability = str(answerability_raw).strip().lower()  # type: ignore[assignment]
            if answerability not in VALID_ANSWERABILITY:
                raise ValueError(
                    f"Evaluation case {case_id} has invalid answerability: "
                    f"{answerability}"
                )

        if split not in VALID_SPLITS:
            raise ValueError(f"Evaluation case {case_id} has invalid split: {split}")
        if case_id in seen:
            raise ValueError(f"Duplicate evaluation case id: {case_id}")
        if require_structured:
            missing = [
                field_name
                for field_name in (
                    "split",
                    "domain",
                    "difficulty",
                    "answerability",
                    "expected_facts",
                    "must_cite",
                    "tags",
                )
                if field_name not in raw
            ]
            if missing:
                raise ValueError(
                    f"Evaluation case {case_id} is missing structured fields: {missing}"
                )
        if answerability == "answerable" and not source_ids:
            raise ValueError(f"Answerable case {case_id} must have source_ids.")
        if answerability == "unanswerable" and source_ids:
            raise ValueError(f"Unanswerable case {case_id} must not have source_ids.")
        if source_ids and not evidence_phrases:
            raise ValueError(f"Answerable case {case_id} has no evidence phrases.")
        if not source_ids and evidence_phrases:
            raise ValueError(f"No-answer case {case_id} must not contain evidence.")

        must_cite = raw.get("must_cite", bool(source_ids))
        if not isinstance(must_cite, bool):
            raise ValueError(f"Evaluation case {case_id}.must_cite must be boolean.")
        refusal_reason = raw.get("expected_refusal_reason")
        if refusal_reason is not None:
            refusal_reason = str(refusal_reason).strip() or None
        case = EvalCase(
            id=case_id,
            category=category,
            question=question,
            source_ids=source_ids,
            evidence_phrases=evidence_phrases,
            split=split,  # type: ignore[arg-type]
            domain=domain,
            difficulty=difficulty,
            expected_facts=expected_facts,
            must_cite=must_cite,
            expected_refusal_reason=refusal_reason,
            tags=tags,
            answerability=answerability,
        )
        seen.add(case_id)
        cases.append(case)
    return cases


def validate_cases_against_corpus(
    cases: list[EvalCase],
    documents: list[Document],
) -> None:
    """Validate gold sources and exact evidence against indexed documents."""

    content_by_source: dict[str, list[str]] = {}
    for document in documents:
        source_id = str(document.metadata.get("source_id", "")).strip()
        if source_id:
            content_by_source.setdefault(source_id, []).append(document.page_content)
    errors: list[str] = []
    for case in cases:
        for source_id in case.source_ids:
            if source_id not in content_by_source:
                errors.append(f"{case.id}: unknown source {source_id}")
        source_text = "\n".join(
            text
            for source_id in case.source_ids
            for text in content_by_source.get(source_id, [])
        )
        for phrase in case.evidence_phrases:
            if phrase not in source_text:
                errors.append(f"{case.id}: evidence not found: {phrase}")
    if errors:
        raise ValueError("Invalid evaluation gold data:\n" + "\n".join(errors))


def validate_dataset_shape(
    cases: list[EvalCase],
    *,
    minimum_cases: int = 0,
) -> list[str]:
    """Return coverage diagnostics used by the offline regression gate."""

    errors: list[str] = []
    if len(cases) < minimum_cases:
        errors.append(f"expected at least {minimum_cases} cases, got {len(cases)}")
    if not cases:
        return errors
    splits = {case.split for case in cases}
    missing_splits = sorted(VALID_SPLITS - splits)
    if missing_splits:
        errors.append(f"missing evaluation splits: {missing_splits}")
    domains = {case.domain for case in cases}
    if len(domains - {"unknown"}) < 6:
        errors.append("structured evaluation set must cover at least six domains")
    answerable_count = sum(case.answerable for case in cases)
    unanswerable_count = len(cases) - answerable_count
    if not unanswerable_count:
        errors.append("evaluation set must contain unanswerable cases")
    else:
        ratio = answerable_count / len(cases)
        if not 0.65 <= ratio <= 0.85:
            errors.append(
                "answerable ratio should be between 0.65 and 0.85; "
                f"got {ratio:.3f}"
            )
    return errors


def _required_text(raw: dict[str, Any], name: str, index: int) -> str:
    value = str(raw.get(name, "")).strip()
    if not value:
        raise ValueError(f"Evaluation case {index} requires {name}.")
    return value


def _string_list(
    raw: dict[str, Any],
    name: str,
    index: int,
    *,
    default: list[str] | None = None,
) -> list[str]:
    value = raw.get(name, default if default is not None else [])
    if not isinstance(value, list):
        raise ValueError(f"Evaluation case {index}.{name} must be an array.")
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise ValueError(f"Evaluation case {index}.{name} must not contain empty values.")
    return result
