from __future__ import annotations

from typing import Any

from .codecs import (
    build_llm_message_override,
    build_llm_output_override,
    deserialize_llm_output,
    serialize_llm_output,
)


def response_to_record(response: Any) -> dict[str, Any]:
    return serialize_llm_output(response)


def response_from_record(record: dict[str, Any]) -> Any:
    output_record = record.get("output") if isinstance(record.get("output"), dict) else {}
    return deserialize_llm_output(output_record)


def build_override_response(record: dict[str, Any], override_output: str) -> Any:
    output_record = record.get("output") if isinstance(record.get("output"), dict) else {}
    return build_llm_output_override(output_record, override_output)


def build_override_message_response(
    record: dict[str, Any],
    override_message: dict[str, Any],
) -> Any:
    output_record = record.get("output") if isinstance(record.get("output"), dict) else {}
    return build_llm_message_override(output_record, override_message)
