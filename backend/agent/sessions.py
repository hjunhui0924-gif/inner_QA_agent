"""Session identity and complete lifecycle operations."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage

from backend.agent.memory import delete_chat_session


class AsyncThreadCheckpointer(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


@dataclass
class _SessionLockEntry:
    lock: asyncio.Lock
    users: int = 0


_SESSION_LOCKS: dict[str, _SessionLockEntry] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


@asynccontextmanager
async def session_operation(thread_id: str) -> AsyncIterator[None]:
    """Serialize stream and delete operations for one session in this process."""

    with _SESSION_LOCKS_GUARD:
        entry = _SESSION_LOCKS.setdefault(
            thread_id,
            _SessionLockEntry(lock=asyncio.Lock()),
        )
        entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        with _SESSION_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _SESSION_LOCKS.pop(thread_id, None)


def thread_id_for(user_id: str, session_id: str) -> str:
    """Return the existing LangGraph thread identity for one chat session."""

    return f"{user_id}_{session_id}"


def create_turn_state(
    *,
    message: str,
    user_id: str,
    session_id: str,
    trace_id: str,
    mode: str = "knowledge",
    web_search: bool = False,
) -> dict[str, Any]:
    """Create one turn input while preserving only checkpointed conversation memory."""

    return {
        "messages": [HumanMessage(content=message)],
        "query": message,
        "user_id": user_id,
        "session_id": session_id,
        "trace_id": trace_id,
        "mode": mode if mode in {"knowledge", "general"} else "knowledge",
        "web_search": web_search,
        "rewritten_query": "",
        "retrieved_docs": [],
        "answer": "",
        "generation_error": "",
        "fallback_reason": "",
        "citations": [],
        "hallucination_pass": False,
        "tool_output": "",
        "retrieval_retry_count": 0,
        "hallucination_retry_count": 0,
        "status_events": [],
    }


async def delete_session_completely(
    *,
    user_id: str,
    session_id: str,
    checkpointer: AsyncThreadCheckpointer,
) -> None:
    """Idempotently delete graph state before removing visible chat history."""

    thread_id = thread_id_for(user_id, session_id)
    async with session_operation(thread_id):
        await checkpointer.adelete_thread(thread_id)
        await delete_chat_session(user_id, session_id)
