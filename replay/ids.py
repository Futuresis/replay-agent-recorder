from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import UnsupportedStreamingError
from .normalization import normalize_for_json


RUNTIME_FIELDS = {
    "request_id",
    "trace_id",
    "timestamp",
    "retry_id",
    "timeout",
    "extra_headers",
    "extra_query",
    "idempotency_key",
}

DEFAULT_INPUT_VALUES = {
    "tools": [],
    "temperature": 1,
    "top_p": 1,
    "response_format": None,
    "tool_choice": None,
    "stream": False,
    "n": 1,
}


def build_input_record(
    kwargs: dict[str, Any],
    *,
    provider: str = "openai",
    api: str = "chat.completions.create",
) -> dict[str, Any]:
    if kwargs.get("stream") is True:
        raise UnsupportedStreamingError("stream=True is not supported by this first-stage demo.")

    semantic_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key not in RUNTIME_FIELDS and value is not None
    }

    for key, value in DEFAULT_INPUT_VALUES.items():
        semantic_kwargs.setdefault(key, value)

    semantic_kwargs.pop("stream", None)

    normalized = normalize_for_json(semantic_kwargs)
    return {
        "provider": provider,
        "api": api,
        **normalized,
    }


def compute_input_id(input_record: dict[str, Any]) -> str:
    payload = stable_json(input_record)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(
        normalize_for_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
