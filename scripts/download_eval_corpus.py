"""Download and normalize official documents used by the RAG benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BASE_DIR / "data" / "eval_corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
DOCUMENTS_DIR = CORPUS_DIR / "documents"
PROVENANCE_PATH = CORPUS_DIR / "provenance.json"


def _load_manifest() -> list[dict[str, str]]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Corpus manifest must be a JSON array.")
    return [dict(item) for item in data]


def _validate_source(item: dict[str, str], final_url: str) -> None:
    expected = item["allowed_host"].casefold()
    actual = (urlparse(final_url).hostname or "").casefold()
    if actual != expected:
        raise ValueError(
            f"Source redirect left the allowed host: expected {expected}, got {actual}"
        )


def _extract_text(
    html: str,
    expected_title: str,
    content_selector: str = "",
) -> str:
    # Some gov.cn pages contain markup that lxml repairs by discarding the
    # article body. The standard parser is slower but preserves those pages.
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    selectors = (
        "#Zoom",
        "#BodyLabel",
        "#UCAP-CONTENT",
        ".TRS_Editor",
        ".pages_content",
        ".article-content",
        ".article",
        "main",
    )
    candidates = []
    if content_selector:
        selected = soup.select_one(content_selector)
        if selected is None:
            raise ValueError(
                f"Configured article selector not found for {expected_title}: "
                f"{content_selector}"
            )
        candidates.append(selected)
    else:
        candidates = [soup.select_one(selector) for selector in selectors]
        candidates.extend(soup.find_all(["article", "section", "div"]))
    text_candidates: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        text = candidate.get_text("\n", strip=True)
        if expected_title in text or len(text) >= 1500:
            text_candidates.append(text)
    if not text_candidates:
        raise ValueError(f"Could not locate article body for {expected_title}")

    text = max(text_candidates, key=len)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line or line in cleaned[-2:]:
            continue
        cleaned.append(line)
    trailing_boilerplate = {
        "关闭",
        "解读",
        "登录",
        "注册",
        "×",
        "相关文章",
        "<< 返回首页",
        "网络数据安全管理条例",
    }
    while cleaned and (
        cleaned[-1] in trailing_boilerplate
        or cleaned[-1].startswith("编 辑：")
        or cleaned[-1].startswith("责 编：")
    ):
        cleaned.pop()
    normalized = "\n".join(cleaned)
    title_position = normalized.find(expected_title)
    if title_position >= 0:
        normalized = normalized[title_position:]
    if len(normalized) < 1200:
        raise ValueError(
            f"Extracted body for {expected_title} is suspiciously short: {len(normalized)}"
        )
    return normalized


def _verify_cached_snapshot(
    body: str,
    previous: dict[str, Any],
    source_id: str,
) -> str:
    """Return the checksum or reject a cache changed since provenance capture."""

    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    expected = str(previous.get("content_sha256", ""))
    if expected and actual != expected:
        raise ValueError(
            f"Cached corpus snapshot changed for {source_id}; "
            "run with --refresh to restore the official source"
        )
    return actual


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def download_corpus(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Download missing official documents and return provenance records."""

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    provenance: list[dict[str, Any]] = []
    pending_writes: dict[Path, str] = {}
    previous_records = (
        json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        if PROVENANCE_PATH.exists()
        else []
    )
    previous_by_id = {
        str(record.get("id", "")): record
        for record in previous_records
        if isinstance(record, dict)
    }
    with httpx.Client(
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": "Enterprise-RAG-Evaluator/1.0"},
    ) as client:
        for item in _load_manifest():
            if not re.fullmatch(r"[a-z0-9_]+", item["id"]):
                raise ValueError(f"Unsafe corpus id: {item['id']}")
            previous = previous_by_id.get(item["id"], {})
            previous_relative_path = str(previous.get("local_path", "")).replace(
                "\\", "/"
            )
            previous_target = (
                BASE_DIR / previous_relative_path if previous_relative_path else None
            )
            if previous_target is not None and previous_target.exists() and not refresh:
                target = previous_target
                body = target.read_text(encoding="utf-8")
                content_sha256 = _verify_cached_snapshot(body, previous, item["id"])
                final_url = str(previous.get("final_url", item["url"]))
                status_code = int(previous.get("http_status", 200))
                retrieved_at = str(
                    previous.get("retrieved_at", datetime.now(timezone.utc).isoformat())
                )
            else:
                response = client.get(item["url"])
                response.raise_for_status()
                _validate_source(item, str(response.url))
                if len(response.content) > 5_000_000:
                    raise ValueError(f"Source response is unexpectedly large: {item['id']}")
                body_text = _extract_text(
                    response.text,
                    item["title"],
                    item.get("content_selector", ""),
                )
                body = (
                    f"# {item['title']}\n\n"
                    f"发布机构：{item['publisher']}\n\n"
                    f"原始来源：{item['url']}\n\n"
                    f"{body_text}\n"
                )
                final_url = str(response.url)
                status_code = response.status_code
                retrieved_at = datetime.now(timezone.utc).isoformat()
                content_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()

            pinned_sha256 = str(item.get("expected_sha256", ""))
            if pinned_sha256 and content_sha256 != pinned_sha256:
                raise ValueError(
                    f"Official snapshot digest changed for {item['id']}; "
                    "review the source before updating expected_sha256"
                )
            if refresh or previous_target is None or not previous_target.exists():
                target = DOCUMENTS_DIR / (
                    f"{item['id']}-{content_sha256[:12]}.md"
                )
                pending_writes[target] = body

            provenance.append(
                {
                    **item,
                    "final_url": final_url,
                    "http_status": status_code,
                    "local_path": target.relative_to(BASE_DIR).as_posix(),
                    "content_sha256": content_sha256,
                    "content_length": len(body),
                    "retrieved_at": retrieved_at,
                }
            )
    # Content-addressed snapshots are written first. Provenance is the single
    # atomic pointer switch, so a failed multi-source refresh keeps the old set.
    for target, body in pending_writes.items():
        _atomic_write_text(target, body)
    _atomic_write_text(
        PROVENANCE_PATH,
        json.dumps(provenance, ensure_ascii=False, indent=2),
    )
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    records = download_corpus(refresh=args.refresh)
    print(
        json.dumps(
            [
                {
                    "id": record["id"],
                    "length": record["content_length"],
                    "sha256": record["content_sha256"],
                }
                for record in records
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
