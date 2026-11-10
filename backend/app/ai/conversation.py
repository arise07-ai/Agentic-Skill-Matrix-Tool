"""Helpers for bounded, client-held conversation history."""

import re
from typing import Any, Literal

MAX_CONVERSATION_MESSAGES = 12
MAX_CONVERSATION_MESSAGE_CHARS = 3000
MAX_CONTEXTUAL_QUESTION_CHARS = 18000
_CLOSING_MESSAGE = re.compile(
    r"^(?:(?:no|nope|nah)\s+)?"
    r"(?:nothing\s+(?:else|more)|thats\s+all|all\s+for\s+now|no\s+more\s+questions)"
    r"(?:\s+(?:thanks|thank\s+you))?$"
)
_THANKS_MESSAGE = re.compile(r"^(?:thanks|thank\s+you|okay\s+thanks|ok\s+thanks|thats\s+all)$")


def normalize_messages(messages: list[Any], limit: int = MAX_CONVERSATION_MESSAGES) -> list[dict[str, str]]:
    """Keep the most recent valid user/assistant turns in a consistent, bounded shape."""
    normalized = []
    for message in messages:
        if hasattr(message, "model_dump"):
            message = message.model_dump()
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        content = str(message.get("content") or "").strip()
        if content:
            normalized.append({
                "role": message["role"],
                "content": content[:MAX_CONVERSATION_MESSAGE_CHARS],
            })
    return normalized[-limit:]


def contextual_question(messages: list[Any]) -> str:
    """Render the latest request with recent dialogue so evaluation retains follow-up intent."""
    history = normalize_messages(messages)
    if not history:
        return ""
    if len(history) == 1:
        return history[0]["content"]

    transcript = "\n".join(
        f"{'User' if message['role'] == 'user' else 'Assistant'}: {message['content']}"
        for message in history
    )
    question = f"Conversation context and latest request:\n{transcript}"
    return question[-MAX_CONTEXTUAL_QUESTION_CHARS:]


def cached_conversational_reply(text: str, audience: Literal["chat", "learning_coach"]) -> str | None:
    """Return a fast, fixed reply for clear conversation closings; never intercept questions."""
    normalized = text.casefold().replace("that's", "thats").replace("that’s", "thats")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = " ".join(normalized.split())
    if not (_CLOSING_MESSAGE.fullmatch(normalized) or _THANKS_MESSAGE.fullmatch(normalized)):
        return None
    if audience == "learning_coach":
        return "You’re welcome! I’m here if you’d like more help with your learning plan."
    return "You’re welcome! I’m here if you have another question about the skill matrix."
