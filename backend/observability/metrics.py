"""Small, serializable request metrics helpers used by agent nodes and the API."""

from __future__ import annotations

import time
from typing import Any


def record_call_stats(
    state: dict[str, Any],
    *,
    model_calls: int = 0,
    tool_calls: int = 0,
    started_at: float | None = None,
    elapsed_ms: float | None = None,
) -> dict[str, Any]:
    """Return cumulative counters for attempted model/tool calls.

    Call counters are deliberately derived from the two canonical dimensions so
    ``request_call_count`` cannot drift from ``model_call_count`` plus
    ``tool_call_count``.  Node-level elapsed time is useful before the API has
    the request boundary; the API replaces ``total_latency_ms`` with the final
    wall-clock duration before emitting the result.
    """

    model_count = max(0, int(state.get("model_call_count", 0))) + max(
        0, int(model_calls)
    )
    tool_count = max(0, int(state.get("tool_call_count", 0))) + max(
        0, int(tool_calls)
    )
    if elapsed_ms is None and started_at is not None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
    duration = max(0.0, float(elapsed_ms or 0.0))
    return {
        "request_call_count": model_count + tool_count,
        "model_call_count": model_count,
        "tool_call_count": tool_count,
        "total_latency_ms": max(0.0, float(state.get("total_latency_ms", 0.0)))
        + duration,
    }
