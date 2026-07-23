"""Behavior tests for bounded, summarized conversation state."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agent.nodes import (
    generate,
    manage_conversation_context,
    rewrite_query,
    route_query,
)
from backend.agent.conversation import estimate_text_tokens
from backend.agent.graph import build_graph
from backend.agent.sessions import create_turn_state, thread_id_for


class ConversationCompressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_turns_are_summarized_while_recent_turns_are_retained(self) -> None:
        messages = add_messages(
            [],
            [
                HumanMessage(content="old question " * 20),
                AIMessage(content="old answer " * 20),
                HumanMessage(content="recent question"),
                AIMessage(content="recent answer"),
                HumanMessage(content="current follow-up"),
            ],
        )
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content="The user previously discussed the old policy."
        )

        with (
            patch("backend.agent.nodes._build_model", return_value=model) as builder,
            patch("backend.agent.nodes.settings.conversation_summary_trigger_tokens", 1),
            patch("backend.agent.nodes.settings.conversation_recent_turns", 1),
        ):
            result = await manage_conversation_context(
                {"messages": messages, "conversation_summary": ""}
            )

        self.assertEqual(
            result["conversation_summary"],
            "The user previously discussed the old policy.",
        )
        removed_ids = {message.id for message in result["messages"]}
        self.assertEqual(removed_ids, {messages[0].id, messages[1].id})
        model.ainvoke.assert_awaited_once()
        builder.assert_called_once_with(
            temperature=0,
            max_tokens=1500,
        )

    async def test_generated_summary_is_bounded_by_target_budget(self) -> None:
        messages = add_messages(
            [],
            [
                HumanMessage(content="old question " * 20),
                AIMessage(content="old answer " * 20),
                HumanMessage(content="recent question"),
                AIMessage(content="recent answer"),
                HumanMessage(content="current follow-up"),
            ],
        )
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content="oversized summary " * 100)

        with (
            patch("backend.agent.nodes._build_model", return_value=model),
            patch("backend.agent.nodes.settings.conversation_summary_trigger_tokens", 1),
            patch("backend.agent.nodes.settings.conversation_summary_target_tokens", 20),
            patch("backend.agent.nodes.settings.conversation_recent_turns", 1),
        ):
            result = await manage_conversation_context({"messages": messages})

        self.assertLessEqual(
            estimate_text_tokens(result["conversation_summary"]),
            20,
        )

    async def test_long_recent_turns_are_compacted_to_respect_total_budget(self) -> None:
        messages = add_messages(
            [],
            [
                HumanMessage(content="first long question " * 20),
                AIMessage(content="first long answer " * 20),
                HumanMessage(content="second long question " * 20),
                AIMessage(content="second long answer " * 20),
                HumanMessage(content="current short follow-up"),
            ],
        )
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content="short summary")

        with (
            patch("backend.agent.nodes._build_model", return_value=model),
            patch("backend.agent.nodes.settings.conversation_token_budget", 20),
            patch("backend.agent.nodes.settings.conversation_summary_trigger_tokens", 10),
            patch("backend.agent.nodes.settings.conversation_summary_target_tokens", 5),
            patch("backend.agent.nodes.settings.conversation_recent_turns", 4),
        ):
            result = await manage_conversation_context({"messages": messages})

        removed_ids = {message.id for message in result["messages"]}
        self.assertEqual(removed_ids, {message.id for message in messages[:-1]})

    async def test_fallback_summary_preserves_existing_summary_and_old_dialogue(self) -> None:
        messages = add_messages(
            [],
            [
                HumanMessage(content="new old question " * 20),
                AIMessage(content="new old answer " * 20),
                HumanMessage(content="recent question"),
                AIMessage(content="recent answer"),
                HumanMessage(content="current follow-up"),
            ],
        )
        with (
            patch("backend.agent.nodes._build_model", side_effect=RuntimeError("offline")),
            patch("backend.agent.nodes.settings.conversation_summary_trigger_tokens", 1),
            patch("backend.agent.nodes.settings.conversation_summary_target_tokens", 40),
            patch("backend.agent.nodes.settings.conversation_recent_turns", 1),
        ):
            result = await manage_conversation_context(
                {
                    "messages": messages,
                    "conversation_summary": "ESSENTIAL_OLDER_CONTEXT",
                }
            )

        summary = result["conversation_summary"]
        self.assertIn("ESSENTIAL_OLDER_CONTEXT", summary)
        self.assertIn("old answer", summary)
        self.assertLessEqual(estimate_text_tokens(summary), 40)


class ContextualRewriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_routes_to_rag_using_conversation_context(self) -> None:
        model = AsyncMock()

        async def route_from_prompt(prompt_messages: list[object]) -> AIMessage:
            prompt = str(getattr(prompt_messages[0], "content", ""))
            route = "rag" if "差旅报销制度" in prompt else "direct"
            return AIMessage(content=f'{{"route":"{route}","reason":"context"}}')

        model.ainvoke.side_effect = route_from_prompt
        with patch("backend.agent.nodes._build_model", return_value=model):
            result = await route_query(
                {
                    "query": "超过5000元呢？",
                    "conversation_summary": "用户正在了解差旅报销制度。",
                    "messages": [HumanMessage(content="超过5000元呢？")],
                }
            )

        self.assertEqual(result["route"], "rag")

    async def test_follow_up_is_rewritten_using_conversation_context(self) -> None:
        model = AsyncMock()

        async def answer_from_prompt(prompt_messages: list[object]) -> AIMessage:
            prompt = str(getattr(prompt_messages[0], "content", ""))
            if "差旅报销制度" in prompt and "超过5000元呢" in prompt:
                return AIMessage(
                    content=(
                        '{"rewritten_query":"差旅报销金额超过5000元时的审批流程"}'
                    )
                )
            return AIMessage(content='{"rewritten_query":"超过5000元"}')

        model.ainvoke.side_effect = answer_from_prompt
        messages = add_messages(
            [],
            [
                HumanMessage(content="公司的差旅报销制度是什么？"),
                AIMessage(content="差旅费用需要按差旅报销制度审批。"),
                HumanMessage(content="超过5000元呢？"),
            ],
        )

        with patch("backend.agent.nodes._build_model", return_value=model):
            result = await rewrite_query(
                {
                    "query": "超过5000元呢？",
                    "conversation_summary": "用户正在了解差旅报销制度。",
                    "messages": messages,
                }
            )

        self.assertEqual(
            result["rewritten_query"],
            "差旅报销金额超过5000元时的审批流程",
        )

    async def test_keyword_only_model_rewrite_falls_back_to_contextual_query(self) -> None:
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content='{"rewritten_query":"5000"}'
        )
        messages = add_messages(
            [],
            [
                HumanMessage(content="公司的差旅报销制度是什么？"),
                AIMessage(content="差旅费用需要按差旅报销制度审批。"),
                HumanMessage(content="超过5000元呢？"),
            ],
        )

        with patch("backend.agent.nodes._build_model", return_value=model):
            result = await rewrite_query(
                {
                    "query": "超过5000元呢？",
                    "conversation_summary": "用户正在了解差旅报销制度。",
                    "messages": messages,
                }
            )

        self.assertIn("差旅报销", result["rewritten_query"])
        self.assertIn("5000", result["rewritten_query"])

    async def test_multi_turn_rewrite_keeps_topic_not_just_prior_amount(self) -> None:
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content='{"rewritten_query":"5000"}'
        )
        messages = add_messages(
            [],
            [
                HumanMessage(content="公司的差旅报销制度是什么？"),
                AIMessage(content="正在讨论差旅报销制度。"),
                HumanMessage(content="超过5000元呢？"),
                AIMessage(content="需要继续查询审批要求。"),
                HumanMessage(content="那审批人呢？"),
            ],
        )

        with patch("backend.agent.nodes._build_model", return_value=model):
            result = await rewrite_query(
                {
                    "query": "那审批人呢？",
                    "conversation_summary": "用户正在了解差旅报销制度。",
                    "messages": messages,
                }
            )

        self.assertIn("差旅报销", result["rewritten_query"])
        self.assertIn("审批人", result["rewritten_query"])


class SummaryConsumptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_receives_compacted_conversation_summary(self) -> None:
        class RecordingModel:
            def __init__(self) -> None:
                self.prompt_messages: list[object] = []

            async def astream(self, prompt_messages: list[object]):
                self.prompt_messages = prompt_messages
                yield AIMessageChunk(content="answer")

        model = RecordingModel()
        with patch("backend.agent.nodes._build_model", return_value=model):
            await generate(
                {
                    "route": "direct",
                    "query": "继续",
                    "conversation_summary": "用户正在比较差旅报销制度。",
                    "messages": [HumanMessage(content="继续")],
                }
            )

        system_prompt = str(getattr(model.prompt_messages[0], "content", ""))
        self.assertIn("用户正在比较差旅报销制度", system_prompt)


class ConversationGraphTests(unittest.TestCase):
    def test_context_management_runs_before_query_routing(self) -> None:
        graph = build_graph(None)
        rendered = graph.get_graph()
        edges = {(edge.source, edge.target) for edge in rendered.edges}

        self.assertIn("manage_conversation_context", graph.nodes)
        self.assertIn(("__start__", "manage_conversation_context"), edges)
        self.assertIn(("manage_conversation_context", "route_query"), edges)


class ConversationGraphIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_reducer_removes_messages_after_summarization(self) -> None:
        class GraphModel:
            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                prompt = str(getattr(prompt_messages[0], "content", ""))
                if "待压缩旧对话" in prompt:
                    return AIMessage(content="old conversation summary")
                return AIMessage(content='{"route":"direct","reason":"test"}')

            async def astream(self, prompt_messages: list[object]):
                yield AIMessageChunk(content="final answer")

        graph = build_graph(None)
        messages = [
            HumanMessage(content="remove this old question " * 20),
            AIMessage(content="remove this old answer " * 20),
            HumanMessage(content="keep this recent question"),
            AIMessage(content="keep this recent answer"),
            HumanMessage(content="current follow-up"),
        ]
        with (
            patch("backend.agent.nodes._build_model", return_value=GraphModel()),
            patch("backend.agent.nodes.settings.conversation_summary_trigger_tokens", 1),
            patch("backend.agent.nodes.settings.conversation_recent_turns", 1),
        ):
            result = await graph.ainvoke(
                {
                    "messages": messages,
                    "query": "current follow-up",
                    "status_events": [],
                }
            )

        self.assertEqual(
            result["conversation_summary"],
            "old conversation summary",
        )
        remaining_text = "\n".join(str(message.content) for message in result["messages"])
        self.assertNotIn("remove this old question", remaining_text)
        self.assertIn("keep this recent question", remaining_text)
        self.assertIn("current follow-up", remaining_text)

    async def test_status_events_do_not_accumulate_across_turns(self) -> None:
        class DirectModel:
            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                return AIMessage(content='{"route":"direct","reason":"test"}')

            async def astream(self, prompt_messages: list[object]):
                yield AIMessageChunk(content="answer")

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "checkpoint.db")
            async with AsyncSqliteSaver.from_conn_string(database) as saver:
                await saver.setup()
                graph = build_graph(saver)
                config = {
                    "configurable": {
                        "thread_id": thread_id_for("user-1", "session-1")
                    }
                }
                with patch("backend.agent.nodes._build_model", return_value=DirectModel()):
                    first = await graph.ainvoke(
                        create_turn_state(
                            message="hello",
                            user_id="user-1",
                            session_id="session-1",
                            trace_id="trace-1",
                        ),
                        config=config,
                    )
                    second = await graph.ainvoke(
                        create_turn_state(
                            message="hello again",
                            user_id="user-1",
                            session_id="session-1",
                            trace_id="trace-2",
                        ),
                        config=config,
                    )

                self.assertLessEqual(
                    len(second["status_events"]),
                    len(first["status_events"]),
                )


if __name__ == "__main__":
    unittest.main()
