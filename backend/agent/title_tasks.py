"""Application-owned, bounded best-effort title jobs; never part of turn budgets."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, UTC

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from backend.agent.memory import first_session_question, update_title_if_first
from backend.agent.sessions import session_operation, thread_id_for
from backend.config import settings
from backend.observability.budget import RequestBudget
from backend.observability.tracing import record_trace


@dataclass(frozen=True)
class TitleJob:
    user_id: str
    session_id: str
    first_id: int
    question: str


class TitleTaskManager:
    def __init__(
        self, *, concurrency: int = 2, capacity: int = 32, timeout: float = 10
    ):
        if concurrency < 1 or capacity < 0 or timeout <= 0:
            raise ValueError("Invalid title queue limits")
        self.concurrency, self.capacity, self.timeout = concurrency, capacity, timeout
        self.pending: deque[TitleJob] = deque()
        self.running: set[asyncio.Task] = set()
        self.keys: set[tuple[str, str]] = set()
        self.completed: set[tuple[str, str, int]] = set()
        self.failed: dict[tuple[str, str, int], float] = {}
        self.metrics: deque[dict] = deque(maxlen=200)
        self.closed = False

    def submit(self, job: TitleJob, *, priority: bool = False) -> bool:
        key = (job.user_id, job.session_id)
        identity = (*key, job.first_id)
        if (
            self.closed
            or key in self.keys
            or identity in self.completed
            or time.monotonic() - self.failed.get(identity, -float("inf")) < 600
        ):
            return False
        if len(self.running) >= self.concurrency and len(self.pending) >= self.capacity:
            return False
        self.keys.add(key)
        (self.pending.appendleft if priority else self.pending.append)(job)
        self._start()
        return True

    def _start(self) -> None:
        while not self.closed and self.pending and len(self.running) < self.concurrency:
            task = asyncio.create_task(self._run(self.pending.popleft()))
            self.running.add(task)
            task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self.running.discard(task)
        self._start()

    async def _run(self, job: TitleJob) -> None:
        key = (job.user_id, job.session_id)
        identity = (*key, job.first_id)
        started = time.perf_counter()
        budget = RequestBudget(
            max_model_calls=1,
            max_tool_calls=0,
            max_total_seconds=self.timeout,
            input_price_per_1k=settings.model_input_price_per_1k,
            output_price_per_1k=settings.model_output_price_per_1k,
        )
        outcome = "failed"
        try:
            async with asyncio.timeout(self.timeout):
                current = await first_session_question(*key)
                if current is None or current["first_id"] != job.first_id:
                    outcome = "stale"
                    return
                model = ChatOpenAI(
                    model=settings.model_name,
                    api_key=settings.dashscope_api_key,
                    base_url=settings.dashscope_base_url,
                    temperature=0,
                    max_tokens=24,
                    timeout=self.timeout,
                    max_retries=0,
                    stream_usage=True,
                    extra_body={"enable_thinking": False},
                )
                budget.consume_model_call("session_title")
                response = await model.ainvoke(
                    [
                        SystemMessage(
                            content=(
                                "把以下首次提问概括为不超过12个汉字的标题，只返回标题：\n"
                                + job.question
                            )
                        )
                    ]
                )
                budget.record_response(response)
                title = str(response.content).strip()
                if not title:
                    raise ValueError("empty title")
                async with session_operation(thread_id_for(*key)):
                    updated = await update_title_if_first(*key, job.first_id, title)
                self.completed.add(identity)
                outcome = "completed" if updated else "stale"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            self.failed[identity] = time.monotonic()
        finally:
            self.keys.discard(key)
            metric = {
                "stage": "session_title",
                "outcome": outcome,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "budget": budget.snapshot(),
            }
            self.metrics.append(metric)
            if settings.trace_enabled:
                try:
                    await asyncio.to_thread(
                        record_trace,
                        {
                            **metric,
                            "created_at": datetime.now(UTC).isoformat(),
                            "kind": "background_title",
                        },
                        settings.trace_log_path,
                        max_bytes=settings.trace_max_bytes,
                        backup_count=settings.trace_backup_count,
                        retention_days=settings.trace_retention_days,
                    )
                except Exception:
                    pass

    async def close(self) -> None:
        self.closed = True
        self.pending.clear()
        tasks = list(self.running)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.running.clear()
        self.keys.clear()
