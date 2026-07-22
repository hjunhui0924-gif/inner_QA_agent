"""Structured citation helpers shared by prompting, APIs, and evaluation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, TypedDict

from langchain_core.documents import Document

from backend.retrieval.engine import tokenize


class Citation(TypedDict):
    """One verifiable link from an answer marker to a source chunk."""

    citation_id: str
    document_id: str
    title: str
    source: str
    filename: str
    page: int | None
    section: str
    chunk_id: str
    quote: str
    verification_status: str


_MARKER_PATTERN = re.compile(r"\[\s*C\s*(\d+)\s*\]", flags=re.I)


def format_documents_for_prompt(documents: Sequence[Document]) -> str:
    """Render evidence with stable IDs and source locations for the model."""

    if not documents:
        return "No retrieved evidence."
    rendered: list[str] = []
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata
        location: list[str] = []
        page = _page_number(metadata.get("page"))
        if page is not None:
            location.append(f"page {page}")
        section = str(metadata.get("section", "")).strip()
        if section:
            location.append(section)
        title = str(metadata.get("title", "Untitled document")).strip()
        header = f"[C{index}] {title}"
        if location:
            header += f" ({' / '.join(location)})"
        rendered.append(f"{header}\n{document.page_content.strip()}")
    return "\n\n".join(rendered)


def build_citations(
    documents: Sequence[Document],
    answer: str,
    *,
    query: str = "",
    max_quote_chars: int = 600,
) -> list[Citation]:
    """Resolve only markers actually used by the answer to source metadata."""

    referenced = sorted({int(item) for item in _MARKER_PATTERN.findall(answer)})
    citations: list[Citation] = []
    for number in referenced:
        if number < 1 or number > len(documents):
            continue
        document = documents[number - 1]
        metadata = document.metadata
        citations.append(
            Citation(
                citation_id=f"C{number}",
                document_id=str(metadata.get("document_id", "")).strip(),
                title=str(metadata.get("title", "Untitled document")).strip(),
                source=str(metadata.get("source", "unknown")).strip() or "unknown",
                filename=str(metadata.get("original_filename", "")).strip(),
                page=_page_number(metadata.get("page")),
                section=str(metadata.get("section", "")).strip(),
                chunk_id=str(metadata.get("chunk_id", "")).strip(),
                quote=_supporting_quote(
                    document.page_content,
                    query=query,
                    answer=_claims_for_marker(answer, number),
                    max_chars=max_quote_chars,
                ),
                verification_status="provenance_only",
            )
        )
    return citations


def citation_markers(answer: str) -> set[str]:
    """Return normalized citation IDs referenced by an answer."""

    return {f"C{item}" for item in _MARKER_PATTERN.findall(answer)}


def sanitize_answer_citations(
    answer: str,
    documents: Sequence[Document],
    *,
    query: str = "",
) -> str:
    """Normalize in-range source markers without claiming semantic entailment."""

    if not answer.strip() or not documents:
        return answer
    rendered_lines: list[str] = []
    for line in answer.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        fragments = re.findall(r"[^。！？.!?]+[。！？.!?]?", body)
        if not fragments:
            rendered_lines.append(line)
            continue
        rendered_lines.append(
            "".join(_sanitize_claim(fragment, documents, query) for fragment in fragments)
            + newline
        )
    return "".join(rendered_lines)


def _sanitize_claim(claim: str, documents: Sequence[Document], query: str) -> str:
    referenced = [int(item) for item in _MARKER_PATTERN.findall(claim)]
    clean = _MARKER_PATTERN.sub("", claim).rstrip()
    if not referenced:
        return claim
    if not clean.strip() or _is_structural_claim(clean):
        return clean
    valid_indices = [
        index
        for index in dict.fromkeys(referenced)
        if 1 <= index <= len(documents)
    ]
    if not valid_indices:
        return clean
    markers = "".join(f"[C{index}]" for index in valid_indices)
    match = re.search(r"([。！？.!?])\s*$", clean)
    if match:
        punctuation = match.group(1)
        stem = clean[: match.start()].rstrip()
        return f"{stem} {markers}{punctuation}"
    return f"{clean} {markers}"


def align_answer_citations(
    answer: str,
    documents: Sequence[Document],
    *,
    query: str = "",
) -> str:
    """Backward-compatible alias; citations are now sanitized, never auto-added."""

    return sanitize_answer_citations(answer, documents, query=query)


def polarity_matches(claim: str, evidence: str) -> bool:
    """Reject obvious positive/negative contradictions before citing evidence."""

    return _has_negative_polarity(claim) == _has_negative_polarity(evidence)


_RELATION_FAMILIES: tuple[dict[str, tuple[str, ...]], ...] = (
    {
        "permission": (r"\bmay\b", r"\bcan\b", r"\bpermitted?\b", r"可以", r"允许", r"可(?:以)?"),
        "obligation": (r"\bmust\b", r"\bshall\b", r"\brequired?\b", r"必须", r"应当", r"需要", r"须"),
        "prohibition": (r"\bprohibit", r"\bforbid", r"不得", r"禁止", r"不可", r"不能"),
    },
    {
        "before": (r"\bbefore\b", r"此前", r"之前", r"以前", r"早于"),
        "after": (r"\bafter\b", r"此后", r"之后", r"以后", r"晚于"),
    },
    {
        "minimum": (r"\bat least\b", r"\bminimum\b", r"不少于", r"不低于", r"至少", r"以上"),
        "maximum": (r"\bat most\b", r"\bmaximum\b", r"\bup to\b", r"不超过", r"不高于", r"至多", r"以下"),
    },
    {
        "lower": (r"\bless than\b", r"\bbelow\b", r"\bunder\b", r"少于", r"低于", r"小于"),
        "upper": (r"\bmore than\b", r"\babove\b", r"\bover\b", r"多于", r"高于", r"大于"),
    },
    {
        "increase": (r"\bincreas", r"\braise", r"增长", r"增加", r"提高", r"上升"),
        "decrease": (r"\bdecreas", r"\breduc", r"\blower", r"减少", r"降低", r"下降"),
    },
)


def semantic_relations_match(claim: str, evidence: str) -> bool:
    """Check polarity and mutually exclusive legal/numeric relation modes."""

    if not polarity_matches(claim, evidence):
        return False
    for family in _RELATION_FAMILIES:
        claim_modes = _relation_modes(claim, family)
        if not claim_modes:
            continue
        evidence_modes = _relation_modes(evidence, family)
        if not evidence_modes or claim_modes.isdisjoint(evidence_modes):
            return False
    return True


def extractive_clause_match(claim: str, evidence: str) -> bool:
    """Accept only near-verbatim contiguous clauses, never lexical similarity."""

    claim_text = _normalized_clause(claim)
    evidence_text = _normalized_clause(evidence)
    if min(len(claim_text), len(evidence_text)) < 4:
        return False
    return claim_text in evidence_text or evidence_text in claim_text


def _normalized_clause(text: str) -> str:
    cleaned = _MARKER_PATTERN.sub("", text).replace("**", "").casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", cleaned)


def _relation_modes(text: str, family: dict[str, tuple[str, ...]]) -> set[str]:
    lowered = text.casefold()
    return {
        mode
        for mode, patterns in family.items()
        if any(re.search(pattern, lowered, flags=re.I) for pattern in patterns)
    }


def _has_negative_polarity(text: str) -> bool:
    normalized = text.casefold().replace("未成年人", "").replace("不满", "")
    patterns = (
        r"不(?!满)",
        r"未(?!成年人)",
        r"没有|无权|无法|禁止|不得|不能|不可|拒绝|排除|免于",
        r"\b(?:not|no|never|without|cannot|can't|doesn't|isn't)\b",
        r"\b(?:prohibit|prohibits|prohibited|forbid|forbids|forbidden|deny|denies|denied|exclude|excludes|excluded)\b",
    )
    return any(re.search(pattern, normalized, flags=re.I) for pattern in patterns)


def _is_structural_claim(text: str) -> bool:
    plain = text.replace("**", "").strip()
    if re.fullmatch(r"(?:[-*]|\d+|[（(]?[一二三四五六七八九十]+[）)]?)[.、:]?", plain):
        return True
    return plain.endswith(("：", ":")) and len(plain) <= 40


def _supporting_quote(text: str, *, query: str, answer: str, max_chars: int) -> str:
    clean = " ".join(text.split())
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？.!?；;])\s*", clean)
        if item.strip()
    ]
    candidates: list[str] = []
    for start in range(len(sentences)):
        for end in range(start + 1, min(len(sentences), start + 8) + 1):
            window = " ".join(sentences[start:end])
            if len(window) <= max_chars:
                candidates.append(window)
    target_tokens = set(tokenize(f"{query} {answer}"))
    target_numbers = set(re.findall(r"\d+(?:\.\d+)?", f"{query} {answer}"))

    def relevance(item: str) -> tuple[float, int]:
        candidate_tokens = set(tokenize(item))
        overlap = len(target_tokens & candidate_tokens)
        density = overlap / max(len(candidate_tokens) ** 0.5, 1.0)
        coverage = overlap / max(len(target_tokens), 1)
        candidate_numbers = set(re.findall(r"\d+(?:\.\d+)?", item))
        numeric_bonus = 100.0 * len(target_numbers & candidate_numbers)
        return numeric_bonus + (3.0 * coverage) + density, -len(item)

    best = max(
        candidates or [clean],
        key=relevance,
    )
    if len(best) <= max_chars:
        return best
    return best[:max_chars].rstrip()


def _claims_for_marker(answer: str, number: int) -> str:
    marker = re.compile(rf"\[\s*C\s*{number}\s*\]", flags=re.I)
    claims = [
        item.strip()
        for item in re.split(r"(?<=[。！？.!?])\s*", answer)
        if marker.search(item)
    ]
    return " ".join(claims) or answer


def _page_number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
