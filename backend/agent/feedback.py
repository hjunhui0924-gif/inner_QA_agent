"""Persistence helpers for answer feedback."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from backend.config import settings


async def ensure_feedback_table(db_path: str | None = None) -> None:
    async with aiosqlite.connect(db_path or settings.sqlite_db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS answer_feedback (
                user_id TEXT NOT NULL,
                feedback_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                category TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, feedback_id)
            )
            """
        )
        await db.commit()


async def find_feedback(user_id: str, feedback_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        cursor = await db.execute(
            "SELECT session_id, turn_id, message_id, category, note FROM answer_feedback WHERE user_id=? AND feedback_id=?",
            (user_id, feedback_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
    if row is None:
        return None
    return {"session_id": row[0], "turn_id": row[1], "message_id": row[2], "category": row[3], "note": row[4]}


async def insert_feedback(user_id: str, feedback_id: str, payload: dict[str, str]) -> None:
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO answer_feedback (user_id, feedback_id, session_id, turn_id, message_id, category, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, feedback_id, payload["session_id"], payload["turn_id"], payload["message_id"], payload["category"], payload["note"]),
        )
        await db.commit()
