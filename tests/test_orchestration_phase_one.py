"""Stage-one orchestration contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agent.graph import build_graph
from backend.agent.edges import route_after_hallucination_check
from backend.agent.memory import (
    append_chat_message,
    ensure_user_memory_db,
    load_chat_messages,
    load_completed_turn,
    persist_completed_turn,
)
from backend.agent.nodes import commit_answer, generate
from backend.agent.sessions import create_turn_state, thread_id_for
from backend.api.routes import ChatRequest, _stream_graph_unlocked
from backend.config import settings
from backend.observability.metrics import record_call_stats
from backend.observability.tracing import build_trace
from backend.retrieval.engine import RetrievalResult


class CandidateCommitTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_is_the_only_node_that_creates_formal_assistant_message(self) -> None:
        candidate = {
            "candidate_answer": "正式回答",
            "candidate_citations": [],
            "answer_disposition": "accepted",
            "hallucination_pass": True,
            "turn_id": "turn-1",
        }

        self.assertNotIn("messages", candidate)
        committed = await commit_answer(candidate)

        self.assertEqual(committed["answer"], "正式回答")
        self.assertEqual(committed["messages"][0].id, "turn-1:assistant")
        self.assertEqual(committed["candidate_answer"], "")

    async def test_pending_candidate_cannot_be_committed(self) -> None:
        result = await commit_answer(
            {
                "candidate_answer": "未经验证回答",
                "answer_disposition": "pending",
                "turn_id": "turn-1",
            }
        )

        self.assertNotIn("answer", result)
        self.assertNotIn("messages", result)

    async def test_fallback_candidate_is_committed_without_citations(self) -> None:
        result = await commit_answer(
            {
                "candidate_answer": "暂时无法可靠作答。",
                "candidate_citations": [{"citation_id": "C1"}],
                "answer_disposition": "fallback",
                "turn_id": "turn-fallback",
            }
        )

        self.assertEqual(result["answer"], "暂时无法可靠作答。")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["messages"][0].id, "turn-fallback:assistant")

    async def test_accepted_candidate_with_failed_validation_cannot_be_committed(self) -> None:
        result = await commit_answer(
            {
                "candidate_answer": "未通过校验",
                "answer_disposition": "accepted",
                "hallucination_pass": False,
                "turn_id": "turn-invalid",
            }
        )

        self.assertNotIn("answer", result)
        self.assertNotIn("messages", result)

    async def test_hallucination_reason_is_carried_into_retry_prompt(self) -> None:
        class StreamingModel:
            def __init__(self) -> None:
                self.prompt_messages: list[object] = []

            async def astream(self, prompt_messages: list[object]):
                self.prompt_messages = prompt_messages
                yield AIMessageChunk(content="修正后的回答")

        model = StreamingModel()
        with patch("backend.agent.nodes._build_model", return_value=model):
            await generate(
                {
                    "route": "rag",
                    "query": "制度是什么？",
                    "retrieved_docs": [Document(page_content="制度文本", metadata={})],
                    "messages": [HumanMessage(content="制度是什么？")],
                    "generation_instruction": "删除证据未支持的岗位结论。",
                }
            )

        prompt = str(model.prompt_messages[0].content)
        self.assertIn("删除证据未支持的岗位结论", prompt)
        self.assertIn("不是新的证据", prompt)

    async def test_failed_candidate_is_not_persisted_in_graph_checkpoint(self) -> None:
        class RetryModel:
            def __init__(self) -> None:
                self.generation_count = 0
                self.judge_count = 0

            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                prompt = str(getattr(prompt_messages[0], "content", ""))
                if "路由器" in prompt:
                    return AIMessage(content='{"route":"rag","reason":"test"}')
                if "相关性判断器" in prompt:
                    return AIMessage(content='{"is_relevant":true,"reason":"test"}')
                if "忠实于给定" in prompt:
                    self.judge_count += 1
                    passed = self.judge_count > 1
                    return AIMessage(
                        content=(
                            '{"hallucination_pass":'
                            + ("true" if passed else "false")
                            + ',"reason":"unsupported claim"}'
                        )
                    )
                raise AssertionError("unexpected model prompt")

            async def astream(self, prompt_messages: list[object]):
                self.generation_count += 1
                answer = (
                    "坏候选 制度文本 [C1]"
                    if self.generation_count == 1
                    else "好候选 制度文本 [C1]"
                )
                yield AIMessageChunk(content=answer)

        async def search_documents(
            query: str,
            top_k: int,
            *,
            filters: object | None = None,
        ) -> RetrievalResult:
            document = Document(
                page_content="制度文本",
                metadata={
                    "source_id": "policy",
                    "document_id": "policy",
                    "chunk_id": "policy:0",
                    "title": "制度",
                    "source": "seed",
                    "original_filename": "policy.md",
                },
            )
            return RetrievalResult(
                documents=[document],
                strategy="rerank",
                dense_candidates=1,
                lexical_candidates=1,
                fused_candidates=1,
                rerank_used=False,
                degraded_reason=None,
                latency_ms=1.0,
            )

        model = RetryModel()
        with patch(
            "backend.agent.nodes._build_model", return_value=model
        ), patch(
            "backend.agent.nodes.search_documents_with_metadata",
            side_effect=search_documents,
        ), patch("backend.agent.edges.settings.max_hallucination_retries", 1):
            with tempfile.TemporaryDirectory() as directory:
                checkpoint_path = str(Path(directory) / "checkpoint.db")
                async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
                    await saver.setup()
                    graph = build_graph(saver)
                    state = await graph.ainvoke(
                        create_turn_state(
                            message="制度是什么？",
                            user_id="user-1",
                            session_id="session-1",
                            trace_id="trace-1",
                        ),
                        config={
                            "configurable": {
                                "thread_id": thread_id_for("user-1", "session-1")
                            }
                        },
                    )
                    checkpoints = [
                        item
                        async for item in saver.alist(
                            {
                                "configurable": {
                                    "thread_id": thread_id_for("user-1", "session-1")
                                }
                            }
                        )
                    ]

        messages = state["messages"]
        self.assertEqual(state["answer"], "好候选 制度文本 [C1]")
        self.assertEqual(state["candidate_answer"], "")
        self.assertEqual(state["answer_disposition"], "accepted")
        self.assertEqual(
            [message.content for message in messages if message.type == "ai"],
            ["好候选 制度文本 [C1]"],
        )
        self.assertNotIn("坏候选", "\n".join(str(message.content) for message in messages))
        self.assertEqual(state["model_call_count"], 6)
        self.assertEqual(
            [attempt["passed"] for attempt in state["attempt_history"]],
            [False, True],
        )
        self.assertTrue(checkpoints)
        self.assertTrue(
            all(
                not (
                    set(checkpoint.checkpoint.get("channel_values", {}))
                    & {"candidate_answer", "candidate_citations", "generation_instruction"}
                )
                for checkpoint in checkpoints
            )
        )


class ApiAuthoritativeOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_and_sqlite_only_receive_committed_answer(self) -> None:
        final_state = {
            "candidate_answer": "",
            "answer": "正式答案",
            "candidate_citations": [],
            "citations": [],
            "answer_disposition": "accepted",
            "failure_stage": None,
            "failure_reason": None,
            "request_call_count": 1,
            "model_call_count": 1,
            "tool_call_count": 0,
            "total_latency_ms": 1.0,
        }

        class FakeGraph:
            async def astream_events(self, graph_input, *, config, version, context=None):
                yield {
                    "event": "on_chain_start",
                    "name": "generate",
                    "metadata": {"langgraph_node": "generate"},
                }
                yield {
                    "event": "on_chat_model_stream",
                    "name": "ChatOpenAI",
                    "data": {"chunk": AIMessageChunk(content="失败草稿")},
                    "metadata": {"langgraph_node": "generate"},
                }
                yield {
                    "event": "on_chain_end",
                    "name": "generate",
                    "metadata": {"langgraph_node": "generate"},
                    "data": {"output": {"candidate_answer": "失败草稿"}},
                }
                yield {
                    "event": "on_chain_start",
                    "name": "commit_answer",
                    "metadata": {"langgraph_node": "commit_answer"},
                }
                yield {
                    "event": "on_chain_end",
                    "name": "commit_answer",
                    "metadata": {"langgraph_node": "commit_answer"},
                    "data": {"output": final_state},
                }

        async def disconnected() -> bool:
            return False

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with patch.object(settings, "sqlite_db_path", str(database)), patch.object(
                settings, "dashscope_api_key", ""
            ):
                await ensure_user_memory_db(database)
                request = SimpleNamespace(
                    app=SimpleNamespace(state=SimpleNamespace(graph=FakeGraph())),
                    is_disconnected=disconnected,
                )
                payload = ChatRequest(
                    message="问题",
                    user_id="u",
                    session_id="s",
                    turn_id="api-replay-turn",
                )
                events = [event async for event in _stream_graph_unlocked(request, payload)]
                replay_events = [
                    event async for event in _stream_graph_unlocked(request, payload)
                ]
                history = await load_chat_messages("u", "s")

        rendered = "".join(events)
        self.assertIn("正式答案", rendered)
        self.assertIn("正式答案", "".join(replay_events))
        self.assertNotIn("失败草稿", rendered)
        self.assertEqual([item["content"] for item in history], ["问题", "正式答案"])
        self.assertTrue(history[0]["message_id"].endswith(":user"))
        self.assertTrue(history[1]["message_id"].endswith(":assistant"))

    async def test_replaying_same_turn_returns_the_original_result_without_rerunning_graph(self) -> None:
        class ReplayGraph:
            def __init__(self) -> None:
                self.calls = 0

            async def astream_events(self, graph_input, *, config, version, context=None):
                self.calls += 1
                answer = f"答案{self.calls} [C1]"
                final_state = {
                    "candidate_answer": "",
                    "answer": answer,
                    "candidate_citations": [],
                    "citations": [{"citation_id": "C1", "quote": "证据"}],
                    "answer_disposition": "accepted",
                    "hallucination_pass": True,
                    "failure_stage": None,
                    "failure_reason": None,
                    "request_call_count": 1,
                    "model_call_count": 1,
                    "tool_call_count": 0,
                    "total_latency_ms": 1.0,
                }
                yield {
                    "event": "on_chain_end",
                    "name": "commit_answer",
                    "metadata": {"langgraph_node": "commit_answer"},
                    "data": {"output": final_state},
                }

        graph = ReplayGraph()
        async def disconnected() -> bool:
            return False

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with patch.object(settings, "sqlite_db_path", str(database)), patch.object(
                settings, "dashscope_api_key", ""
            ):
                await ensure_user_memory_db(database)
                request = SimpleNamespace(
                    app=SimpleNamespace(state=SimpleNamespace(graph=graph)),
                    is_disconnected=disconnected,
                )
                payload = ChatRequest(
                    message="问题",
                    user_id="replay-user",
                    session_id="replay-session",
                    turn_id="stable-replay-turn",
                )
                first_events = [
                    event async for event in _stream_graph_unlocked(request, payload)
                ]
                replay_events = [
                    event async for event in _stream_graph_unlocked(request, payload)
                ]

        self.assertEqual(graph.calls, 1)
        self.assertIn("答案1 [C1]", "".join(first_events))
        self.assertIn("答案1 [C1]", "".join(replay_events))
        self.assertNotIn("答案2 [C1]", "".join(replay_events))
        self.assertIn('"replayed": true', "".join(replay_events))


class RetryBoundaryTests(unittest.TestCase):
    def test_zero_retry_falls_back_after_first_failed_validation(self) -> None:
        with patch("backend.agent.edges.settings.max_hallucination_retries", 0):
            self.assertEqual(
                route_after_hallucination_check(
                    {"hallucination_pass": False, "hallucination_retry_count": 1}
                ),
                "fallback_answer",
            )

    def test_one_retry_allows_only_one_retry(self) -> None:
        with patch("backend.agent.edges.settings.max_hallucination_retries", 1):
            self.assertEqual(
                route_after_hallucination_check(
                    {"hallucination_pass": False, "hallucination_retry_count": 1}
                ),
                "generate",
            )
            self.assertEqual(
                route_after_hallucination_check(
                    {"hallucination_pass": False, "hallucination_retry_count": 2}
                ),
                "fallback_answer",
            )


class PersistenceAndMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_turn_is_first_writer_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with patch("backend.agent.memory.settings.sqlite_db_path", str(database)):
                await ensure_user_memory_db(database)
                first = await persist_completed_turn(
                    "u",
                    "s",
                    "turn",
                    "question",
                    "first answer",
                    [{"citation_id": "C1"}],
                )
                second = await persist_completed_turn(
                    "u",
                    "s",
                    "turn",
                    "question",
                    "second answer",
                    [{"citation_id": "C2"}],
                )
                loaded = await load_completed_turn("u", "s", "turn")

        self.assertEqual(first["content"], "first answer")
        self.assertEqual(second["content"], "first answer")
        self.assertEqual(loaded["content"], "first answer")
        self.assertEqual(loaded["citations"], [{"citation_id": "C1"}])

    async def test_sqlite_message_identity_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with patch("backend.agent.memory.settings.sqlite_db_path", str(database)):
                await ensure_user_memory_db(database)
                await append_chat_message(
                    "u", "s", "assistant", "first", turn_id="t", message_id="t:assistant"
                )
                await append_chat_message(
                    "u", "s", "assistant", "updated", turn_id="t", message_id="t:assistant"
                )
                history = await load_chat_messages("u", "s")

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "updated")
        self.assertEqual(history[0]["message_id"], "t:assistant")

    def test_request_call_count_is_derived_from_model_and_tool_counts(self) -> None:
        result = record_call_stats(
            {"model_call_count": 2, "tool_call_count": 1, "total_latency_ms": 4},
            model_calls=1,
            tool_calls=1,
            elapsed_ms=6,
        )

        self.assertEqual(result["request_call_count"], 5)
        self.assertEqual(result["model_call_count"], 3)
        self.assertEqual(result["tool_call_count"], 2)
        self.assertEqual(result["total_latency_ms"], 10)

    def test_trace_exposes_internal_failure_and_call_statistics(self) -> None:
        trace = build_trace(
            trace_id="trace",
            query="q",
            state={
                "turn_id": "turn",
                "answer": "fallback",
                "failure_stage": "citation",
                "failure_reason": "invalid marker",
                "request_call_count": 4,
                "model_call_count": 3,
                "tool_call_count": 1,
                "total_latency_ms": 12.5,
            },
        )

        self.assertEqual(trace["failure_type"], "citation_error")
        self.assertEqual(trace["failure_stage"], "citation")
        self.assertEqual(trace["request_call_count"], 4)

    def test_trace_preserves_validation_attempt_history(self) -> None:
        trace = build_trace(
            trace_id="trace-attempts",
            query="q",
            state={
                "answer": "fallback",
                "attempt_history": [
                    {"stage": "hallucination", "passed": False, "reason": "unsupported"},
                    {"stage": "hallucination", "passed": True, "reason": ""},
                ],
            },
        )

        self.assertEqual(
            [item["passed"] for item in trace["attempt_history"]], [False, True]
        )

    async def test_user_and_assistant_history_share_turn_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with patch("backend.agent.memory.settings.sqlite_db_path", str(database)):
                await ensure_user_memory_db(database)
                await append_chat_message(
                    "u", "s", "user", "question", turn_id="t", message_id="t:user"
                )
                await append_chat_message(
                    "u", "s", "assistant", "answer", turn_id="t", message_id="t:assistant"
                )
                history = await load_chat_messages("u", "s")

        self.assertEqual([item["turn_id"] for item in history], ["t", "t"])
        self.assertEqual(
            [item["message_id"] for item in history], ["t:user", "t:assistant"]
        )

    def test_turn_id_can_be_reused_when_a_request_is_replayed(self) -> None:
        first = create_turn_state(
            message="问题",
            user_id="u",
            session_id="s",
            trace_id="trace-1",
            turn_id="replay-turn",
        )
        replay = create_turn_state(
            message="问题",
            user_id="u",
            session_id="s",
            trace_id="trace-2",
            turn_id="replay-turn",
        )

        self.assertEqual(first["turn_id"], replay["turn_id"])
        self.assertEqual(first["messages"][0].id, replay["messages"][0].id)


if __name__ == "__main__":
    unittest.main()
