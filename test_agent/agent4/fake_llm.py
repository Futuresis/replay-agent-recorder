from __future__ import annotations

import hashlib
import json
from typing import Any

from openai.types.chat.chat_completion import ChatCompletion


_INSTALLED = False
_ORIGINAL_ASYNC_CREATE = None


def install_fake_llm() -> None:
    """Install a deterministic OpenAI-compatible fake for local replay tests."""

    global _INSTALLED
    global _ORIGINAL_ASYNC_CREATE
    if _INSTALLED:
        return

    from openai.resources.chat.completions.completions import AsyncCompletions

    _ORIGINAL_ASYNC_CREATE = AsyncCompletions.create
    AsyncCompletions.create = _fake_async_create
    _INSTALLED = True


def uninstall_fake_llm() -> None:
    global _INSTALLED
    if not _INSTALLED:
        return

    from openai.resources.chat.completions.completions import AsyncCompletions

    AsyncCompletions.create = _ORIGINAL_ASYNC_CREATE
    _INSTALLED = False


async def _fake_async_create(self, *args: Any, **kwargs: Any) -> ChatCompletion:
    messages = kwargs.get("messages") or []
    payload = _last_user_payload(messages)
    phase = str(payload.get("phase") or "chat")
    label = str(payload.get("label") or payload.get("branch") or payload.get("probe") or "root")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    content = f"fake-agent4::{phase}::{label}::{digest}"

    return ChatCompletion.model_validate(
        {
            "id": f"fake-agent4-{digest}",
            "object": "chat.completion",
            "created": 0,
            "model": kwargs.get("model", "agent4-fake-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


def _last_user_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return {"text": content}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}

