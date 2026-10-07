"""Deterministic source preview windows and citation location helpers."""

from __future__ import annotations

import re
import base64
import binascii
import hashlib
import hmac
import json
from typing import Any, Iterable


MAX_BLOCKS = 12
MAX_CODEPOINTS = 24_000
MAX_BLOCK_CODEPOINTS = 4_000


def build_preview_blocks(
    content: str,
    segments: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build bounded, stable text blocks from stored extraction segments."""

    candidates = list(segments or [])
    if not candidates:
        candidates = [{"text": part} for part in re.split(r"\n{2,}", content) if part.strip()]
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        if not text.strip():
            continue
        for offset in range(0, len(text), MAX_BLOCK_CODEPOINTS):
            chunk = text[offset : offset + MAX_BLOCK_CODEPOINTS]
            if not chunk:
                continue
            block: dict[str, Any] = {
                "id": f"block-{index}-{offset}",
                "text": chunk,
                "page": _positive_int(item.get("page")),
                "section": str(item.get("section", "")).strip() or None,
            }
            blocks.append(block)
    return blocks


def locate_quote(blocks: list[dict[str, Any]], quote: str) -> dict[str, Any]:
    """Locate a quote without inventing coordinates for ambiguous matches."""

    target = quote.strip()
    if not target:
        return _location("unavailable", None, None, None, None, 0)
    exact: list[tuple[dict[str, Any], int, int]] = []
    for block in blocks:
        text = str(block.get("text", ""))
        start = text.find(target)
        while start >= 0:
            exact.append((block, start, start + len(target)))
            start = text.find(target, start + 1)
    if len(exact) == 1:
        block, start, end = exact[0]
        return _location("exact", block, start, end, block.get("page"), 1)
    if len(exact) > 1:
        return _location("ambiguous", None, None, None, None, len(exact))

    normalized_target = _normalize(target)
    normalized: list[tuple[dict[str, Any], int, int]] = []
    for block in blocks:
        text = str(block.get("text", ""))
        mapping: list[int] = []
        normalized_text_parts: list[str] = []
        for index, char in enumerate(text):
            if char.isspace():
                if normalized_text_parts and normalized_text_parts[-1] != " ":
                    normalized_text_parts.append(" ")
                    mapping.append(index)
            else:
                normalized_text_parts.append(char)
                mapping.append(index)
        normalized_text = "".join(normalized_text_parts).strip()
        start = normalized_text.find(normalized_target)
        while start >= 0:
            end = start + len(normalized_target)
            if end > 0 and start < len(mapping) and end - 1 < len(mapping):
                normalized.append((block, mapping[start], mapping[end - 1] + 1))
            start = normalized_text.find(normalized_target, start + 1)
    if len(normalized) == 1:
        block, start, end = normalized[0]
        return _location("normalized", block, start, end, block.get("page"), 1)
    if len(normalized) > 1:
        return _location("ambiguous", None, None, None, None, len(normalized))
    return _location("not_found", None, None, None, None, 0)


def build_window(blocks: list[dict[str, Any]], target_block_id: str | None = None) -> list[dict[str, Any]]:
    """Return a small neighboring window, centered on the located block."""

    if not blocks:
        return []
    if not target_block_id:
        return page_window(blocks, 0)
    index = next((i for i, block in enumerate(blocks) if block.get("id") == target_block_id), 0)
    start = max(0, index - 2)
    return page_window(blocks, start)


def page_window(blocks: list[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
    """Return one bounded page starting at a validated block offset."""

    window = []
    total = 0
    for block in blocks[max(0, offset) : max(0, offset) + MAX_BLOCKS]:
        size = len(str(block.get("text", "")))
        if total + size > MAX_CODEPOINTS:
            break
        window.append(block)
        total += size
    return window


def previous_window_offset(blocks: list[dict[str, Any]], offset: int) -> int:
    """Walk backwards using the same byte-independent response window limits."""
    start = max(0, offset)
    total = 0
    count = 0
    while start > 0 and count < MAX_BLOCKS:
        size = len(str(blocks[start - 1].get("text", "")))
        if total + size > MAX_CODEPOINTS:
            break
        start -= 1
        total += size
        count += 1
    return start


def encode_cursor(source_id: str, checksum: str, offset: int, *, key: str) -> str:
    payload = json.dumps({"source_id": source_id, "checksum": checksum, "offset": max(0, offset)}, separators=(",", ":"), ensure_ascii=False).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{encoded}.{signature}"


def decode_cursor(cursor: str, source_id: str, checksum: str, *, key: str) -> int:
    try:
        encoded, signature = cursor.split(".", 1)
        expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("source_id") != source_id or payload.get("checksum") != checksum:
            raise ValueError
        offset = int(payload.get("offset"))
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as error:
        raise ValueError("Invalid source preview cursor.") from error


def _location(status: str, block: dict[str, Any] | None, start: int | None, end: int | None, page: int | None, count: int) -> dict[str, Any]:
    return {
        "status": status,
        "block_id": block.get("id") if block else None,
        "page": page,
        "section": block.get("section") if block else None,
        "start": start,
        "end": end,
        "match_count": count,
    }


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
