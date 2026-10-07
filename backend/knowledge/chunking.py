"""Paragraph-aware text chunking for Chinese enterprise documents."""

from __future__ import annotations

import re


def split_text(text: str, *, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """Split text while preserving paragraph and sentence endings when possible."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size.")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    segments = _segments(normalized, chunk_size)
    chunks: list[str] = []
    current = ""
    for segment in segments:
        separator = "\n" if current and not current.endswith("\n") else ""
        candidate = f"{current}{separator}{segment}" if current else segment
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
        prefix = current[-overlap:].strip() if overlap and current else ""
        current = f"{prefix}\n{segment}".strip() if prefix else segment
        if len(current) > chunk_size:
            chunks.extend(_hard_split(current, chunk_size, overlap)[:-1])
            current = _hard_split(current, chunk_size, overlap)[-1]
    if current.strip():
        chunks.append(current.strip())
    return _repair_leading_continuations(
        _remove_adjacent_duplicates(chunks), chunk_size=chunk_size, overlap=overlap
    )


def _segments(text: str, chunk_size: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    result: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            result.append(paragraph)
            continue
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[。！？；.!?;])\s*|\n+", paragraph)
            if part.strip()
        ]
        result.extend(sentences or [paragraph])
    return result


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = end - overlap
    return [chunk for chunk in chunks if chunk]


def _repair_leading_continuations(
    chunks: list[str], *, chunk_size: int, overlap: int
) -> list[str]:
    """Copy a bounded clause prefix when a chunk starts mid-sentence.

    This runs after the historic splitter, so ordinary retrieval chunk
    boundaries remain stable. It only repairs a continuation that follows an
    unterminated previous chunk, which is common when PDF line wraps split a
    condition such as ``财政\n局发布``.
    """

    if overlap <= 0:
        return chunks
    max_context = min(overlap + 32, chunk_size - 1)
    heading = re.compile(r"^(?:第[一二三四五六七八九十百千万\d]+条|[-—–]?\s*\d+\s*[-—–]|\d+[.)、])")
    for index in range(1, len(chunks)):
        previous = chunks[index - 1]
        current = chunks[index].lstrip()
        if not previous or not current or heading.match(current):
            continue
        # The next chunk's opening text should already occur near the end of
        # the previous chunk. This detects a PDF overlap that starts at a
        # wrapped continuation (``财政`` / ``政局``) without changing ordinary
        # paragraph or table boundaries.
        if not re.match(r"^[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", current):
            continue
        prefix = current[: min(24, len(current))]
        occurrence = previous.rfind(prefix)
        if occurrence < 0 or len(previous) - occurrence > max_context:
            continue
        boundaries = [match.end() for match in re.finditer(r"[。！？!?；;.]\s*|\n{2,}", previous)
                      if match.end() <= occurrence]
        if not boundaries:
            continue
        start = boundaries[-1]
        context = previous[start:].strip()
        if len(context) < 20 or context in current:
            continue
        combined = f"{context}\n{current}"
        chunks[index] = combined[:chunk_size].rstrip()
    return chunks


def _remove_adjacent_duplicates(chunks: list[str]) -> list[str]:
    result: list[str] = []
    for chunk in chunks:
        if not result or chunk != result[-1]:
            result.append(chunk)
    return result

