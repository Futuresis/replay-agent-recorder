from __future__ import annotations

import copy
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import LlmCodecError


OPENAI_CHAT_CODEC = "openai_chat_completion"
LANGCHAIN_MESSAGE_CODEC = "langchain_message"
LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC = "langchain_message_chunk_list"


def serialize_llm_output(response: Any) -> dict[str, Any]:
    codec = detect_llm_codec(response)
    if codec == OPENAI_CHAT_CODEC:
        return _serialize_openai_chat_completion(response)
    if codec == LANGCHAIN_MESSAGE_CODEC:
        return _serialize_langchain_message(response)
    if codec == LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC:
        return _serialize_langchain_message_chunk_list(response)
    raise LlmCodecError(f"Unsupported LLM codec: {codec}")


def deserialize_llm_output(output_record: dict[str, Any]) -> Any:
    codec = output_record.get("codec")
    if codec is None:
        return _deserialize_openai_chat_completion(output_record)
    if codec == OPENAI_CHAT_CODEC:
        return _deserialize_openai_chat_completion(output_record)
    if codec == LANGCHAIN_MESSAGE_CODEC:
        return _deserialize_langchain_message(output_record)
    if codec == LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC:
        return _deserialize_langchain_message_chunk_list(output_record)
    raise LlmCodecError(f"Unsupported LLM codec: {codec}")


def build_llm_output_override(output_record: dict[str, Any], override_output: str) -> Any:
    codec = output_record.get("codec")
    if codec is None or codec == OPENAI_CHAT_CODEC:
        return _build_openai_text_override(output_record, override_output)
    if codec == LANGCHAIN_MESSAGE_CODEC:
        return _build_langchain_text_override(output_record, override_output)
    if codec == LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC:
        return _build_langchain_chunk_list_text_override(override_output)
    raise LlmCodecError(f"Unsupported LLM codec: {codec}")


def build_llm_message_override(output_record: dict[str, Any], override_message: dict[str, Any]) -> Any:
    codec = output_record.get("codec")
    if codec is None or codec == OPENAI_CHAT_CODEC:
        return _build_openai_message_override(output_record, override_message)
    if codec == LANGCHAIN_MESSAGE_CODEC:
        return _build_langchain_message_override(output_record, override_message)
    if codec == LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC:
        return _build_langchain_chunk_list_message_override(override_message)
    raise LlmCodecError(f"Unsupported LLM codec: {codec}")


def detect_llm_codec(response: Any) -> str:
    response = _unwrap_legacy_openai_response(response)
    if _is_langchain_message_chunk_list(response):
        return LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC
    if _is_langchain_message(response):
        return LANGCHAIN_MESSAGE_CODEC
    if _looks_like_openai_chat_completion(response):
        return OPENAI_CHAT_CODEC
    raise LlmCodecError(f"Unsupported LLM response type: {type(response).__name__}")


def _looks_like_openai_chat_completion(response: Any) -> bool:
    response = _unwrap_legacy_openai_response(response)
    raw = to_jsonable(response)
    return isinstance(raw, dict) and isinstance(raw.get("choices"), list) and raw.get("object") == "chat.completion"


def _is_langchain_message(response: Any) -> bool:
    try:
        from langchain_core.messages import BaseMessage
    except Exception:
        return False
    return isinstance(response, BaseMessage)


def _is_langchain_message_chunk_list(response: Any) -> bool:
    if not isinstance(response, list) or not response:
        return False
    try:
        from langchain_core.messages import BaseMessageChunk
    except Exception:
        return False
    return all(isinstance(item, BaseMessageChunk) for item in response)


