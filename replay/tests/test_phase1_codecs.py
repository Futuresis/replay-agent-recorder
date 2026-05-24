from __future__ import annotations

import inspect

import pytest

import replay
from replay.langgraph_patch import install_langgraph_patch

from replay.response import (
    build_override_message_response,
    build_override_response,
    response_from_record,
    response_to_record,
)


def _openai_response(content: str = "hello"):
    from openai.types.chat.chat_completion import ChatCompletion

    raw = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if hasattr(ChatCompletion, "model_validate"):
        return ChatCompletion.model_validate(raw)
    return ChatCompletion.parse_obj(raw)


def test_public_api_exports_adapter_installers_and_flags() -> None:
    signature = inspect.signature(replay.install)

    assert "langchain" in signature.parameters
    assert "langgraph" in signature.parameters
    assert hasattr(replay, "install_langchain_patch")
    assert hasattr(replay, "install_langgraph_patch")
    assert hasattr(replay, "ClassMethodToolAdapter")


def test_langgraph_patch_restore_is_reference_counted() -> None:
    class DummyStateGraph:
        def add_node(self, node, action=None, **kwargs):
            return node, action, kwargs

    original_add_node = DummyStateGraph.add_node
    first_restore = install_langgraph_patch({"StateGraph": DummyStateGraph})
    second_restore = install_langgraph_patch({"StateGraph": DummyStateGraph})

    try:
        assert DummyStateGraph.add_node is not original_add_node
        first_restore()
        assert DummyStateGraph.add_node is not original_add_node
        second_restore()
        assert DummyStateGraph.add_node is original_add_node
    finally:
        first_restore()
        second_restore()
        DummyStateGraph.add_node = original_add_node


def test_langgraph_compile_patch_does_not_crash_for_readonly_compiled() -> None:
    class UnpatchableCompiled:
        def invoke(self, input_value):
            return {"input": input_value}

        def __setattr__(self, name, value):
            if name in {"invoke", "ainvoke", "stream", "astream", "__replay_langgraph_run_wrapper__"}:
                raise AttributeError("readonly")
            object.__setattr__(self, name, value)

    class DummyStateGraph:
        def add_node(self, node, action=None, **kwargs):
            return node, action, kwargs

        def compile(self, *args, **kwargs):
            return UnpatchableCompiled()

    restore = install_langgraph_patch({"StateGraph": DummyStateGraph})
    try:
        compiled = DummyStateGraph().compile()
    finally:
        restore()

    assert isinstance(compiled, UnpatchableCompiled)
    assert compiled.invoke({"x": 1}) == {"input": {"x": 1}}


def test_install_langgraph_patch_compile_does_not_crash_for_readonly_compiled() -> None:
    class UnpatchableCompiled:
        def invoke(self, input_value):
            return {"input": input_value}

        def __setattr__(self, name, value):
            if name in {
                "invoke",
                "ainvoke",
                "stream",
                "astream",
                "__replay_langgraph_run_wrapper__",
            }:
                raise AttributeError("readonly")
            object.__setattr__(self, name, value)

    class DummyStateGraph:
        def add_node(self, node, action=None, **kwargs):
            return node, action, kwargs

        def compile(self, *args, **kwargs):
            return UnpatchableCompiled()

    restore = install_langgraph_patch({"StateGraph": DummyStateGraph})
    try:
        compiled = DummyStateGraph().compile()
    finally:
        restore()

    assert isinstance(compiled, UnpatchableCompiled)
    assert compiled.invoke({"x": 1}) == {"input": {"x": 1}}


def test_openai_codec_preserves_legacy_record_compatibility() -> None:
    pytest.importorskip("openai")
    response = _openai_response()

    output_record = response_to_record(response)
    assert output_record["codec"] == "openai_chat_completion"
    assert output_record["content"] == "hello"

    replayed = response_from_record({"output": output_record})
    assert replayed.choices[0].message.content == "hello"

    legacy_replayed = response_from_record(
        {"output": {key: value for key, value in output_record.items() if key != "codec"}}
    )
    assert legacy_replayed.choices[0].message.content == "hello"

    overridden = build_override_response({"output": output_record}, "override")
    assert overridden.choices[0].message.content == "override"

    overridden_message = build_override_message_response(
        {"output": output_record},
        {
            "content": "tool",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
    )
    assert overridden_message.choices[0].message.content == "tool"
    assert overridden_message.choices[0].finish_reason == "tool_calls"


def test_langchain_message_and_chunk_codecs_roundtrip_without_api() -> None:
    pytest.importorskip("langchain_core")
    from langchain_core.messages import AIMessage, AIMessageChunk

    message = AIMessage(
        content="lookup",
        tool_calls=[{"id": "call_1", "name": "lookup", "args": {"query": "replay"}}],
    )
    output_record = response_to_record(message)

    assert output_record["codec"] == "langchain_message"
    assert output_record["tool_calls"][0]["name"] == "lookup"
    replayed = response_from_record({"output": output_record})
    assert isinstance(replayed, AIMessage)
    assert replayed.content == "lookup"
    assert replayed.tool_calls[0]["args"] == {"query": "replay"}

    overridden = build_override_response({"output": output_record}, "override")
    assert isinstance(overridden, AIMessage)
    assert overridden.content == "override"
    assert overridden.tool_calls == []

    chunks = [AIMessageChunk(content="hel"), AIMessageChunk(content="lo")]
    chunk_record = response_to_record(chunks)
    assert chunk_record["codec"] == "langchain_message_chunk_list"
    chunk_replayed = response_from_record({"output": chunk_record})
    assert "".join(chunk.content for chunk in chunk_replayed) == "hello"
