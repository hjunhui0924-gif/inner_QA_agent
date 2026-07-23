"""Conversation-window planning behind a small, deterministic interface."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from langchain_core.messages import BaseMessage


@dataclass(frozen=True)
class ConversationWindowPlan:
    """Messages to compress and messages that remain verbatim."""

    messages_to_summarize: list[BaseMessage]
    messages_to_keep: list[BaseMessage]


def estimate_text_tokens(text: str) -> int:
    """Return a conservative token estimate for mixed Chinese/ASCII text."""

    estimate = 0
    for token in re.findall(r"[\u3400-\u9fff]|[a-zA-Z0-9_]+|[^\s]", text):
        if re.fullmatch(r"[a-zA-Z0-9_]+", token):
            estimate += math.ceil(len(token) / 3)
        else:
            estimate += 1
    return estimate


def estimate_conversation_tokens(
    messages: list[BaseMessage],
    summary: str = "",
) -> int:
    """Estimate serialized conversation size including per-message overhead."""

    return estimate_text_tokens(summary) + sum(
        estimate_text_tokens(_message_text(message)) + 4 for message in messages
    )


def plan_conversation_window(
    messages: list[BaseMessage],
    *,
    summary: str,
    trigger_tokens: int,
    token_budget: int,
    summary_target_tokens: int,
    recent_turns: int,
) -> ConversationWindowPlan | None:
    """Plan one compaction while preserving current input and recent turns."""

    if min(trigger_tokens, token_budget, summary_target_tokens, recent_turns) <= 0:
        raise ValueError("Conversation limits must be positive.")
    if estimate_conversation_tokens(messages, summary) <= trigger_tokens:
        return None

    human_indices = [
        index
        for index, message in enumerate(messages)
        if getattr(message, "type", "") == "human"
    ]
    if not human_indices:
        return None

    current_is_human = getattr(messages[-1], "type", "") == "human"
    human_messages_to_keep = recent_turns + (1 if current_is_human else 0)
    keep_from = (
        human_indices[-human_messages_to_keep]
        if len(human_indices) > human_messages_to_keep
        else 0
    )
    while (
        keep_from < human_indices[-1]
        and estimate_conversation_tokens(messages[keep_from:])
        + summary_target_tokens
        > token_budget
    ):
        keep_from = next(
            index for index in human_indices if index > keep_from
        )
    messages_to_summarize = messages[:keep_from]
    if not messages_to_summarize:
        return None
    return ConversationWindowPlan(
        messages_to_summarize=messages_to_summarize,
        messages_to_keep=messages[keep_from:],
    )


def render_messages(messages: list[BaseMessage]) -> str:
    """Render conversation messages with explicit roles for prompting."""

    labels = {"human": "用户", "ai": "助手", "system": "系统"}
    return "\n".join(
        f"{labels.get(getattr(message, 'type', ''), '消息')}：{_message_text(message)}"
        for message in messages
    )


def truncate_to_token_budget(
    text: str,
    token_budget: int,
    *,
    keep_recent: bool = True,
) -> str:
    """Keep one end of text within an approximate token budget."""

    if token_budget <= 0:
        raise ValueError("token_budget must be positive.")
    if estimate_text_tokens(text) <= token_budget:
        return text.strip()
    low, high = 0, len(text)
    while low < high:
        middle = (low + high) // 2
        candidate = text[len(text) - middle :] if keep_recent else text[:middle]
        if estimate_text_tokens(candidate) <= token_budget:
            low = middle + 1
        else:
            high = middle
    keep = max(1, low - 1)
    return (text[len(text) - keep :] if keep_recent else text[:keep]).strip()


def merge_fallback_summary(
    existing_summary: str,
    old_conversation: str,
    token_budget: int,
) -> str:
    """Preserve prior summary first, then add the most recent old dialogue."""

    if not existing_summary.strip():
        return truncate_to_token_budget(old_conversation, token_budget)
    existing_budget = max(1, token_budget * 2 // 3)
    existing_part = truncate_to_token_budget(
        existing_summary,
        existing_budget,
        keep_recent=False,
    )
    remaining = max(1, token_budget - estimate_text_tokens(existing_part))
    old_part = truncate_to_token_budget(old_conversation, remaining)
    combined = "\n".join(part for part in [existing_part, old_part] if part)
    if estimate_text_tokens(combined) <= token_budget:
        return combined
    # Token estimates are additive except at run boundaries; trim only the new part.
    overflow = estimate_text_tokens(combined) - token_budget
    old_budget = max(1, estimate_text_tokens(old_part) - overflow)
    old_part = truncate_to_token_budget(old_part, old_budget)
    return "\n".join(part for part in [existing_part, old_part] if part)


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)
