"""Title lifecycle regressions using isolated SQLite and a blocked provider."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from backend.agent.memory import (
    ensure_user_memory_db,
    append_chat_message,
    first_session_question,
    list_chat_sessions,
    delete_chat_session,
    update_title_if_first,
)
from backend.agent.title_tasks import TitleTaskManager, TitleJob
from backend.api.routes import chat_sessions


class TitleTasksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.dbpatch = patch(
            "backend.agent.memory.settings.sqlite_db_path",
            str(Path(self.directory.name) / "test.db"),
        )
        self.dbpatch.start()
        await ensure_user_memory_db(Path(self.directory.name) / "test.db")
        self.manager = TitleTaskManager(concurrency=1, capacity=1, timeout=0.1)
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

        async def invoke(*args):
            self.started.set()
            await self.gate.wait()
            return SimpleNamespace(content="摘要")

        self.model = patch(
            "backend.agent.title_tasks.ChatOpenAI",
            return_value=SimpleNamespace(ainvoke=invoke),
        )
        self.model.start()

    async def asyncTearDown(self):
        await self.manager.close()
        self.model.stop()
        self.dbpatch.stop()
        self.directory.cleanup()

    async def job(self, sid="s"):
        await append_chat_message("u", sid, "user", "首问", message_id=sid + ":first")
        first = await first_session_question("u", sid)
        return TitleJob("u", sid, first["first_id"], first["question"])

    async def drain(self):
        while self.manager.running:
            await asyncio.gather(*list(self.manager.running))
            await asyncio.sleep(0)

    async def test_dedup_capacity_timeout_cooldown_and_shutdown(self):
        a, b, c = await self.job("a"), await self.job("b"), await self.job("c")
        self.assertTrue(self.manager.submit(a))
        self.assertFalse(self.manager.submit(a))
        self.assertTrue(self.manager.submit(b))
        self.assertFalse(self.manager.submit(c))
        await self.drain()
        self.assertFalse(self.manager.submit(a))
        self.assertEqual(
            [m["outcome"] for m in self.manager.metrics], ["failed", "failed"]
        )
        self.assertTrue(self.manager.submit(c))
        await self.manager.close()
        self.assertFalse(self.manager.submit(c))
        self.assertFalse(self.manager.running)

    async def test_delete_during_generation_cannot_revive_session(self):
        job = await self.job()
        self.manager.submit(job)
        await self.started.wait()
        await delete_chat_session("u", "s")
        self.gate.set()
        await self.drain()
        self.assertEqual(await list_chat_sessions("u"), [])

    async def test_first_question_identity_survives_followup_and_rejects_recreated_session(
        self,
    ):
        old = await self.job()
        await append_chat_message("u", "s", "user", "追问")
        self.assertTrue(await update_title_if_first("u", "s", old.first_id, "首问摘要"))
        await delete_chat_session("u", "s")
        new = await self.job()
        self.assertNotEqual(old.first_id, new.first_id)
        self.assertFalse(await update_title_if_first("u", "s", old.first_id, "旧摘要"))

    async def test_success_is_not_repeated_even_when_summary_equals_question(self):
        job = await self.job()
        with patch(
            "backend.agent.title_tasks.ChatOpenAI",
            return_value=SimpleNamespace(
                ainvoke=AsyncMock(return_value=SimpleNamespace(content="首问"))
            ),
        ):
            self.manager.submit(job)
            await self.drain()
        self.assertFalse(self.manager.submit(job))
        self.assertEqual(self.manager.metrics[-1]["budget"]["model_calls"], 1)

    async def test_list_returns_while_title_provider_is_blocked(self):
        await self.job()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(title_tasks=self.manager))
        )
        with patch("backend.api.routes.settings.dashscope_api_key", "test"):
            result = await asyncio.wait_for(chat_sessions(request, "u"), 0.5)
        self.assertEqual(result["items"][0]["title"], "首问")
        self.assertTrue(self.manager.running)
        self.gate.set()
        await self.drain()

    async def test_title_enqueue_uses_server_identity_not_untrusted_body(self):
        from backend.api.routes import _queue_first_title, ChatRequest
        from backend.auth.access import development_access_context
        from unittest.mock import Mock

        manager = Mock()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(title_tasks=manager)),
            state=SimpleNamespace(access_context=development_access_context()),
        )
        first = AsyncMock(return_value={"first_id": 1, "question": "question"})
        with patch("backend.api.routes.first_session_question", first), patch(
            "backend.api.routes.settings.dashscope_api_key", "test"
        ):
            await _queue_first_title(
                request,
                ChatRequest(message="question", user_id="victim", session_id="shared"),
            )
        first.assert_awaited_once_with(development_access_context().user_id, "shared")
        self.assertEqual(
            manager.submit.call_args.args[0].user_id,
            development_access_context().user_id,
        )

    async def test_hanging_title_provider_does_not_delay_result_or_response_end(self):
        import json
        import httpx
        from fastapi import FastAPI
        from backend.api.routes import router

        class Graph:
            async def astream_events(self, state, **kwargs):
                yield {
                    "event": "on_chain_end",
                    "name": "commit_answer",
                    "metadata": {"langgraph_node": "commit_answer"},
                    "data": {"output": {"answer": "正式答案", "citations": []}},
                }

        app = FastAPI()
        app.include_router(router)
        app.state.graph = Graph()
        app.state.title_tasks = self.manager
        self.manager.timeout = 5
        with patch("backend.api.routes.settings.dashscope_api_key", "test"), patch(
            "backend.api.routes.settings.trace_enabled", False
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await asyncio.wait_for(
                    client.post(
                        "/chat/stream",
                        json={
                            "message": "首问",
                            "session_id": "hanging-title",
                            "turn_id": "hanging-turn",
                        },
                    ),
                    timeout=1,
                )
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                self.assertEqual([e["type"] for e in events][-2:], ["result", "done"])
                self.assertEqual(events[-2]["content"], "正式答案")
                self.assertEqual(events[-2]["budget_snapshot"]["model_calls"], 0)
                await self.started.wait()
                self.assertTrue(self.manager.running)
                self.assertFalse(self.gate.is_set())
                self.gate.set()
                await self.drain()
