from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TypedDict
from uuid import UUID

import pytest
from pydantic import BaseModel, SecretStr

from replay.ids import build_input_record, stable_json
from replay.normalization import normalize_for_json


class ResearchQuestion(BaseModel):
    question: str


def test_build_input_record_accepts_provider_api_and_normalizes_models() -> None:
    record = build_input_record(
        {
            "response_format": ResearchQuestion,
            "messages": [{"role": "user", "content": "hi"}],
        },
        provider="langchain",
        api="chat_model.invoke",
    )

    assert json.loads(json.dumps(record)) == record
    assert record["provider"] == "langchain"
    assert record["api"] == "chat_model.invoke"
    assert record["response_format"]["__kind__"] == "pydantic_model_class"


def test_build_input_record_keeps_plain_openai_shape_stable() -> None:
    record = build_input_record(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.7,
        }
    )

    assert record == {
        "provider": "openai",
        "api": "chat.completions.create",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.7,
        "top_p": 1,
        "tools": [],
        "n": 1,
    }
    assert stable_json(record) == (
        '{"api":"chat.completions.create","messages":[{"content":"hello","role":"user"}],'
        '"n":1,"provider":"openai","temperature":0.7,"tools":[],"top_p":1}'
    )


def test_normalize_for_json_supports_runtime_types_and_redacts_secrets() -> None:
    @dataclass
    class Point:
        x: int
        y: int

    class Payload(TypedDict):
        city: str
        count: int

    class Status(Enum):
        READY = "ready"

    normalized = normalize_for_json(
        {
            "model_class": ResearchQuestion,
            "model_instance": ResearchQuestion(question="What changed?"),
            "dataclass_class": Point,
            "dataclass_instance": Point(1, 2),
            "typed_dict_class": Payload,
            "enum_value": Status.READY,
            "dt": datetime(2026, 5, 14, 9, 30, tzinfo=timezone.utc),
            "d": date(2026, 5, 14),
            "t": time(9, 30, 1),
            "delta": timedelta(minutes=2, seconds=30),
            "uuid": UUID("12345678-1234-5678-1234-567812345678"),
            "path": Path("/tmp/demo.txt"),
            "decimal": Decimal("1.2300"),
            "secret": SecretStr("super-secret"),
            "bytes": b"\x00hi",
            "set": {3, 1, 2},
        }
    )

    assert normalized["model_class"]["__kind__"] == "pydantic_model_class"
    assert normalized["model_instance"]["data"] == {"question": "What changed?"}
    assert normalized["dataclass_class"]["__kind__"] == "dataclass_class"
    assert normalized["dataclass_instance"]["data"] == {"x": 1, "y": 2}
    assert normalized["typed_dict_class"]["__kind__"] == "typeddict_class"
    assert normalized["enum_value"]["enum_value"] == "ready"
    assert normalized["dt"] == {"__kind__": "datetime", "value": "2026-05-14T09:30:00+00:00"}
    assert normalized["d"] == {"__kind__": "date", "value": "2026-05-14"}
    assert normalized["t"] == {"__kind__": "time", "value": "09:30:01"}
    assert normalized["delta"] == {"__kind__": "timedelta", "total_seconds": 150.0}
    assert normalized["uuid"]["value"] == "12345678-1234-5678-1234-567812345678"
    assert normalized["path"] == {"__kind__": "path", "value": str(Path("/tmp/demo.txt"))}
    assert normalized["decimal"] == 1.23
    assert normalized["secret"]["__kind__"] == "secret"
    assert "super-secret" not in stable_json(normalized["secret"])
    assert normalized["bytes"] == {"__kind__": "bytes", "base64": "AGhp", "length": 3}
    assert normalized["set"] == [1, 2, 3]


def test_normalize_for_json_handles_langchain_messages() -> None:
    pytest.importorskip("langchain_core")
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages = [
        HumanMessage(content="hello"),
        AIMessage(
            content="tool call",
            tool_calls=[{"name": "lookup", "args": {"query": "hello"}, "id": "call_1"}],
        ),
        ToolMessage(content="HELLO", name="lookup", tool_call_id="call_1"),
    ]

    normalized = normalize_for_json(messages)

    assert [item["type"] for item in normalized] == ["human", "ai", "tool"]
    assert normalized[1]["data"]["tool_calls"][0]["name"] == "lookup"
    assert normalized[2]["data"]["tool_call_id"] == "call_1"


def test_normalize_for_json_handles_cycles_and_max_depth() -> None:
    cycle = []
    cycle.append(cycle)

    assert normalize_for_json(cycle) == [{"__kind__": "cycle", "type": "list"}]
    assert normalize_for_json({"a": {"b": {"c": 1}}}, max_depth=2) == {
        "a": {"b": {"__kind__": "max_depth", "type": "dict"}}
    }
