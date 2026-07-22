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
    return _remove_adjacent_duplicates(chunks)


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


def _remove_adjacent_duplicates(chunks: list[str]) -> list[str]:
    result: list[str] = []
    for chunk in chunks:
        if not result or chunk != result[-1]:
            result.append(chunk)
    return result

