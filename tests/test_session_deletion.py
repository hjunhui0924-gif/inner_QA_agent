"""Behavior tests for deleting all state owned by one chat session."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.agent.memory import (
    append_chat_message,
    ensure_user_memory_db,
    list_chat_sessions,
    load_chat_messages,
)
from backend.agent.sessions import (
    create_turn_state,
    delete_session_completely,
    session_operation,
    thread_id_for,
)
from backend.api.routes import delete_session


class CompleteSessionDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_removes_history_and_langgraph_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            checkpointer = AsyncMock()
            with patch("backend.agent.memory.settings.sqlite_db_path", str(database)):
                await ensure_user_memory_db(database)
                await append_chat_message("user-1", "session-1", "user", "hello")

                await delete_session_completely(
                    user_id="user-1",
                    session_id="session-1",
                    checkpointer=checkpointer,
                )

                self.assertEqual(
                    await load_chat_messages("user-1", "session-1"),
                    [],
                )
                self.assertEqual(await list_chat_sessions("user-1"), [])

            checkpointer.adelete_thread.assert_awaited_once_with(
                thread_id_for("user-1", "session-1")
            )

    async def test_delete_endpoint_uses_application_checkpointer(self) -> None:
        checkpointer = AsyncMock()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(checkpointer=checkpointer))
        )

        with patch(
            "backend.api.routes.delete_session_completely",
            new_callable=AsyncMock,
        ) as complete_delete:
            response = await delete_session(
                request=request,
                user_id="user-1",
                session_id="session-1",
            )

        self.assertEqual(response, {"message": "会话已删除。"})
        complete_delete.assert_awaited_once_with(
            user_id="user-1",
            session_id="session-1",
            checkpointer=checkpointer,
        )

    async def test_real_checkpoint_is_absent_after_complete_delete(self) -> None:
        class TinyState(TypedDict):
            value: str

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with patch("backend.agent.memory.settings.sqlite_db_path", str(database)):
                await ensure_user_memory_db(database)
                await append_chat_message("user-1", "session-1", "user", "hello")
                async with AsyncSqliteSaver.from_conn_string(str(database)) as saver:
                    await saver.setup()
                    builder = StateGraph(TinyState)
                    builder.add_node("finish", lambda state: state)
                    builder.add_edge(START, "finish")
                    builder.add_edge("finish", END)
                    graph = builder.compile(checkpointer=saver)
                    config = {
                        "configurable": {
                            "thread_id": thread_id_for("user-1", "session-1")
                        }
                    }
                    await graph.ainvoke({"value": "remember me"}, config=config)
                    self.assertIsNotNone(await saver.aget_tuple(config))

                    await delete_session_completely(
                        user_id="user-1",
                        session_id="session-1",
                        checkpointer=saver,
                    )

                    self.assertIsNone(await saver.aget_tuple(config))
                self.assertEqual(
                    await load_chat_messages("user-1", "session-1"),
                    [],
                )


class TurnIsolationTests(unittest.TestCase):
    def test_new_turn_explicitly_clears_transient_previous_turn_state(self) -> None:
        state = create_turn_state(
            message="new question",
            user_id="user-1",
            session_id="session-1",
            trace_id="trace-1",
        )

        self.assertEqual(state["rewritten_query"], "")
        self.assertEqual(state["retrieved_docs"], [])
        self.assertEqual(state["tool_output"], "")
        self.assertEqual(state["answer"], "")
        self.assertEqual(state["citations"], [])
        self.assertEqual(state["generation_error"], "")
        self.assertNotIn("conversation_summary", state)


class SessionOperationLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_session_operations_are_serialized(self) -> None:
        import asyncio

        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()

        async def first_operation() -> None:
            async with session_operation("user-1_session-1"):
                first_entered.set()
                await release_first.wait()

        async def second_operation() -> None:
            await first_entered.wait()
            async with session_operation("user-1_session-1"):
                second_entered.set()

        first_task = asyncio.create_task(first_operation())
        second_task = asyncio.create_task(second_operation())
        await first_entered.wait()
        await asyncio.sleep(0)
        self.assertFalse(second_entered.is_set())

        release_first.set()
        await asyncio.gather(first_task, second_task)
        self.assertTrue(second_entered.is_set())

    async def test_delete_waits_for_inflight_session_operation(self) -> None:
        import asyncio

        checkpointer = AsyncMock()
        thread_id = thread_id_for("user-1", "session-1")
        with patch(
            "backend.agent.sessions.delete_chat_session",
            new_callable=AsyncMock,
        ) as delete_history:
            async with session_operation(thread_id):
                delete_task = asyncio.create_task(
                    delete_session_completely(
                        user_id="user-1",
                        session_id="session-1",
                        checkpointer=checkpointer,
                    )
                )
                await asyncio.sleep(0)
                checkpointer.adelete_thread.assert_not_awaited()

            await delete_task

        checkpointer.adelete_thread.assert_awaited_once_with(thread_id)
        delete_history.assert_awaited_once_with("user-1", "session-1")


if __name__ == "__main__":
    unittest.main()