def _serialize_openai_chat_completion(response: Any) -> dict[str, Any]:
    response = _unwrap_legacy_openai_response(response)
    raw_response = to_jsonable(response)
    choices = raw_response.get("choices") if isinstance(raw_response, dict) else None
    first_message = None
    tool_calls = []

    if choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            first_message = first_choice.get("message")
            if isinstance(first_message, dict):
                tool_calls = first_message.get("tool_calls") or []

    content = first_message.get("content") if isinstance(first_message, dict) else None
    finish_reason = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
    return {
        "codec": OPENAI_CHAT_CODEC,
        "content": content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "raw_response": raw_response,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
    }


def _unwrap_legacy_openai_response(response: Any) -> Any:
    parse = getattr(response, "parse", None)
    if callable(parse):
        try:
            return parse()
        except Exception:
            return response
    return response


def _deserialize_openai_chat_completion(output_record: dict[str, Any]) -> Any:
    return _build_chat_completion(output_record.get("raw_response"))


def _build_openai_text_override(output_record: dict[str, Any], override_output: str) -> Any:
    raw_response = copy.deepcopy(output_record.get("raw_response") or {})
    if not raw_response:
        raw_response = _minimal_chat_completion(override_output)
    else:
        raw_response.setdefault("choices", [_minimal_choice(override_output)])
        if not raw_response["choices"]:
            raw_response["choices"].append(_minimal_choice(override_output))
        first_choice = raw_response["choices"][0]
        first_choice.setdefault("message", {})
        first_choice["message"]["role"] = first_choice["message"].get("role") or "assistant"
        first_choice["message"]["content"] = override_output
        first_choice["message"].pop("tool_calls", None)
        first_choice["finish_reason"] = "stop"
    return _build_chat_completion(raw_response)


def _build_openai_message_override(output_record: dict[str, Any], override_message: dict[str, Any]) -> Any:
    raw_response = copy.deepcopy(output_record.get("raw_response") or {})
    if not raw_response:
        raw_response = _minimal_chat_completion("")

    raw_response.setdefault("choices", [_minimal_choice("")])
    if not raw_response["choices"]:
        raw_response["choices"].append(_minimal_choice(""))

    first_choice = raw_response["choices"][0]
    first_choice.setdefault("message", {})
    message = first_choice["message"]
    message["role"] = message.get("role") or "assistant"

    patch = dict(override_message)
    finish_reason = patch.pop("finish_reason", None)
    tool_calls_was_patched = "tool_calls" in patch
    tool_calls = patch.get("tool_calls")

    for key, value in patch.items():
        if key == "tool_calls" and value is None:
            message.pop("tool_calls", None)
        else:
            message[key] = value

    if finish_reason is not None:
        first_choice["finish_reason"] = finish_reason
    elif tool_calls_was_patched:
        if tool_calls:
            first_choice["finish_reason"] = "tool_calls"
        elif first_choice.get("finish_reason") == "tool_calls":
            first_choice["finish_reason"] = "stop"

    return _build_chat_completion(raw_response)


def _serialize_langchain_message(response: Any) -> dict[str, Any]:
    from langchain_core.messages import message_to_dict

    return {
        "codec": LANGCHAIN_MESSAGE_CODEC,
        "content": _langchain_content(response),
        "tool_calls": to_jsonable(getattr(response, "tool_calls", None) or []),
        "message": message_to_dict(response),
        "usage": _langchain_usage(response),
    }


def _deserialize_langchain_message(output_record: dict[str, Any]) -> Any:
    from langchain_core.messages import messages_from_dict

    message_dict = output_record.get("message")
    if not isinstance(message_dict, dict):
        raise LlmCodecError("langchain_message codec requires a serialized message dict.")
    return messages_from_dict([message_dict])[0]


def _build_langchain_text_override(output_record: dict[str, Any], override_output: str) -> Any:
    return _build_langchain_message_override(output_record, {"content": override_output, "tool_calls": []})


