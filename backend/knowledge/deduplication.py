"""Document-level exact and near-duplicate detection.

Near-duplicate detection intentionally uses surface-form overlap rather than
semantic embeddings. Two policies about the same subject are not duplicates;
two copies that differ only in formatting, headers, or a few edits are.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal


DuplicateKind = Literal["exact", "near"]


@dataclass(frozen=True)
class DuplicateMatch:
    """The existing record that caused a document to be rejected."""

    kind: DuplicateKind
    score: float
    record: dict[str, Any]


def normalize_content(text: str) -> str:
    """Normalize inconsequential Unicode and whitespace differences."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(normalized.split()).strip()


def content_fingerprint(text: str) -> str:
    """Return a stable fingerprint for exact duplicate detection."""

    return hashlib.sha256(normalize_content(text).encode("utf-8")).hexdigest()


def find_duplicate(
    candidate_text: str,
    records: list[dict[str, Any]],
    *,
    near_threshold: float = 0.92,
    minimum_length_ratio: float = 0.85,
    minimum_near_length: int = 200,
    shingle_size: int = 5,
) -> DuplicateMatch | None:
    """Find an exact copy or a conservatively defined near copy.

    Short text only participates in exact matching because character-shingle
    similarity is unstable for short snippets.
    """

    if not 0.0 <= near_threshold <= 1.0:
        raise ValueError("near_threshold must be between 0 and 1.")
    if not 0.0 <= minimum_length_ratio <= 1.0:
        raise ValueError("minimum_length_ratio must be between 0 and 1.")
    if shingle_size <= 0:
        raise ValueError("shingle_size must be greater than zero.")

    normalized_candidate = normalize_content(candidate_text)
    candidate_fingerprint = hashlib.sha256(
        normalized_candidate.encode("utf-8")
    ).hexdigest()

    candidates: list[tuple[dict[str, Any], str]] = []
    for record in records:
        existing_text = str(record.get("content", "")).strip()
        if not existing_text:
            continue
        normalized_existing = normalize_content(existing_text)
        # Recompute from content so records written by an older normalization
        # algorithm remain compatible after an upgrade.
        existing_fingerprint = hashlib.sha256(
            normalized_existing.encode("utf-8")
        ).hexdigest()
        if existing_fingerprint == candidate_fingerprint:
            return DuplicateMatch(kind="exact", score=1.0, record=record)
        candidates.append((record, normalized_existing))

    compact_candidate = _compact_for_near_match(normalized_candidate)
    if len(compact_candidate) < minimum_near_length:
        return None
    candidate_shingles = _shingles(compact_candidate, shingle_size)
    best_match: DuplicateMatch | None = None

    for record, normalized_existing in candidates:
        compact_existing = _compact_for_near_match(normalized_existing)
        if len(compact_existing) < minimum_near_length:
            continue
        length_ratio = min(len(compact_candidate), len(compact_existing)) / max(
            len(compact_candidate), len(compact_existing)
        )
        if length_ratio < minimum_length_ratio:
            continue
        score = _near_duplicate_score(
            candidate_shingles,
            _shingles(compact_existing, shingle_size),
            length_ratio,
        )
        if score >= near_threshold and (
            best_match is None or score > best_match.score
        ):
            best_match = DuplicateMatch(kind="near", score=score, record=record)
    return best_match


def _compact_for_near_match(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)


def _shingles(text: str, size: int) -> set[str]:
    if len(text) <= size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _near_duplicate_score(
    left: set[str],
    right: set[str],
    length_ratio: float,
) -> float:
    if not left or not right:
        return 0.0
    shorter_coverage = len(left & right) / min(len(left), len(right))
    return shorter_coverage * length_ratio
