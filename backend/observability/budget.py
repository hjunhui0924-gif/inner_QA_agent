"""Run-scoped request budgets and model usage accounting."""

from __future__ import annotations

import math
import time
import asyncio
from contextlib import asynccontextmanager
from collections.abc import Mapping
from dataclasses import dataclass, field


class BudgetExceededError(RuntimeError):
    """Raised when a request cannot start another model or tool call."""

    def __init__(self, reason: str, node: str) -> None:
        self.reason = reason
        self.node = node
        super().__init__(reason)


BUDGET_EXHAUSTED_CODE = "request_budget_exhausted"
EXHAUSTION_REASONS = frozenset(
    {
        "deadline_exceeded",
        "model_call_limit",
        "tool_call_limit",
        "input_token_limit",
        "output_token_limit",
        "cost_limit",
    }
)


def _usage_number(value: object) -> int:
    """Read a finite non-negative token count from provider metadata."""

    if isinstance(value, bool):
        return 0
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def _usage_cost(value: object) -> float | None:
    """Read an optional finite non-negative provider-reported cost."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _usage_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def extract_usage(value: object) -> tuple[int, int, float | None]:
    """Extract input/output tokens from common LangChain provider shapes.

    Providers expose usage in slightly different places. Missing usage is a
    valid result and is represented by zero tokens; the request and call
    counters remain authoritative even when token metadata is unavailable.
    """

    candidates: list[Mapping[str, object]] = []
    direct = _usage_mapping(value)
    if direct is not None:
        candidates.append(direct)
    for attribute in ("usage_metadata", "response_metadata", "usage"):
        nested = _usage_mapping(getattr(value, attribute, None))
        if nested is not None:
            candidates.append(nested)
            for key in ("token_usage", "usage", "output", "input"):
                child = _usage_mapping(nested.get(key))
                if child is not None:
                    candidates.append(child)

    input_tokens = 0
    output_tokens = 0
    provider_cost: float | None = None
    input_keys = ("input_tokens", "prompt_tokens", "input_token_count")
    output_keys = ("output_tokens", "completion_tokens", "output_token_count")
    cost_keys = ("cost", "total_cost", "estimated_cost")
    for candidate in candidates:
        if not input_tokens:
            input_tokens = next(
                (_usage_number(candidate.get(key)) for key in input_keys if key in candidate),
                0,
            )
        if not output_tokens:
            output_tokens = next(
                (_usage_number(candidate.get(key)) for key in output_keys if key in candidate),
                0,
            )
        if provider_cost is None:
            provider_cost = next(
                (_usage_cost(candidate.get(key)) for key in cost_keys if key in candidate),
                None,
            )
        if input_tokens and output_tokens and provider_cost is not None:
            break
    return input_tokens, output_tokens, provider_cost


@dataclass
class RequestBudget:
    """A mutable budget that lives only for one graph/API request."""

    max_model_calls: int
    max_tool_calls: int
    max_total_seconds: float
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    max_estimated_cost: float = 0.0
    _started_at: float = field(default_factory=time.monotonic, init=False, repr=False)
    _exhausted_reason: str | None = field(default=None, init=False, repr=False)
    _last_node: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (
                self.max_model_calls,
                self.max_tool_calls,
                self.max_input_tokens,
                self.max_output_tokens,
            )
        ):
            raise ValueError("Request call and token limits must be integers.")
        if (
            isinstance(self.max_total_seconds, bool)
            or not isinstance(self.max_total_seconds, (int, float))
            or not math.isfinite(self.max_total_seconds)
        ):
            raise ValueError("Request deadline must be finite and positive.")
        if (
            self.max_model_calls < 0
            or self.max_tool_calls < 0
            or self.max_input_tokens < 0
            or self.max_output_tokens < 0
        ):
            raise ValueError("Request call limits must not be negative.")
        if self.max_total_seconds <= 0:
            raise ValueError("Request deadline must be positive.")
        if (
            isinstance(self.input_price_per_1k, bool)
            or not isinstance(self.input_price_per_1k, (int, float))
            or isinstance(self.output_price_per_1k, bool)
            or not isinstance(self.output_price_per_1k, (int, float))
            or self.input_price_per_1k < 0
            or self.output_price_per_1k < 0
            or not math.isfinite(self.input_price_per_1k)
            or not math.isfinite(self.output_price_per_1k)
            or not isinstance(self.max_estimated_cost, (int, float))
            or isinstance(self.max_estimated_cost, bool)
            or self.max_estimated_cost < 0
            or not math.isfinite(self.max_estimated_cost)
        ):
            raise ValueError("Model prices and cost limits must be finite and non-negative.")

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self._started_at)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_total_seconds - self.elapsed_seconds)

    @property
    def exhausted_reason(self) -> str | None:
        return self._exhausted_reason

    def _raise_exhausted(self, reason: str, node: str) -> None:
        self._exhausted_reason = reason if reason in EXHAUSTION_REASONS else "budget_limit"
        self._last_node = str(node)[:80] or "request"
        raise BudgetExceededError(BUDGET_EXHAUSTED_CODE, node)

    def mark_exhausted(self, reason: str, node: str) -> None:
        """Record exhaustion without raising, for timeout/error handlers."""

        if self._exhausted_reason is None:
            self._exhausted_reason = reason
        self._last_node = node

    def exhaust(self, reason: str, node: str) -> None:
        """Mark a deadline/provider boundary as exhausted and raise safely."""

        self._raise_exhausted(reason, node)

    def ensure_available(self) -> None:
        """Fail before a new call when the request deadline has elapsed."""

        if self._exhausted_reason is not None:
            raise BudgetExceededError(BUDGET_EXHAUSTED_CODE, self._last_node or "request")
        if self.elapsed_seconds >= self.max_total_seconds:
            self._raise_exhausted("deadline_exceeded", self._last_node or "request")
        if self.max_input_tokens and self.input_tokens >= self.max_input_tokens:
            self._raise_exhausted("input_token_limit", self._last_node or "request")
        if self.max_output_tokens and self.output_tokens >= self.max_output_tokens:
            self._raise_exhausted("output_token_limit", self._last_node or "request")
        if self.max_estimated_cost and self.estimated_cost >= self.max_estimated_cost:
            self._raise_exhausted("cost_limit", self._last_node or "request")

    def consume_model_call(self, node: str) -> None:
        """Reserve one model attempt before constructing/invoking the model."""

        self.ensure_available()
        if self.model_calls >= self.max_model_calls:
            self._raise_exhausted("model_call_limit", node)
        self.model_calls += 1
        self._last_node = node

    def consume_tool_call(self, node: str) -> None:
        """Reserve one tool attempt before executing the tool."""

        self.ensure_available()
        if self.tool_calls >= self.max_tool_calls:
            self._raise_exhausted("tool_call_limit", node)
        self.tool_calls += 1
        self._last_node = node

    def estimate_usage_cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (
            max(0, input_tokens) / 1000 * self.input_price_per_1k
            + max(0, output_tokens) / 1000 * self.output_price_per_1k
        )

    def record_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost: float | None = None,
    ) -> None:
        """Accumulate usage metadata without making it a persistence object."""

        normalized_input = _usage_number(input_tokens)
        normalized_output = _usage_number(output_tokens)
        reported_cost = _usage_cost(cost)
        self.input_tokens += normalized_input
        self.output_tokens += normalized_output
        self.estimated_cost += (
            reported_cost
            if reported_cost is not None
            else self.estimate_usage_cost(
                input_tokens=normalized_input,
                output_tokens=normalized_output,
            )
        )
        if self.max_input_tokens and self.input_tokens >= self.max_input_tokens:
            self._raise_exhausted("input_token_limit", self._last_node or "usage")
        if self.max_output_tokens and self.output_tokens >= self.max_output_tokens:
            self._raise_exhausted("output_token_limit", self._last_node or "usage")
        if self.max_estimated_cost and self.estimated_cost >= self.max_estimated_cost:
            self._raise_exhausted("cost_limit", self._last_node or "usage")

    def record_response(self, response: object) -> None:
        """Record provider usage when a LangChain response exposes it."""

        input_tokens, output_tokens, cost = extract_usage(response)
        self.record_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-safe summary suitable for AgentState/checkpoints."""

        elapsed = self.elapsed_seconds
        exhausted_reason = self._exhausted_reason
        if exhausted_reason is None and elapsed >= self.max_total_seconds:
            exhausted_reason = "deadline_exceeded"
        return {
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_total_seconds": self.max_total_seconds,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_estimated_cost": self.max_estimated_cost,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "request_calls": self.model_calls + self.tool_calls,
            "remaining_model_calls": max(0, self.max_model_calls - self.model_calls),
            "remaining_tool_calls": max(0, self.max_tool_calls - self.tool_calls),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": round(max(0.0, self.estimated_cost), 8),
            "elapsed_seconds": round(elapsed, 6),
            "exhausted": exhausted_reason is not None,
            "exhausted_reason": exhausted_reason,
            "last_node": self._last_node,
        }


@dataclass
class RunContext:
    """LangGraph runtime context; never placed in AgentState/checkpoints."""

    budget: RequestBudget


@asynccontextmanager
async def budget_deadline(
    budget: RequestBudget | None,
    node: str,
):
    """Bound one awaited operation by the remaining request deadline."""

    if budget is None:
        yield
        return
    budget.ensure_available()
    timeout = asyncio.timeout(max(0.001, budget.remaining_seconds))
    try:
        async with timeout:
            yield
    except TimeoutError as exc:
        if timeout.expired():
            budget.mark_exhausted("deadline_exceeded", node)
            raise BudgetExceededError(BUDGET_EXHAUSTED_CODE, node) from exc
        raise