def _build_langchain_message_override(output_record: dict[str, Any], override_message: dict[str, Any]) -> Any:
    from langchain_core.messages import messages_from_dict

    encoded = copy.deepcopy(output_record.get("message") or {})
    if not isinstance(encoded, dict):
        raise LlmCodecError("langchain_message codec requires a serialized message dict.")
    data = encoded.setdefault("data", {})
    if not isinstance(data, dict):
        raise LlmCodecError("langchain_message codec data payload must be a dict.")
    if "content" in override_message:
        data["content"] = override_message.get("content")
    if "tool_calls" in override_message:
        data["tool_calls"] = to_jsonable(override_message.get("tool_calls") or [])
        data["invalid_tool_calls"] = []
    return messages_from_dict([encoded])[0]


def _serialize_langchain_message_chunk_list(chunks: list[Any]) -> dict[str, Any]:
    from langchain_core.messages import messages_to_dict

    encoded_chunks = messages_to_dict(chunks)
    content = "".join(_langchain_content(chunk) for chunk in chunks)
    tool_calls = []
    usage = None
    if chunks:
        try:
            merged_chunk = chunks[0]
            for chunk in chunks[1:]:
                merged_chunk += chunk
            tool_calls = to_jsonable(getattr(merged_chunk, "tool_calls", None) or [])
        except Exception:
            tool_calls = []
    for chunk in reversed(chunks):
        if tool_calls:
            break
        tool_calls = to_jsonable(getattr(chunk, "tool_calls", None) or [])
    for chunk in reversed(chunks):
        usage = _langchain_usage(chunk)
        if usage is not None:
            break
    return {
        "codec": LANGCHAIN_MESSAGE_CHUNK_LIST_CODEC,
        "content": content,
        "tool_calls": tool_calls,
        "chunks": encoded_chunks,
        "usage": usage,
    }


def _deserialize_langchain_message_chunk_list(output_record: dict[str, Any]) -> Any:
    from langchain_core.messages import messages_from_dict

    encoded_chunks = output_record.get("chunks")
    if not isinstance(encoded_chunks, list):
        raise LlmCodecError("langchain_message_chunk_list codec requires a serialized chunk list.")
    return messages_from_dict(encoded_chunks)


def _build_langchain_chunk_list_text_override(override_output: str) -> Any:
    from langchain_core.messages import AIMessageChunk

    return [AIMessageChunk(content=override_output)]


def _build_langchain_chunk_list_message_override(override_message: dict[str, Any]) -> Any:
    from langchain_core.messages import AIMessageChunk

    kwargs = {"content": override_message.get("content") or ""}
    if "tool_calls" in override_message:
        kwargs["tool_calls"] = to_jsonable(override_message.get("tool_calls") or [])
    return [AIMessageChunk(**kwargs)]


def _build_chat_completion(raw_response: Any) -> Any:
    from openai.types.chat.chat_completion import ChatCompletion

    if hasattr(ChatCompletion, "model_validate"):
        return ChatCompletion.model_validate(raw_response)
    return ChatCompletion.parse_obj(raw_response)


def _minimal_chat_completion(content: str) -> dict[str, Any]:
    return {
        "id": "replay-override",
        "object": "chat.completion",
        "created": 0,
        "model": "replay",
        "choices": [_minimal_choice(content)],
    }


def _minimal_choice(content: str) -> dict[str, Any]:
    return {
        "index": 0,
        "message": {"role": "assistant", "content": content},
        "finish_reason": "stop",
    }


def _langchain_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text") if isinstance(item.get("text"), str) else str(item))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _langchain_usage(message: Any) -> Any:
    usage = getattr(message, "usage_metadata", None)
    if usage is not None:
        return to_jsonable(usage)
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        if response_metadata.get("token_usage") is not None:
            return to_jsonable(response_metadata.get("token_usage"))
        if response_metadata.get("usage") is not None:
            return to_jsonable(response_metadata.get("usage"))
    return None


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(to_jsonable(item) for item in value)
    if isinstance(value, float):
        if value == 0:
            return 0.0
        return float(Decimal(str(value)).normalize())
    if isinstance(value, Decimal):
        return float(value.normalize())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)
