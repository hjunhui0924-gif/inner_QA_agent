"""Regression tests preventing exception details from crossing public boundaries."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agent.nodes import generate
from backend.api.routes import ChatRequest, _stream_graph, _stream_graph_unlocked
from backend.observability.tracing import build_trace
from backend.observability.safe_errors import safe_diagnostic, safe_fallback_reason


class ErrorSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_exception_becomes_stable_generation_error(self) -> None:
        with patch(
            "backend.agent.nodes._build_model",
            side_effect=RuntimeError("D:\\private\\secrets\\provider-response.json"),
        ):
            result = await generate(
                {
                    "query": "制度是什么？",
                    "route": "direct",
                    "messages": [],
                }
            )

        self.assertEqual(result["generation_error"], "generation_unavailable")
        self.assertNotIn("provider-response", str(result))

    async def test_trace_sanitizes_exception_details_and_status_events(self) -> None:
        trace = build_trace(
            trace_id="trace-safe",
            query="问题",
            state={
                "answer": "",
                "generation_error": "PermissionDeniedError: https://internal.example/token",
                "failure_stage": "runtime",
                "failure_reason": "RuntimeError: C:\\private\\memory.db",
                "status_events": ["error:RuntimeError: C:\\private\\memory.db"],
                "retrieval_metadata": {
                    "degraded_reason": "DatabaseFailure: postgres://internal",
                },
            },
        )

        encoded = json.dumps(trace, ensure_ascii=False)
        self.assertNotIn("PermissionDeniedError", encoded)
        self.assertNotIn("internal.example", encoded)
        self.assertNotIn("memory.db", encoded)
        self.assertEqual(trace["generation_error"], "generation_unavailable")
        self.assertEqual(trace["failure_reason"], "internal_error")
        self.assertEqual(trace["failure_stage"], "runtime")
        self.assertEqual(trace["retrieval"]["degraded_reason"], "internal_error")

        fallback_trace = build_trace(
            trace_id="trace-fallback-safe",
            query="问题",
            state={
                "answer": "fallback",
                "fallback_reason": "DatabaseFailure: relation missing",
            },
        )
        self.assertEqual(fallback_trace["fallback_reason"], "unknown")

    async def test_diagnostics_use_allowlist_instead_of_guessing_exception_shape(self) -> None:
        self.assertEqual(
            safe_diagnostic("DatabaseFailure: relation missing"),
            "internal_error",
        )
        self.assertEqual(
            safe_diagnostic(r"\\server\share\private.db"),
            "internal_error",
        )
        self.assertEqual(safe_fallback_reason("tool_error"), "tool_error")
        self.assertEqual(safe_fallback_reason("postgres://internal"), "unknown")

    async def test_stream_runtime_error_is_safe_for_sse(self) -> None:
        class FailingGraph:
            async def astream_events(self, graph_input, *, config, version, context=None):
                raise RuntimeError("C:\\private\\checkpoint.db")
                yield graph_input

        async def disconnected() -> bool:
            return False

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(graph=FailingGraph())),
            is_disconnected=disconnected,
        )
        payload = ChatRequest(message="问题", user_id="safe-user", session_id="safe-session")
        with patch("backend.api.routes.settings.trace_enabled", False):
            events = [event async for event in _stream_graph_unlocked(request, payload)]

        encoded = "".join(events)
        self.assertNotIn("checkpoint.db", encoded)
        self.assertIn("服务暂时不可用", encoded)
        self.assertIn('"node": "error"', encoded)

    async def test_setup_failure_before_graph_stream_is_safe_for_sse(self) -> None:
        async def disconnected() -> bool:
            return False

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(graph=object())),
            is_disconnected=disconnected,
            headers={},
        )
        payload = ChatRequest(
            message="问题",
            user_id="safe-user",
            session_id="safe-session",
            turn_id="safe-turn",
        )
        with patch(
            "backend.api.routes.load_completed_turn",
            side_effect=RuntimeError("\\\\server\\share\\checkpoint.db"),
        ), patch("backend.api.routes.settings.trace_enabled", False):
            events = [event async for event in _stream_graph_unlocked(request, payload)]

        encoded = "".join(events)
        self.assertNotIn("checkpoint.db", encoded)
        self.assertIn("服务暂时不可用", encoded)

    async def test_session_lock_setup_failure_is_safe_for_sse(self) -> None:
        async def disconnected() -> bool:
            return False

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(graph=object())),
            is_disconnected=disconnected,
        )
        payload = ChatRequest(message="问题", user_id="safe-user", session_id="safe-session")
        with patch(
            "backend.api.routes.session_operation",
            side_effect=RuntimeError("postgres://internal/credentials"),
        ), patch("backend.api.routes.settings.trace_enabled", False):
            events = [event async for event in _stream_graph(request, payload)]

        encoded = "".join(events)
        self.assertNotIn("postgres://", encoded)
        self.assertIn("服务暂时不可用", encoded)

    async def test_uninitialized_graph_uses_safe_sse_failure(self) -> None:
        async def disconnected() -> bool:
            return False

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(graph=None)),
            is_disconnected=disconnected,
        )
        payload = ChatRequest(message="问题", user_id="safe-user", session_id="safe-session")
        with patch("backend.api.routes.settings.trace_enabled", False):
            events = [event async for event in _stream_graph_unlocked(request, payload)]

        encoded = "".join(events)
        self.assertNotIn("Graph is not initialized", encoded)
        self.assertIn("服务暂时不可用", encoded)

    async def test_trace_failure_after_delivery_does_not_append_duplicate_error_events(self) -> None:
        class OneAnswerGraph:
            async def astream_events(self, graph_input, *, config, version, context=None):
                yield {
                    "event": "on_chain_end",
                    "name": "commit_answer",
                    "metadata": {"langgraph_node": "commit_answer"},
                    "data": {
                        "output": {
                            "answer": "正式答案",
                            "citations": [],
                            "model_call_count": 1,
                            "tool_call_count": 0,
                        }
                    },
                }

        async def disconnected() -> bool:
            return False

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(graph=OneAnswerGraph())),
            is_disconnected=disconnected,
        )
        payload = ChatRequest(message="问题", user_id="safe-user", session_id="safe-session")
        committed = {"user_content": "问题", "content": "正式答案", "citations": []}
        trace = {"failure_type": "none", "failure_stage": None, "failure_reason": None}
        with patch(
            "backend.api.routes.persist_completed_turn",
            new=AsyncMock(return_value=committed),
        ), patch(
            "backend.api.routes.list_chat_sessions",
            new=AsyncMock(return_value=[{"session_id": "safe-session"}]),
        ), patch(
            "backend.api.routes.append_chat_message",
            new=AsyncMock(),
        ), patch(
            "backend.api.routes.build_trace",
            side_effect=[trace, RuntimeError("trace store private path")],
        ), patch("backend.api.routes.settings.trace_enabled", True):
            events = [event async for event in _stream_graph_unlocked(request, payload)]

        encoded = "".join(events)
        self.assertEqual(encoded.count('"type": "done"'), 1)
        self.assertNotIn('"node": "error"', encoded)
        self.assertIn("正式答案", encoded)


if __name__ == "__main__":
    unittest.main()
