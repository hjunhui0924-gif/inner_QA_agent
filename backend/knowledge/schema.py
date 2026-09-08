"""Structured metadata helpers for enterprise knowledge records.

The JSON knowledge base predates the structured corpus.  This module keeps
legacy records readable while giving every new record a stable source ID,
version, lifecycle status, effective dates, and two different fingerprints:
``content_fingerprint`` for normalized duplicate detection and
``content_checksum`` for exact provenance checks.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from backend.knowledge.deduplication import content_fingerprint


KNOWLEDGE_STATUSES = frozenset({"draft", "active", "deprecated", "archived"})
DEFAULT_EFFECTIVE_FROM = "1970-01-01"
DEFAULT_VERSION = "v1"
DEFAULT_OWNER = "未指定"
DEFAULT_ACCESS_SCOPE = "internal"


def canonical_content(text: str) -> str:
    """Normalize line endings for reproducible raw-content checksums."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_checksum(text: str) -> str:
    """Return the exact SHA-256 checksum of canonical UTF-8 content."""

    return hashlib.sha256(canonical_content(text).encode("utf-8")).hexdigest()


def parse_iso_date(value: Any, *, field_name: str) -> date | None:
    """Parse an optional ISO date and raise a useful validation error."""

    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD).") from exc


def knowledge_record_id(record: dict[str, Any], *, fallback_index: int | None = None) -> str:
    """Return a stable ID, deriving one for a legacy record when necessary."""

    explicit = str(record.get("id", "")).strip()
    if explicit:
        return explicit
    content = str(record.get("content", "")).strip()
    if content:
        return f"legacy-{content_fingerprint(content)[:16]}"
    if fallback_index is not None:
        return f"legacy-record-{fallback_index}"
    return "legacy-record"


def normalize_knowledge_record(
    record: dict[str, Any],
    *,
    fallback_index: int | None = None,
) -> dict[str, Any] | None:
    """Normalize one JSON record without silently changing its content.

    ``None`` is returned for malformed records that cannot be indexed.  The
    strict validator used by the regression gate reports those records as
    errors instead of silently accepting them.
    """

    if not isinstance(record, dict):
        return None
    title = str(record.get("title", "")).strip()
    content = canonical_content(str(record.get("content", "")).strip())
    if not title or not content:
        return None

    normalized = dict(record)
    normalized["id"] = knowledge_record_id(record, fallback_index=fallback_index)
    normalized["title"] = title
    normalized["content"] = content
    normalized["source"] = str(record.get("source", "seed")).strip() or "seed"
    normalized["source_type"] = (
        str(record.get("source_type", normalized["source"])).strip()
        or normalized["source"]
    )
    normalized["department"] = (
        str(record.get("department", "unknown")).strip() or "unknown"
    )
    normalized["version"] = (
        str(record.get("version", DEFAULT_VERSION)).strip() or DEFAULT_VERSION
    )
    normalized["status"] = (
        str(record.get("status", "active")).strip().lower() or "active"
    )
    normalized["effective_from"] = (
        str(record.get("effective_from", DEFAULT_EFFECTIVE_FROM)).strip()
        or DEFAULT_EFFECTIVE_FROM
    )
    effective_to = record.get("effective_to")
    normalized["effective_to"] = (
        str(effective_to).strip() if effective_to not in {None, ""} else None
    )
    normalized["owner"] = (
        str(record.get("owner", DEFAULT_OWNER)).strip() or DEFAULT_OWNER
    )
    normalized["access_scope"] = (
        str(record.get("access_scope", DEFAULT_ACCESS_SCOPE)).strip()
        or DEFAULT_ACCESS_SCOPE
    )
    normalized["original_filename"] = str(
        record.get("original_filename", "")
    ).strip()
    normalized["content_fingerprint"] = content_fingerprint(content)
    normalized["content_checksum"] = content_checksum(content)
    return normalized


