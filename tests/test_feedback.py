import asyncio
from pathlib import Path

from backend.agent import feedback


def test_feedback_insert_and_idempotent_lookup(tmp_path: Path, monkeypatch):
    database = tmp_path / "feedback.db"
    monkeypatch.setattr(feedback.settings, "sqlite_db_path", str(database))

    async def scenario():
        await feedback.ensure_feedback_table()
        payload = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "message_id": "turn-1:assistant",
            "category": "not_helpful",
            "note": "请补充审批条件",
        }
        await feedback.insert_feedback("alice", "feedback-1", payload)
        assert await feedback.find_feedback("alice", "feedback-1") == payload
        assert await feedback.find_feedback("bob", "feedback-1") is None

    asyncio.run(scenario())
