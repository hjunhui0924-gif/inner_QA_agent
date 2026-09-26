"""Isolated HTTP SSE latency benchmark. Never saves questions, answers or secrets."""

from __future__ import annotations
import argparse
import csv
import json
import math
import time
import uuid
from pathlib import Path
import httpx


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)] if ordered else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Isolated backend URL, including API prefix",
    )
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/chat_latency_cases.json")
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("work/chat-latency"))
    parser.add_argument(
        "--user-id", default="user_001", help="Must match trusted server identity"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly enable model-consuming HTTP requests",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error(
            "--live is required; run only against an isolated backend and frozen corpus"
        )
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with httpx.Client(timeout=180) as client:
        for repeat in range(args.repeats):
            for case in cases:
                sid = "benchmark-" + str(uuid.uuid4())
                for turn, message in enumerate(case["messages"]):
                    row = dict(
                        case_id=case["case_id"],
                        category=case["category"],
                        repeat=repeat,
                        turn=turn,
                        turn_id=str(uuid.uuid4()),
                        first_status_ms=None,
                        result_ms=None,
                        ready_ms=None,
                        failure_type="transport_error",
                        replay=False,
                    )
                    started = time.perf_counter()
                    try:
                        with client.stream(
                            "POST",
                            args.base_url.rstrip("/") + "/chat/stream",
                            json=dict(
                                message=message,
                                user_id=args.user_id,
                                session_id=sid,
                                mode=case["mode"],
                                web_search=case["web_search"],
                                turn_id=row["turn_id"],
                            ),
                        ) as response:
                            response.raise_for_status()
                            for line in response.iter_lines():
                                if not line.startswith("data:"):
                                    continue
                                event = json.loads(line[5:])
                                elapsed = (time.perf_counter() - started) * 1000
                                if (
                                    event.get("type") == "status"
                                    and row["first_status_ms"] is None
                                ):
                                    row["first_status_ms"] = elapsed
                                if event.get("type") == "result":
                                    row.update(
                                        result_ms=elapsed,
                                        failure_type=event.get("failure_type")
                                        or "none",
                                        trace_id=event.get("trace_id"),
                                        budget=event.get("budget_snapshot"),
                                        replay=bool(event.get("replayed")),
                                    )
                        row["ready_ms"] = (time.perf_counter() - started) * 1000
                    except Exception as error:
                        if row["result_ms"] is None:
                            row["failure_type"] = (
                                "timeout"
                                if isinstance(error, httpx.TimeoutException)
                                else "transport_error"
                            )
                        row["transport_exception"] = type(error).__name__
                    row["elapsed_ms"] = (time.perf_counter() - started) * 1000
                    rows.append(row)
                    (args.output_dir / "raw.json").write_text(
                        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
    with (args.output_dir / "summary.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "category",
                "turn_kind",
                "n",
                "success",
                "timeouts",
                "p50_result_ms",
                "p95_result_ms",
                "p50_elapsed_ms",
                "p95_elapsed_ms",
            ],
        )
        writer.writeheader()
        for category in sorted({r["category"] for r in rows}):
            for kind in ["first", "followup"]:
                group = [
                    r
                    for r in rows
                    if r["category"] == category
                    and (r["turn"] == 0) == (kind == "first")
                    and not r["replay"]
                ]
                if not group:
                    continue
                times = [r["result_ms"] for r in group if r["result_ms"] is not None]
                elapsed = [r["elapsed_ms"] for r in group]
                writer.writerow(
                    dict(
                        category=category,
                        turn_kind=kind,
                        n=len(group),
                        success=sum(r["failure_type"] == "none" for r in group),
                        timeouts=sum(r["failure_type"] == "timeout" for r in group),
                        p50_result_ms=percentile(times, 0.5),
                        p95_result_ms=percentile(times, 0.95),
                        p50_elapsed_ms=percentile(elapsed, 0.5),
                        p95_elapsed_ms=percentile(elapsed, 0.95),
                    )
                )
    print(
        f"Saved {len(rows)} turns. HTTP result timing is not browser paint timing; small-sample P95 is exploratory."
    )


if __name__ == "__main__":
    main()
