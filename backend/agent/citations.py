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
    source_id: str
    document_id: str
    title: str
    source: str
    source_type: str
    version: str
    status: str
    effective_from: str | None
    effective_to: str | None
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
                source_id=str(
                    metadata.get("source_id", metadata.get("document_id", ""))
                ).strip(),
                document_id=str(metadata.get("document_id", "")).strip(),
                title=str(metadata.get("title", "Untitled document")).strip(),
                source=str(metadata.get("source", "unknown")).strip() or "unknown",
                source_type=str(metadata.get("source_type", "")).strip(),
                version=str(metadata.get("version", "")).strip(),
                status=str(metadata.get("status", "")).strip(),
                effective_from=(
                    str(metadata.get("effective_from")).strip()
                    if metadata.get("effective_from") not in {None, ""}
                    else None
                ),
                effective_to=(
                    str(metadata.get("effective_to")).strip()
                    if metadata.get("effective_to") not in {None, ""}
                    else None
                ),
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


def validate_citation_claim_alignment(
    answer: str,
    citations: Sequence[Citation],
) -> tuple[bool, str]:
    """Check that each factual claim is tied to supporting cited text.

    Citation numbering alone only proves that a marker is in range.  This
    guard additionally compares every cited claim with the quote resolved for
    that marker.  It is intentionally conservative: an unsupported or
    uncited claim fails closed and is sent through the existing retry path.
    """

    citations_by_id = {
        str(citation.get("citation_id", "")).strip(): citation
        for citation in citations
        if str(citation.get("citation_id", "")).strip()
    }
    claims = _answer_claims(answer)
    if not claims:
        return False, "回答没有可验证的事实断言。"
    for index, claim in enumerate(claims):
        if _is_structural_claim(claim) or _is_abstention_claim(claim):
            continue
        if (
            _is_short_conclusion_claim(claim)
            and any(citation_markers(item) for item in claims[index + 1 : index + 2])
        ):
            continue
        markers = citation_markers(claim)
        if not markers:
            return False, "回答存在未绑定引用的事实断言。"
        supported = False
        for marker in markers:
            citation = citations_by_id.get(marker)
            if citation is None:
                continue
            quote = str(citation.get("quote", "")).strip()
            if quote and _claim_has_quote_support(claim, quote):
                supported = True
                break
        if not supported:
            return False, "引用原文不支持对应的事实断言。"
    return True, ""


def _is_short_conclusion_claim(claim: str) -> bool:
    """Allow a short conclusion when the following sentence carries evidence."""

    normalized = re.sub(r"[^a-zA-Z\u4e00-\u9fff]", "", claim).casefold()
    return normalized in {"不适用", "适用", "可以", "不可以"}


def _answer_claims(answer: str) -> list[str]:
    claims: list[str] = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-*]\s+|\d+[.、)]\s*)", "", line).strip()
        if not line or line.endswith(("：", ":")):
            continue
        claims.extend(
            item.strip()
            for item in re.findall(r"[^。！？!?]+[。！？!?]?", line)
            if item.strip()
        )
    return claims


def _claim_has_quote_support(claim: str, quote: str) -> bool:
    clean_claim = _MARKER_PATTERN.sub("", claim).replace("**", "").strip()
    clean_quote = " ".join(quote.split())
    if not clean_claim or not clean_quote:
        return False
    if not _claim_numbers_are_supported(clean_claim, clean_quote):
        return False
    if extractive_clause_match(clean_claim, clean_quote):
        return True

    if not _relation_modes_are_compatible(clean_claim, clean_quote):
        return False
    claim_tokens = {
        token
        for token in tokenize(clean_claim)
        if len(token) >= 2 or any(character.isdigit() for character in token)
    }
    quote_tokens = set(tokenize(_remove_normative_fillers(clean_quote)))
    claim_tokens = set(tokenize(_remove_normative_fillers(clean_claim))) or claim_tokens
    if not claim_tokens:
        return False
    overlap = claim_tokens & quote_tokens
    overlap_ratio = len(overlap) / len(claim_tokens)
    numeric_overlap = {
        token for token in overlap if any(character.isdigit() for character in token)
    }
    return (
        overlap_ratio >= 0.25 and len(overlap) >= 2
    ) or (bool(numeric_overlap) and len(overlap) >= 2)


def _relation_modes_are_compatible(claim: str, quote: str) -> bool:
    """Reject explicit opposite relations without rejecting broader quotes."""

    for family in _RELATION_FAMILIES:
        claim_modes = _relation_modes(claim, family)
        quote_modes = _relation_modes(quote, family)
        if claim_modes and quote_modes and claim_modes.isdisjoint(quote_modes):
            return False
    return True


def _remove_normative_fillers(text: str) -> str:
    """Ignore harmless legal drafting fillers for paraphrase alignment."""

    return re.sub(r"(?:应当|应该|需|需要|可以|是指|其中|根据|本法|本办法)", "", text)


def _claim_numbers_are_supported(claim: str, quote: str) -> bool:
    """Do not accept a citation when an explicit claim number is absent."""

    claim_numbers = set(re.findall(r"\d+(?:\.\d+)?", claim))
    quote_numbers = set(re.findall(r"\d+(?:\.\d+)?", quote))
    return claim_numbers <= quote_numbers


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
    if not clean.strip() or _is_structural_claim(clean) or _is_abstention_claim(clean):
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
        "obligation": (
            r"\bmust\b",
            r"\bshall\b",
            r"\brequired?\b",
            r"必须",
            r"应(?:当)?",
            r"需要",
            r"须",
        ),
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


def _is_abstention_claim(text: str) -> bool:
    lowered = text.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "未包含该问题的答案",
            "未找到足够",
            "信息不足",
            "无法可靠作答",
            "does not contain the answer",
            "not enough information",
            "cannot reliably answer",
        )
    )


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
