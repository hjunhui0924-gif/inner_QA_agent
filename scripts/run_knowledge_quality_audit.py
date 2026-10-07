"""Run a frozen quality audit against an isolated real Agent.

Explicit --live is required. Model/embedding/rerank calls consume quota.
Automatic checks are evidence prechecks, never an independent semantic verdict.
Each run owns its server and new storage; daily knowledge and .env stay intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_fixture(path: Path) -> tuple[dict, list[dict]]:
    """Bind expectations to a specific corpus revision before any service calls."""
    from backend.knowledge.schema import normalize_knowledge_record

    fixture = json.loads(path.read_bytes())
    if "corpus_path" in fixture:
        if "records" in fixture:
            raise ValueError("Specify either corpus_path or inline records")
        corpus_path = (ROOT / fixture["corpus_path"]).resolve()
        corpus_bytes = corpus_path.read_bytes()
        if hashlib.sha256(corpus_bytes).hexdigest() != fixture.get("corpus_sha256"):
            raise ValueError("Corpus changed; review expectations before updating corpus_sha256")
        raw_records = json.loads(corpus_bytes)
    else:
        raw_records = fixture["records"]
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("Audit corpus must contain records")
    records = [normalize_knowledge_record(record) for record in raw_records]
    if not all(records):
        raise ValueError("Invalid audit corpus record")
    by_id = {record["id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("Duplicate corpus source ID")
    cases = fixture.get("cases", [])
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Audit cases must have unique IDs and must not be empty")
    for case in cases:
        if not case.get("question", "").strip() or not re.fullmatch(r"[A-Za-z0-9_-]+", case.get("session", "")):
            raise ValueError("Invalid question or session identifier")
        allowed = set(case.get("allowed_sources", []))
        if not allowed.issubset(by_id) or (not case.get("refusal") and not allowed):
            raise ValueError(f"Invalid expected sources for {case['case_id']}")
        if not set(case.get("required_sources", [])).issubset(allowed):
            raise ValueError(f"Required sources outside allowed sources for {case['case_id']}")
        for evidence in case.get("evidence", []) + case.get("required_quote_phrases", []):
            source_id = evidence["source_id"]
            quote = re.sub(r"\s+", "", evidence["quote"])
            if source_id not in allowed or not quote or quote not in re.sub(r"\s+", "", by_id[source_id]["content"]):
                raise ValueError(f"Expected evidence absent from corpus for {case['case_id']}")
    return fixture, records


def precheck(case: dict, result: dict, records: list[dict] | None = None) -> list[str]:
    """Check the frozen minimum contract; reviewers must still read the answer."""
    failures = []
    content = result.get("content", "")
    normalized_content = re.sub(r"\s+", "", content)
    citations = result.get("citations", [])
    if case.get("refusal"):
        if result.get("failure_type") != "retrieval_miss" or citations:
            failures.append("expected evidence-insufficient refusal without citations")
    else:
        if result.get("failure_type") != "none":
            failures.append("unexpected fallback")
        if not citations:
            failures.append("missing citations")
        for alternatives in case.get("required", []):
            if not any(re.sub(r"\s+", "", term) in normalized_content for term in alternatives):
                failures.append("missing required fact: " + " / ".join(alternatives))
        allowed = set(case.get("allowed_sources", []))
        if any(c.get("source_id") not in allowed for c in citations):
            failures.append("citation outside frozen allowed sources")
        actual_sources = {c.get("source_id") for c in citations}
        if not set(case.get("required_sources", [])).issubset(actual_sources):
            failures.append("missing required citation source")
        for evidence in case.get("required_quote_phrases", []):
            phrase = re.sub(r"\s+", "", evidence["quote"])
            if not any(citation.get("source_id") == evidence["source_id"]
                       and phrase in re.sub(r"\s+", "", citation.get("quote", "")) for citation in citations):
                failures.append("missing required quote phrase: " + evidence["quote"])
    if records is not None:
        by_id = {record["id"]: record for record in records}
        markers = {f"C{number}" for number in re.findall(r"\[C(\d+)\]", content)}
        citation_ids = {c.get("citation_id") for c in citations}
        if markers != citation_ids or len(citation_ids) != len(citations):
            failures.append("answer markers and citation records disagree")
        for citation in citations:
            source = by_id.get(citation.get("source_id"))
            if source is None:
                failures.append("citation source absent from frozen corpus")
                continue
            if citation.get("version") != source["version"] or citation.get("source_content_checksum") != source["content_checksum"]:
                failures.append("citation revision does not match frozen corpus")
            quote = re.sub(r"\s+", "", citation.get("quote", ""))
            if not quote or quote not in re.sub(r"\s+", "", source["content"]):
                failures.append("citation quote absent from frozen source")
    for term in case.get("forbidden", []):
        if term in content:
            failures.append("forbidden content: " + term)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--validate-only", action="store_true", help="Validate frozen cases and corpus without service calls")
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/knowledge_quality_audit.json")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--port", type=int, default=8115)
    args = parser.parse_args()
    fixture_bytes = args.cases.read_bytes()
    fixture, records = load_fixture(args.cases)
    if args.validate_only:
        print(f"Validated {len(records)} records and {len(fixture['cases'])} cases; no service calls")
        return 0
    if not args.live:
        parser.error("--live is required for paid real-service calls")
    if args.output_dir is None:
        parser.error("--output-dir is required for live audit evidence")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    runtime = out / "runtime"
    runtime.mkdir()
    (out / "frozen-cases.json").write_bytes(fixture_bytes)
    from backend.config import settings
    save(runtime / "knowledge.json", records)
    save(out / "frozen-knowledge.json", records)
    manifest = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
        "python": sys.version, "os": platform.platform(), "cpu": platform.processor(),
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "configuration": {key: getattr(settings, key) for key in [
            "model_provider", "model_name", "judge_model_name", "citation_validation_mode",
            "embedding_model", "reranker_model", "retrieval_top_k", "retrieval_strategy",
            "web_search_provider", "request_max_total_seconds",
        ]},
        "mocked": False, "corpus": fixture.get("corpus_description", "synthetic only"),
        "corpus_sha256": hashlib.sha256((runtime / "knowledge.json").read_bytes()).hexdigest(),
        "semantic_review": "pending",
    }
    save(out / "manifest.json", manifest)
    (out / "source.patch").write_bytes(subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT))
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    manifest["source_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
        for p in paths if p.endswith((".py", ".ts", ".vue", ".json")) and (ROOT / p).is_file()}
    save(out / "manifest.json", manifest)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("Audit port occupied; no existing service will be stopped")
    reader = fixture.get("reader", {})
    reader_id = reader.get("user_id", "audit_reader")
    manifest["reader"] = {"user_id": reader_id, "roles": reader.get("roles", "employee,knowledge_reader"),
        "departments": reader.get("departments", "Finance"), "scopes": reader.get("scopes", "internal")}
    save(out / "manifest.json", manifest)
    env = os.environ.copy()
    env.update({
        "KNOWLEDGE_BASE_PATH": str(runtime / "knowledge.json"),
        "SQLITE_DB_PATH": str(runtime / "memory.db"),
        "CHROMA_PERSIST_DIR": str(runtime / "chroma"),
        "UPLOAD_DIR": str(runtime / "uploads"),
        "TRACE_LOG_PATH": str(runtime / "trace.jsonl"),
        "TRACE_ENABLED": "true", "TRACE_INCLUDE_CONTENT": "false",
        "AUTH_MODE": "development", "AUTH_DEV_USER_ID": reader_id,
        "AUTH_DEV_ROLES": manifest["reader"]["roles"], "AUTH_DEV_SCOPES": manifest["reader"]["scopes"],
        "AUTH_DEV_DEPARTMENTS": manifest["reader"]["departments"], "AUTH_DEV_INTERNAL_USER": "true",
        "PYTHONIOENCODING": "utf-8",
    })
    report = {"rows": [], "access_checks": [], "semantic_review": "pending"}
    base = f"http://127.0.0.1:{args.port}"
    with (out / "backend.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(timeout=100) as client:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("isolated backend exited; inspect backend.log")
                    try:
                        if client.get(base + "/health", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.25)
                else:
                    raise TimeoutError("isolated backend startup deadline")
                listing = client.get(base + "/knowledge/records")
                listing.raise_for_status()
                if any(record["id"] == "audit-secret" for record in records):
                    report["access_checks"].append({
                        "name": "hidden source absent from listing",
                        "passed": "audit-secret" not in {r["source_id"] for r in listing.json()["items"]},
                    })
                    for suffix in ["", "/download"]:
                        response = client.get(base + "/knowledge/records/audit-secret" + suffix)
                        report["access_checks"].append({"name": "hidden source" + suffix, "status": response.status_code, "passed": response.status_code == 404})
                sessions = {}
                for case in fixture["cases"]:
                    sid = sessions.setdefault(case["session"], "audit-" + uuid.uuid4().hex)
                    row = {"case_id": case["case_id"], "expected": case, "session_id": sid, "turn_id": uuid.uuid4().hex}
                    started = time.perf_counter()
                    events = []
                    try:
                        with client.stream("POST", base + "/chat/stream", json={
                            "message": case["question"], "mode": "knowledge", "web_search": False,
                            "user_id": reader_id, "session_id": sid, "turn_id": row["turn_id"],
                        }) as response:
                            response.raise_for_status()
                            for line in response.iter_lines():
                                if line.startswith("data:"):
                                    event = json.loads(line[5:])
                                    events.append(event)
                                    if event.get("type") == "result":
                                        row["result_ms"] = round((time.perf_counter() - started) * 1000, 2)
                        results = [event for event in events if event.get("type") == "result"]
                        assert len(results) == 1 and any(e.get("type") == "done" for e in events), "incomplete SSE"
                        row["result"] = results[0]
                        row["precheck_failures"] = precheck(case, results[0], records)
                        row["semantic_review"] = "pending"
                        row["nodes"] = list(dict.fromkeys(e.get("node") for e in events if e.get("type") == "status"))
                    except Exception as error:
                        row["transport_error"] = type(error).__name__
                    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                    report["rows"].append(row)
                    save(out / "report.json", report)
                    print(case["case_id"], row.get("precheck_failures", row.get("transport_error")), flush=True)
                for key, sid in sessions.items():
                    history = client.get(base + f"/chat/history/{reader_id}/{sid}")
                    history.raise_for_status()
                    save(out / f"history-{key}.json", history.json())
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            save(out / "report.json", report)
    return int(any(row.get("transport_error") or row.get("precheck_failures") for row in report["rows"]) or any(not check["passed"] for check in report["access_checks"]))


if __name__ == "__main__":
    raise SystemExit(main())
