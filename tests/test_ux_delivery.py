"""Provisional delivery, replay metadata and explicit network-choice contracts."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage
from backend.config import settings
from backend.api.routes import ChatRequest, _stream_graph
from backend.agent.nodes import route_query
from backend.agent.memory import (ensure_user_memory_db, load_completed_turn,
    persist_completed_turn, append_chat_message, load_chat_messages)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(settings, "sqlite_db_path", str(Path(self.temp.name) / "memory.db"))
        self.db.start()
        self.user = patch.object(settings, "auth_dev_user_id", "u")
        self.user.start()
        self.trace = patch.object(settings, "trace_enabled", False)
        self.trace.start()
        await ensure_user_memory_db(Path(settings.sqlite_db_path))

    async def asyncTearDown(self):
        self.user.stop()
        self.trace.stop()
        self.db.stop()
        self.temp.cleanup()

    def stream(self, mode="general", web=False, turn="turn", node="generate"):
        self.committed = False
        owner = self
        class Graph:
            async def astream_events(self, state, **kwargs):
                yield {"event": "on_chain_end", "name": "route_query", "metadata": {"langgraph_node": "route_query"}, "data": {"output": {"route": "direct"}}}
                yield {"event": "on_custom_event", "name": "general_preview_delta", "metadata": {"langgraph_node": node}, "data": {"content": "partial draft", "generation_id": "gen-1"}}
                owner.committed = True
                yield {"event": "on_chain_end", "name": "commit_answer", "metadata": {"langgraph_node": "commit_answer"}, "data": {"output": {"answer": "final answer", "citations": [], "answer_disposition": "accepted"}}}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=Graph())), is_disconnected=AsyncMock(return_value=False))
        return _stream_graph(request, ChatRequest(message="question", user_id="u", session_id="s", mode=mode, web_search=web, turn_id=turn))

    @staticmethod
    def event(raw):
        return json.loads(raw.split("data: ", 1)[1])

    async def test_preview_precedes_commit_and_result_replaces_it(self):
        events = []
        async for raw in self.stream():
            event = self.event(raw)
            if event["type"] == "preview_delta":
                self.assertFalse(self.committed)
                self.assertIsNone(await load_completed_turn("u", "s", "turn"))
            events.append(event)
        self.assertEqual([e["type"] for e in events].count("preview_delta"), 1)
        self.assertEqual(next(e["content"] for e in events if e["type"] == "result"), "final answer")
        self.assertEqual((await load_completed_turn("u", "s", "turn"))["content"], "final answer")

    async def test_interrupted_preview_is_never_persisted(self):
        stream = self.stream()
        raw = await anext(stream)
        self.assertEqual(self.event(raw)["type"], "preview_delta")
        await stream.aclose()
        self.assertFalse(self.committed)
        self.assertIsNone(await load_completed_turn("u", "s", "turn"))
        self.assertEqual(await load_chat_messages("u", "s"), [])

    async def test_rag_web_and_router_events_never_expose_preview(self):
        for index, (mode, web, node) in enumerate([("knowledge", False, "generate"), ("general", True, "generate"), ("general", False, "route_query")]):
            with self.subTest(mode=mode, web=web, node=node):
                events = [self.event(raw) async for raw in self.stream(mode, web, str(index), node)]
                self.assertFalse(any(e["type"] == "preview_delta" for e in events))

    async def test_retry_metadata_survives_history_and_replay_without_parent(self):
        metadata = {"attempt_group_id": "cancelled-original", "retry_of_turn_id": "missing-parent", "mode": "general", "web_search": True,
                    "failure_type": "search_error", "failure_stage": "tool", "failure_reason": "web_search_unavailable"}
        await persist_completed_turn("u", "s", "turn", "question", "search unavailable", metadata=metadata)
        for role, content in [("user", "question"), ("assistant", "search unavailable")]:
            await append_chat_message("u", "s", role, content, turn_id="turn", message_id=f"turn:{role}")
        history = await load_chat_messages("u", "s")
        self.assertEqual(history[1]["attempt_group_id"], "cancelled-original")
        self.assertEqual(history[1]["failure_type"], "search_error")
        self.assertNotIn("failure_type", history[0])
        self.assertEqual(await load_chat_messages("another-user", "s"), [])
        events = [self.event(raw) async for raw in self.stream(web=True)]
        result = next(e for e in events if e["type"] == "result")
        self.assertEqual(result["failure_type"], "search_error")
        self.assertEqual(result["attempt_group_id"], "cancelled-original")
        self.assertFalse(self.committed)
        # First committed metadata wins on replay/duplicate persistence.
        duplicate = await persist_completed_turn("u", "s", "turn", "question", "different", metadata={"attempt_group_id": "changed"})
        self.assertEqual(duplicate["attempt_group_id"], "cancelled-original")

    async def test_disabled_web_overrides_keywords_and_model_routing(self):
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content='{"route":"tool_call"}')
        with patch("backend.agent.nodes._build_model", return_value=model):
            result = await route_query({"mode": "general", "web_search": False, "query": "联网搜索最新消息", "messages": []})
        self.assertEqual(result["route"], "direct")
