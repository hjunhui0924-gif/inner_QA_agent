"""Tests for run-scoped request budgets and graph context propagation."""

from __future__ import annotations

import json
import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agent.graph import build_graph
from backend.agent.nodes import generate, tool_executor
from backend.agent.sessions import create_turn_state
from backend.agent.edges import route_after_routing
from backend.api.routes import ChatRequest, _stream_graph_unlocked
from backend.observability.budget import (
    BUDGET_EXHAUSTED_CODE,
    BudgetExceededError,
    RequestBudget,
    RunContext,
    extract_usage,
)


class RequestBudgetTests(unittest.TestCase):
    def test_model_and_tool_limits_reserve_calls_before_execution(self) -> None:
        budget = RequestBudget(max_model_calls=1, max_tool_calls=1, max_total_seconds=30)

        budget.consume_model_call("route_query")
        budget.consume_tool_call("tool_executor")

        with self.assertRaises(BudgetExceededError) as model_error:
            budget.consume_model_call("generate")
        self.assertEqual(model_error.exception.args[0], BUDGET_EXHAUSTED_CODE)
        self.assertEqual(budget.snapshot()["exhausted_reason"], "model_call_limit")

    def test_deadline_is_enforced_and_snapshot_is_json_safe(self) -> None:
        budget = RequestBudget(max_model_calls=2, max_tool_calls=1, max_total_seconds=1)
        budget._started_at = time.monotonic() - 2

        with self.assertRaises(BudgetExceededError):
            budget.ensure_available()

        snapshot = budget.snapshot()
        self.assertTrue(snapshot["exhausted"])
        self.assertEqual(snapshot["exhausted_reason"], "deadline_exceeded")
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False)

    def test_usage_metadata_and_configured_prices_are_accumulated(self) -> None:
        budget = RequestBudget(
            max_model_calls=2,
            max_tool_calls=1,
            max_total_seconds=30,
            input_price_per_1k=0.1,
            output_price_per_1k=0.2,
        )
        response = AIMessage(
            content="回答",
            usage_metadata={"input_tokens": 120, "output_tokens": 50, "total_tokens": 170},
        )

        self.assertEqual(extract_usage(response), (120, 50, None))
        budget.consume_model_call("generate")
        budget.record_response(response)

        snapshot = budget.snapshot()
        self.assertEqual(snapshot["input_tokens"], 120)
        self.assertEqual(snapshot["output_tokens"], 50)
        self.assertEqual(snapshot["estimated_cost"], 0.022)

    def test_reported_cost_takes_precedence_and_invalid_usage_is_safe(self) -> None:
        budget = RequestBudget(
            max_model_calls=1,
            max_tool_calls=1,
            max_total_seconds=30,
            input_price_per_1k=100,
            output_price_per_1k=100,
        )
        budget.record_usage(input_tokens=-10, output_tokens="bad", cost=0.003)
        budget.record_usage(input_tokens=1, output_tokens=2, cost=float("nan"))

        snapshot = budget.snapshot()
        self.assertEqual(snapshot["input_tokens"], 1)
        self.assertEqual(snapshot["output_tokens"], 2)
        self.assertEqual(snapshot["estimated_cost"], 0.303)
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False)

    def test_invalid_budget_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RequestBudget(max_model_calls=True, max_tool_calls=1, max_total_seconds=30)
        with self.assertRaises(ValueError):
            RequestBudget(max_model_calls=1, max_tool_calls=1, max_total_seconds=float("inf"))
        with self.assertRaises(ValueError):
            RequestBudget(
                max_model_calls=1,
                max_tool_calls=1,
                max_total_seconds=30,
                max_estimated_cost=float("nan"),
            )

    def test_deadline_context_cancels_slow_operation(self) -> None:
        from backend.observability.budget import budget_deadline

        budget = RequestBudget(max_model_calls=1, max_tool_calls=1, max_total_seconds=0.01)
        async def run() -> None:
            with self.assertRaises(BudgetExceededError):
                async with budget_deadline(budget, "slow_tool"):
                    await asyncio.sleep(0.05)
        asyncio.run(run())
        self.assertTrue(budget.snapshot()["exhausted"])


class BudgetedGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_exhaustion_routes_to_committed_safe_fallback(self) -> None:
        graph = build_graph(None)
        budget = RequestBudget(max_model_calls=0, max_tool_calls=0, max_total_seconds=30)
        result = await graph.ainvoke(
            create_turn_state(
                message="请回答这个问题",
                user_id="budget-user",
                session_id="budget-session",
                trace_id="budget-trace",
                mode="knowledge",
            ),
            context=RunContext(budget=budget),
        )

        self.assertEqual(
            result["answer"],
            "本次请求已达到资源限制，未提交未经验证的回答，请稍后重试。",
        )
        self.assertEqual(result["answer_disposition"], "fallback")
        self.assertEqual(result["candidate_answer"], "")
        self.assertEqual(
            [message.content for message in result["messages"] if message.type == "ai"],
            ["本次请求已达到资源限制，未提交未经验证的回答，请稍后重试。"],
        )
        self.assertEqual(result["budget_snapshot"]["model_calls"], 0)
        self.assertTrue(result["budget_snapshot"]["exhausted"])
        self.assertNotIn("budget", result)
        self.assertEqual(route_after_routing({"failure_stage": "runtime"}), "fallback_answer")

    async def test_checkpoint_keeps_budget_snapshot_but_not_runtime_budget_object(self) -> None:
        with TemporaryDirectory() as directory:
            async with AsyncSqliteSaver.from_conn_string(
                str(Path(directory) / "checkpoint.db")
            ) as saver:
                await saver.setup()
                graph = build_graph(saver)
                thread_id = "budget-checkpoint-user_budget-checkpoint-session"
                await graph.ainvoke(
                    create_turn_state(
                        message="请回答这个问题",
                        user_id="budget-checkpoint-user",
                        session_id="budget-checkpoint-session",
                        trace_id="budget-checkpoint-trace",
                        mode="knowledge",
                    ),
                    config={"configurable": {"thread_id": thread_id}},
                    context=RunContext(
                        budget=RequestBudget(
                            max_model_calls=0,
                            max_tool_calls=0,
                            max_total_seconds=30,
                        )
                    ),
                )
                checkpoints = [
                    item
                    async for item in saver.alist(
                        {"configurable": {"thread_id": thread_id}}
                    )
                ]

        self.assertTrue(checkpoints)
        snapshots = []
        for checkpoint in checkpoints:
            channel_values = checkpoint.checkpoint.get("channel_values", {})
            self.assertNotIn("budget", channel_values)
            if (
                "budget_snapshot" in channel_values
                and channel_values["budget_snapshot"] is not None
            ):
                snapshots.append(channel_values["budget_snapshot"])
        self.assertTrue(snapshots)
        self.assertTrue(all(isinstance(snapshot, dict) for snapshot in snapshots))

    async def test_failed_model_and_tool_attempts_are_counted(self) -> None:
        model_budget = RequestBudget(max_model_calls=1, max_tool_calls=1, max_total_seconds=30)
        with patch(
            "backend.agent.nodes._build_model",
            side_effect=RuntimeError("provider unavailable"),
        ):
            model_result = await generate(
                {"query": "问题", "route": "direct", "messages": []},
                runtime=__import__("langgraph.runtime", fromlist=["Runtime"]).Runtime(
                    context=RunContext(model_budget)
                ),
            )
        self.assertEqual(model_result["generation_error"], "generation_unavailable")
        self.assertEqual(model_budget.model_calls, 1)
        self.assertEqual(model_result["model_call_count"], 1)

        tool_budget = RequestBudget(max_model_calls=1, max_tool_calls=1, max_total_seconds=30)
        with patch(
            "backend.agent.nodes._choose_tool_result",
            side_effect=RuntimeError("tool provider unavailable"),
        ):
            tool_result = await tool_executor(
                {"query": "问题"},
                runtime=__import__("langgraph.runtime", fromlist=["Runtime"]).Runtime(
                    context=RunContext(tool_budget)
                ),
            )
        self.assertEqual(tool_budget.tool_calls, 1)
        self.assertEqual(tool_result["tool_call_count"], 1)
        self.assertEqual(tool_result["failure_stage"], "tool")


class BudgetedApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_passes_one_budget_context_to_graph_and_title_call(self) -> None:
        class FakeGraph:
            def __init__(self) -> None:
                self.context = None

            async def astream_events(self, graph_input, *, config, version, context=None):
                self.context = context
                yield {
                    "event": "on_chain_end",
                    "name": "commit_answer",
                    "metadata": {"langgraph_node": "commit_answer"},
                    "data": {
                        "output": {
                            "answer": "正式答案",
                            "citations": [],
                            "model_call_count": 0,
                            "tool_call_count": 0,
                            "request_call_count": 0,
                        }
                    },
                }

        class TitleModel:
            async def ainvoke(self, messages):
                return AIMessage(content="预算测试", usage_metadata={
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "total_tokens": 6,
                })

        async def disconnected() -> bool:
            return False

        graph = FakeGraph()
        budget = RequestBudget(max_model_calls=3, max_tool_calls=1, max_total_seconds=30)
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(graph=graph)),
            is_disconnected=disconnected,
            headers={},
        )
        payload = ChatRequest(message="问题", user_id="budget-user", session_id="budget-session")
        committed = {"user_content": "问题", "content": "正式答案", "citations": []}
        with patch("backend.api.routes._request_budget", return_value=budget), \
            patch("backend.api.routes.persist_completed_turn", new=AsyncMock(return_value=committed)), \
            patch("backend.api.routes.list_chat_sessions", new=AsyncMock(return_value=[])), \
            patch("backend.api.routes.append_chat_message", new=AsyncMock()), \
            patch("backend.api.routes.upsert_chat_session", new=AsyncMock()), \
            patch("backend.api.routes.ChatOpenAI", return_value=TitleModel()), \
            patch.object(__import__("backend.api.routes", fromlist=["settings"]).settings, "dashscope_api_key", "test-key"), \
            patch.object(__import__("backend.api.routes", fromlist=["settings"]).settings, "trace_enabled", False):
            events = [event async for event in _stream_graph_unlocked(request, payload)]

        self.assertIsNotNone(graph.context)
        self.assertIs(graph.context.budget, budget)
        self.assertEqual(budget.model_calls, 1)
        self.assertIn("正式答案", "".join(events))


if __name__ == "__main__":
    unittest.main()
