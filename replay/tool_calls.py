from __future__ import annotations

import json
from typing import Any


def build_tool_call_records(
    source_record: dict[str, Any],
    *,
    next_record_uid,
    link_tool_executions: bool = True,
) -> list[dict[str, Any]]:
    output = source_record.get("output")
    tool_calls = output.get("tool_calls") if isinstance(output, dict) else None
    if not isinstance(tool_calls, list) or not tool_calls:
        return []

    spans = []
    metadata = source_record.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("spans"), list):
        spans = metadata["spans"]

    records = []
    for index, tool_call in enumerate(tool_calls):
        parsed = parse_tool_call(tool_call, index=index)
        if parsed is None:
            continue
        tool_call_metadata = {
            "component": "tool_call",
            "replayable": False,
            "spans": spans,
        }
        if not link_tool_executions:
            tool_call_metadata["link_tool_executions"] = False
        records.append(
            {
                "record_uid": next_record_uid(),
                "kind": "tool_call",
                "input_id": source_record.get("input_id"),
                "path_id": f"{source_record.get('path_id')}/tool_call/{index}",
                "input": {
                    **parsed,
                    "index": index,
                    "source_llm_record_uid": source_record.get("record_uid"),
                },
                "output": None,
                "metadata": tool_call_metadata,
            }
        )
    return records


def parse_tool_call(tool_call: Any, *, index: int) -> dict[str, Any] | None:
    if not isinstance(tool_call, dict):
        return None

    if isinstance(tool_call.get("function"), dict):
        function = tool_call["function"]
        return {
            "tool_call_id": tool_call.get("id"),
            "tool_name": function.get("name"),
            "arguments": parse_arguments(function.get("arguments")),
        }

    if "name" in tool_call or "args" in tool_call:
        return {
            "tool_call_id": tool_call.get("id"),
            "tool_name": tool_call.get("name"),
            "arguments": tool_call.get("args"),
        }

    return {
        "tool_call_id": tool_call.get("id"),
        "tool_name": tool_call.get("name") or f"tool_call_{index}",
        "arguments": tool_call.get("arguments"),
    }


def parse_arguments(arguments: Any) -> Any:
    if not isinstance(arguments, str):
        return arguments
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
