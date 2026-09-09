"""Checkpoint adapters for keeping request-local drafts out of persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    RunnableConfig,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


# These values are needed by the running graph while a turn is in flight, but
# an interrupted run must never make them look like durable conversation state.
TRANSIENT_CHECKPOINT_CHANNELS = frozenset(
    {
        "candidate_answer",
        "candidate_citations",
        "generation_instruction",
    }
)


def _without_transient_channels(checkpoint: Checkpoint) -> Checkpoint:
    """Copy a checkpoint while removing request-local answer drafts."""

    sanitized = dict(checkpoint)
    channel_values = checkpoint.get("channel_values", {})
    if isinstance(channel_values, dict):
        sanitized["channel_values"] = {
            key: value
            for key, value in channel_values.items()
            if key not in TRANSIENT_CHECKPOINT_CHANNELS
        }
    return sanitized  # type: ignore[return-value]


def _without_transient_writes(
    writes: Sequence[tuple[str, Any]],
) -> list[tuple[str, Any]]:
    """Remove transient channel writes before the saver serializes them."""

    return [
        (channel, value)
        for channel, value in writes
        if channel not in TRANSIENT_CHECKPOINT_CHANNELS
    ]


class TransientStateFilteringAsyncSqliteSaver(AsyncSqliteSaver):
    """Async SQLite saver that persists only durable graph channels.

    LangGraph continues to use the in-memory channel values for the current
    execution, so retries can read a candidate normally.  Only the serialized
    checkpoint and pending writes are filtered; a crash before ``commit_answer``
    therefore resumes without an unvalidated draft.
    """

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await super().aput(
            config,
            _without_transient_channels(checkpoint),
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        filtered = _without_transient_writes(writes)
        if not filtered:
            return
        await super().aput_writes(config, filtered, task_id, task_path)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return super().put(
            config,
            _without_transient_channels(checkpoint),
            metadata,
            new_versions,
        )

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        filtered = _without_transient_writes(writes)
        if not filtered:
            return
        super().put_writes(config, filtered, task_id, task_path)
