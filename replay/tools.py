from __future__ import annotations

import inspect
import math
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ReplayedToolError, ToolSerializationError
from .filesystem_effects import FilesystemCapture
from .ids import compute_input_id
from .semantic_runtime import RUNTIME


async def invoke_tool(
    name: str,
    arguments: Mapping[str, Any] | None,
    invoke: Callable[[], Any],
    *,
    namespace: str | None = None,
    version: str | None = None,
    fs_capture: FilesystemCapture | None = None,
) -> Any:
    """Record or replay a named async tool call.

    This is the core tool protocol for adapters: provide a stable tool name,
    JSON-like arguments, and a thunk that performs the live tool call.
    """

    from .context import get_current_session

    session = get_current_session()
    if session is None:
        result = invoke()
        if inspect.isawaitable(result):
            return await result
        return result

    input_record, input_id = prepare_tool_input(
        name,
        arguments,
        namespace=namespace,
        version=version,
    )
    return await session.handle_async_tool_event(
        input_record=input_record,
        input_id=input_id,
        tool_name=name,
        invoke=invoke,
        fs_capture=fs_capture,
        input_arguments=arguments,
    )


def invoke_tool_sync(
    name: str,
    arguments: Mapping[str, Any] | None,
    invoke: Callable[[], Any],
    *,
    namespace: str | None = None,
    version: str | None = None,
    fs_capture: FilesystemCapture | None = None,
) -> Any:
    """Record or replay a named sync tool call."""

    from .context import get_current_session

    session = get_current_session()
    if session is None:
        return invoke()

    input_record, input_id = prepare_tool_input(
        name,
        arguments,
        namespace=namespace,
        version=version,
    )
    return session.handle_sync_tool_event(
        input_record=input_record,
        input_id=input_id,
        tool_name=name,
        invoke=invoke,
        fs_capture=fs_capture,
        input_arguments=arguments,
    )


def prepare_tool_input(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    namespace: str | None = None,
    version: str | None = None,
) -> tuple[dict[str, Any], str]:
    input_record = build_tool_input_record(
        tool_name,
        RUNTIME.plain_snapshot(arguments),
        namespace=namespace,
        version=version,
    )
    return input_record, compute_input_id(input_record)


def build_tool_input_record(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    namespace: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    if not isinstance(tool_name, str) or not tool_name:
        raise ToolSerializationError("Tool records require a non-empty string tool name.")

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise ToolSerializationError(
            f"Tool {tool_name!r} input arguments must be a mapping, "
            f"got {type(arguments).__name__}."
        )

    input_record = {
        "tool_name": tool_name,
        "arguments": to_replay_json(dict(arguments), location=f"tool {tool_name!r} input"),
    }
    if namespace is not None:
        input_record["namespace"] = str(namespace)
    if version is not None:
        input_record["version"] = str(version)
    return input_record


def tool_output_to_record(value: Any, *, tool_name: str) -> dict[str, Any]:
    return {"value": to_replay_json(value, location=f"tool {tool_name!r} output", sort_dict_keys=False)}


def tool_error_to_record(exc: BaseException) -> dict[str, str]:
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
        "repr": repr(exc),
    }


def replay_tool_record(record: dict[str, Any]) -> Any:
    error = record.get("error")
    if error:
        input_record = record.get("input") if isinstance(record.get("input"), dict) else {}
        raise ReplayedToolError(
            tool_name=input_record.get("tool_name"),
            record_uid=record.get("record_uid"),
            original_type=error.get("type"),
            message=error.get("message"),
            original_repr=error.get("repr"),
        )
    output = record.get("output")
    if not isinstance(output, dict) or "value" not in output:
        raise ToolSerializationError(f"Tool replay record has no output value: {record.get('record_uid')}")
    return output["value"]


def to_replay_json(value: Any, *, location: str, sort_dict_keys: bool = True) -> Any:
    if value is None:
        return None

    if hasattr(value, "model_dump"):
        return to_replay_json(
            value.model_dump(mode="json", exclude_none=True),
            location=location,
            sort_dict_keys=sort_dict_keys,
        )

    if is_dataclass(value):
        return to_replay_json(asdict(value), location=location, sort_dict_keys=sort_dict_keys)

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = normalize_key(key, location=location)
            if normalized_key in normalized:
                raise ToolSerializationError(
                    f"{location} contains duplicate key after JSON normalization: {normalized_key!r}."
                )
            if item is None:
                continue
            normalized[normalized_key] = to_replay_json(
                item,
                location=f"{location}.{normalized_key}",
                sort_dict_keys=sort_dict_keys,
            )
        keys = sorted(normalized) if sort_dict_keys else normalized.keys()
        return {key: normalized[key] for key in keys}

    if isinstance(value, (list, tuple)):
        return [
            to_replay_json(item, location=f"{location}[]", sort_dict_keys=sort_dict_keys)
            for item in value
        ]

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolSerializationError(f"{location} contains non-finite float {value!r}.")
        if value == 0:
            return 0.0
        return float(Decimal(str(value)).normalize())

    if isinstance(value, Decimal):
        return float(value.normalize())

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (str, int, bool)):
        return value

    raise ToolSerializationError(
        f"{location} contains unsupported value of type {type(value).__name__}; "
        "return JSON-like data from wrapped tools."
    )


def normalize_key(key: Any, *, location: str) -> str:
    if isinstance(key, str):
        return key
    if isinstance(key, (int, float, bool)):
        return str(key)
    raise ToolSerializationError(
        f"{location} contains unsupported dict key of type {type(key).__name__}; "
        "tool records require JSON-like object keys."
    )
