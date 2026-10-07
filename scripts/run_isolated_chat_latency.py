"""Measure the final Agent with the frozen latency cases and synthetic corpus.

Requires --live; owns a new runtime and loopback server, never daily data.
Reports absolute HTTP timings, not a speedup or a semantic quality score.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_knowledge_quality_audit import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8116)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not args.live or args.repeats < 1:
        parser.error("--live and positive --repeats required")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    runtime = out / "runtime"
    runtime.mkdir()
    from backend.config import settings
    from backend.knowledge.schema import normalize_knowledge_record
    corpus = (ROOT / "tests/fixtures/chat_latency_corpus.md").read_text(encoding="utf-8")
    cases = ROOT / "tests/fixtures/chat_latency_cases.json"
    save(runtime / "knowledge.json", [normalize_knowledge_record(dict(
        id="m6-latency-policy", title="合成企业制度", content=corpus, source="synthetic",
        department="Finance", version="v2", status="active", effective_from="2026-01-01",
        access_scope="internal", public_internal=True,
    ))])
    manifest = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "os": platform.platform(),
        "python": sys.version, "mocked": False, "quality_verdict": "not evaluated by timing script",
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
        "corpus_sha256": hashlib.sha256(corpus.encode()).hexdigest(),
        "cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
        "configuration": {k: getattr(settings, k) for k in ["model_provider", "model_name", "judge_model_name", "citation_validation_mode", "embedding_model", "reranker_model", "retrieval_top_k", "web_search_provider"]}}
    save(out / "manifest.json", manifest)
    (out / "source.patch").write_bytes(subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT))
    (out / "cases.json").write_bytes(cases.read_bytes())
    (out / "corpus.md").write_text(corpus, encoding="utf-8")
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("port occupied; existing process left alone")
    env = os.environ.copy()
    env.update(KNOWLEDGE_BASE_PATH=str(runtime / "knowledge.json"), SQLITE_DB_PATH=str(runtime / "memory.db"),
        CHROMA_PERSIST_DIR=str(runtime / "chroma"), UPLOAD_DIR=str(runtime / "uploads"),
        TRACE_LOG_PATH=str(runtime / "trace.jsonl"), TRACE_ENABLED="true", TRACE_INCLUDE_CONTENT="false",
        AUTH_MODE="development", AUTH_DEV_USER_ID="user_001", AUTH_DEV_ROLES="employee,knowledge_reader",
        AUTH_DEV_SCOPES="internal", AUTH_DEV_DEPARTMENTS="Finance", PYTHONIOENCODING="utf-8")
    base = f"http://127.0.0.1:{args.port}"
    with (out / "backend.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("backend exited")
                try:
                    if httpx.get(base + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            else:
                raise TimeoutError("backend readiness deadline")
            # Warm the actual retrieval/model path; keep this out of measured rows.
            response = httpx.post(base + "/chat/stream", json=dict(message="差旅报销需要哪些材料？", mode="knowledge", web_search=False, user_id="user_001", session_id="latency-warmup", turn_id="latency-warmup"), timeout=100)
            response.raise_for_status()
            events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
            result = next(e for e in events if e.get("type") == "result")
            save(out / "warmup.json", result)
            if result.get("failure_type") != "none":
                raise RuntimeError("warmup knowledge query failed; benchmark not started")
            completed = subprocess.run([sys.executable, str(ROOT / "scripts/benchmark_chat_latency.py"), "--base-url", base,
                "--cases", str(cases), "--repeats", str(args.repeats), "--output-dir", str(out / "benchmark"), "--live"], env=env, cwd=ROOT, timeout=1800)
            # Titles have a 10s per-job bound. Report unfinished jobs as unknown.
            time.sleep(12)
            return completed.returncode
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