def validate_knowledge_record(
    record: dict[str, Any],
    *,
    index: int = 0,
) -> list[str]:
    """Return all validation errors for one structured knowledge record."""

    errors: list[str] = []
    prefix = f"record[{index}]"
    if not isinstance(record, dict):
        return [f"{prefix} must be an object"]

    required = (
        "id",
        "title",
        "content",
        "source",
        "department",
        "version",
        "status",
        "effective_from",
        "effective_to",
        "owner",
        "access_scope",
        "original_filename",
        "content_checksum",
    )
    for field_name in required:
        if field_name not in record:
            errors.append(f"{prefix} missing {field_name}")

    record_id = str(record.get("id", "")).strip()
    title = str(record.get("title", "")).strip()
    content = canonical_content(str(record.get("content", "")).strip())
    if not record_id:
        errors.append(f"{prefix}.id must not be empty")
    if not title:
        errors.append(f"{prefix}.title must not be empty")
    if not content:
        errors.append(f"{prefix}.content must not be empty")

    status = str(record.get("status", "")).strip().lower()
    if status not in KNOWLEDGE_STATUSES:
        errors.append(f"{prefix}.status must be one of {sorted(KNOWLEDGE_STATUSES)}")

    try:
        effective_from = parse_iso_date(
            record.get("effective_from"), field_name=f"{prefix}.effective_from"
        )
        effective_to = parse_iso_date(
            record.get("effective_to"), field_name=f"{prefix}.effective_to"
        )
        if effective_from and effective_to and effective_to < effective_from:
            errors.append(f"{prefix}.effective_to must not precede effective_from")
    except ValueError as exc:
        errors.append(str(exc))

    expected_checksum = content_checksum(content) if content else ""
    actual_checksum = str(record.get("content_checksum", "")).strip()
    if actual_checksum and actual_checksum != expected_checksum:
        errors.append(f"{prefix}.content_checksum does not match content")

    expected_fingerprint = content_fingerprint(content) if content else ""
    actual_fingerprint = str(record.get("content_fingerprint", "")).strip()
    if actual_fingerprint and actual_fingerprint != expected_fingerprint:
        errors.append(f"{prefix}.content_fingerprint does not match content")
    return errors


def validate_knowledge_records(records: Any) -> list[str]:
    """Validate the whole JSON array, including stable-ID uniqueness."""

    if not isinstance(records, list):
        return ["Knowledge base must be a JSON array."]
    errors: list[str] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        errors.extend(validate_knowledge_record(record, index=index))
        if isinstance(record, dict):
            record_id = str(record.get("id", "")).strip()
            if record_id and record_id in seen:
                errors.append(f"duplicate knowledge record id: {record_id}")
            if record_id:
                seen.add(record_id)
    return errors


def metadata_for_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-safe metadata copied onto every indexed chunk."""

    normalized = normalize_knowledge_record(record) or record
    return {
        "source_id": str(normalized.get("id", "")).strip(),
        "document_id": str(normalized.get("id", "")).strip(),
        "title": str(normalized.get("title", "")).strip(),
        "source": str(normalized.get("source", "unknown")).strip() or "unknown",
        "source_type": str(
            normalized.get("source_type", normalized.get("source", "unknown"))
        ).strip(),
        "department": str(normalized.get("department", "unknown")).strip(),
        "version": str(normalized.get("version", DEFAULT_VERSION)).strip(),
        "status": str(normalized.get("status", "active")).strip().lower(),
        "effective_from": normalized.get("effective_from"),
        "effective_to": normalized.get("effective_to"),
        "owner": str(normalized.get("owner", DEFAULT_OWNER)).strip(),
        "access_scope": str(
            normalized.get("access_scope", DEFAULT_ACCESS_SCOPE)
        ).strip(),
        "document_family": str(normalized.get("document_family", "")).strip(),
        "original_filename": str(normalized.get("original_filename", "")).strip(),
        "content_fingerprint": str(
            normalized.get("content_fingerprint", "")
        ).strip(),
        "content_checksum": str(normalized.get("content_checksum", "")).strip(),
    }
