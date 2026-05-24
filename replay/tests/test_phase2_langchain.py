from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openai")

import replay
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from replay.graph_ir import build_graph_ir
from replay.semantic_runtime import RUNTIME


def _load_llm_config() -> tuple[str, str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(".env").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return (
        values["OPENROUTER_API_KEY"],
        values["OPENROUTER_BASE_URL"],
        values["MODEL_NAME"],
    )


def _chat_model() -> ChatOpenAI:
    api_key, base_url, model_name = _load_llm_config()
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        timeout=120,
        max_retries=1,
        max_tokens=1024,
    )


@tool
def add_tool(a: int, b: int) -> str:
    """Add two integers and return the sum as text."""
    return str(a + b)


@tool
async def multiply_tool(a: int, b: int) -> str:
    """Multiply two integers and return the product as text."""
    await asyncio.sleep(0)
    return str(a * b)


def _jsonl(log_dir: Path, run_id: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (log_dir / f"{run_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def _tool_calls(message: AIMessage) -> list[dict]:
    tool_calls = getattr(message, "tool_calls", None) or []
    assert tool_calls, (
        "Provider did not return tool_calls for the bind_tools prompt. "
        f"content={message.content!r}; additional_kwargs={message.additional_kwargs!r}; "
        f"response_metadata={message.response_metadata!r}"
    )
    return tool_calls


def _invoke_add_tool_call_sync() -> AIMessage:
    llm = _chat_model().bind_tools([add_tool], tool_choice="add_tool")
    message = llm.invoke("Use add_tool with a=13 and b=29.")
    assert isinstance(message, AIMessage)
    return message


async def _invoke_add_tool_call() -> AIMessage:
    llm = _chat_model().bind_tools([add_tool], tool_choice="add_tool")
    message = await llm.ainvoke("Use add_tool with a=13 and b=29.")
    assert isinstance(message, AIMessage)
    return message


async def _invoke_multiply_tool_call() -> AIMessage:
    llm = _chat_model().bind_tools([multiply_tool], tool_choice="multiply_tool")
    message = await llm.ainvoke("Use multiply_tool with a=6 and b=7.")
    assert isinstance(message, AIMessage)
    return message


def _first_tool_payload(message: AIMessage) -> dict:
    tool_call = _tool_calls(message)[0]
    return {
        "type": "tool_call",
        "name": tool_call["name"],
        "args": tool_call["args"],
        "id": tool_call["id"],
    }


def _assert_tool_call_record_shape(
    records: list[dict],
    *,
    expected_tool_call: dict,
    expected_arguments: dict,
) -> None:
    llm_record = next(item for item in records if item.get("kind") == "llm")
    tool_call = next(item for item in records if item.get("kind") == "tool_call")

    assert tool_call["path_id"] == f"{llm_record['path_id']}/tool_call/0"
    assert tool_call["input"]["index"] == 0
    assert tool_call["input"]["tool_call_id"] == expected_tool_call["id"]
    assert tool_call["input"]["tool_name"] == expected_tool_call["name"]
    assert tool_call["input"]["arguments"] == expected_arguments
    assert tool_call["input"]["source_llm_record_uid"] == llm_record["record_uid"]
    assert tool_call["metadata"]["component"] == "tool_call"
    assert tool_call["metadata"]["replayable"] is False


async def _invoke_model() -> AIMessage:
    llm = _chat_model()
    message = await llm.ainvoke("Reply with exactly PHASE2_LC_MODEL")
    assert isinstance(message, AIMessage)
    return message


async def _stream_model() -> list[AIMessageChunk]:
    llm = _chat_model()
    chunks = [
        chunk async for chunk in llm.astream("Reply with exactly PHASE2_LC_STREAM")
    ]
    assert chunks
    assert all(isinstance(chunk, AIMessageChunk) for chunk in chunks)
    return chunks


def test_langchain_base_chat_model_record_replay_codecs(tmp_path: Path) -> None:
    async def scenario() -> None:
        replay.install(langchain=True)
        try:
            with replay.record("phase2_model", log_dir=tmp_path):
                model_message = await _invoke_model()
            model_records = [
                item for item in _jsonl(tmp_path, "phase2_model") if item.get("kind") == "llm"
            ]
            assert len(model_records) == 1
            assert model_records[0]["metadata"]["framework"] == "langchain"
            assert model_records[0]["metadata"]["component"] == "chat_model"
            assert model_records[0]["output"]["codec"] == "langchain_message"
            assert "PHASE2_LC_MODEL" in model_message.content

            with replay.replay(base_run="phase2_model", log_dir=tmp_path):
                replayed_message = await _invoke_model()
            assert replayed_message.content == model_message.content

            with replay.record("phase2_stream", log_dir=tmp_path):
                chunks = await _stream_model()
            stream_records = [
                item for item in _jsonl(tmp_path, "phase2_stream") if item.get("kind") == "llm"
            ]
            assert len(stream_records) == 1
            assert stream_records[0]["metadata"]["method"] == "astream"
            assert stream_records[0]["output"]["codec"] == "langchain_message_chunk_list"

            with replay.replay(base_run="phase2_stream", log_dir=tmp_path):
                replayed_chunks = await _stream_model()
            assert "".join(chunk.content for chunk in replayed_chunks) == "".join(
                chunk.content for chunk in chunks
            )
        finally:
            replay.uninstall()

    asyncio.run(scenario())


def test_langchain_sync_chat_model_record_replay_codecs(tmp_path: Path) -> None:
    replay.install(langchain=True)
    try:
        llm = _chat_model()
        prompt = "Reply with exactly PHASE2_LC_SYNC_MODEL"

        with replay.record("phase2_sync_model", log_dir=tmp_path):
            message = llm.invoke(prompt)
        assert isinstance(message, AIMessage)
        records = [
            item for item in _jsonl(tmp_path, "phase2_sync_model") if item.get("kind") == "llm"
        ]
        assert records[0]["metadata"]["method"] == "invoke"
        assert records[0]["output"]["codec"] == "langchain_message"

        with replay.replay(base_run="phase2_sync_model", log_dir=tmp_path):
            replayed_message = llm.invoke(prompt)
        assert replayed_message.content == message.content

        stream_prompt = "Reply with exactly PHASE2_LC_SYNC_STREAM"
        with replay.record("phase2_sync_stream", log_dir=tmp_path):
            chunks = list(llm.stream(stream_prompt))
        assert chunks
        stream_records = [
            item for item in _jsonl(tmp_path, "phase2_sync_stream") if item.get("kind") == "llm"
        ]
        assert stream_records[0]["metadata"]["method"] == "stream"
        assert stream_records[0]["output"]["codec"] == "langchain_message_chunk_list"

        with replay.replay(base_run="phase2_sync_stream", log_dir=tmp_path):
            replayed_chunks = list(llm.stream(stream_prompt))
        assert "".join(chunk.content for chunk in replayed_chunks) == "".join(
            chunk.content for chunk in chunks
        )
    finally:
        replay.uninstall()


def test_langchain_base_tool_record_replay_all_entrypoints(tmp_path: Path) -> None:
    async def scenario() -> None:
        replay.install(langchain=True)
        try:
            with replay.record("phase2_tools", log_dir=tmp_path):
                sync_direct = add_tool.invoke({"a": 1, "b": 2})
                sync_run = add_tool.run({"a": 3, "b": 4})
                async_direct = await multiply_tool.ainvoke({"a": 2, "b": 5})
                async_run = await multiply_tool.arun({"a": 3, "b": 6})
                tool_call_message = add_tool.invoke(
                    {
                        "type": "tool_call",
                        "name": "add_tool",
                        "args": {"a": 5, "b": 7},
                        "id": "tool_call_1",
                    }
                )
            assert (sync_direct, sync_run, async_direct, async_run) == (
                "3",
                "7",
                "10",
                "18",
            )
            assert getattr(tool_call_message, "tool_call_id", None) == "tool_call_1"

            tool_records = [
                item for item in _jsonl(tmp_path, "phase2_tools") if item.get("kind") == "tool"
            ]
            assert len(tool_records) == 5
            assert all(
                item["metadata"]["framework"] == "langchain" for item in tool_records
            )
            assert [item["metadata"]["method"] for item in tool_records] == [
                "invoke",
                "run",
                "ainvoke",
                "arun",
                "invoke",
            ]

            with replay.replay(base_run="phase2_tools", log_dir=tmp_path):
                assert add_tool.invoke({"a": 1, "b": 2}) == sync_direct
                assert add_tool.run({"a": 3, "b": 4}) == sync_run
                assert await multiply_tool.ainvoke({"a": 2, "b": 5}) == async_direct
                assert await multiply_tool.arun({"a": 3, "b": 6}) == async_run
                replayed_tool_call_message = add_tool.invoke(
                    {
                        "type": "tool_call",
                        "name": "add_tool",
                        "args": {"a": 5, "b": 7},
                        "id": "tool_call_1",
                    }
                )
            assert (
                getattr(replayed_tool_call_message, "tool_call_id", None)
                == "tool_call_1"
            )
        finally:
            replay.uninstall()

    asyncio.run(scenario())


def test_bind_tools_intent_without_tool_execution_records_tool_call(
    tmp_path: Path,
) -> None:
    replay.install(langchain=True)
    try:
        async def scenario() -> AIMessage:
            with replay.record("phase2_bind_tools_intent", log_dir=tmp_path):
                with RUNTIME.context_span(
                    "phase2_test",
                    "adapter_tool_call_shape",
                    {"case": "bind_tools_intent"},
                ):
                    return await _invoke_add_tool_call()

        message = asyncio.run(scenario())
        expected_tool_call = _tool_calls(message)[0]
        records = _jsonl(tmp_path, "phase2_bind_tools_intent")
        kinds = [item.get("kind") for item in records]
        assert kinds.count("llm") == 1
        assert kinds.count("tool_call") == 1
        assert kinds.count("tool") == 0
        _assert_tool_call_record_shape(
            records,
            expected_tool_call=expected_tool_call,
            expected_arguments={"a": 13, "b": 29},
        )
    finally:
        replay.uninstall()


def test_bind_tools_intent_followed_by_base_tool_execution_links_graph_edge(
    tmp_path: Path,
) -> None:
    replay.install(langchain=True)
    try:
        with replay.record("phase2_bind_tools_execution", log_dir=tmp_path):
            message = _invoke_add_tool_call_sync()
            payload = _first_tool_payload(message)
            tool_message = add_tool.invoke(payload)
            assert str(tool_message.content) == "42"
            assert getattr(tool_message, "tool_call_id", None) == payload["id"]

        records = _jsonl(tmp_path, "phase2_bind_tools_execution")
        assert [item.get("kind") for item in records].count("tool_call") == 1
        ir = build_graph_ir(records)
        assert any(
            edge["edge_kind"] == "tool_execution" for edge in ir["graph"]["edges"]
        )
    finally:
        replay.uninstall()


def test_bind_tools_intent_followed_by_base_tool_ainvoke_links_graph_edge(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        replay.install(langchain=True)
        try:
            with replay.record("phase2_bind_tools_async_execution", log_dir=tmp_path):
                message = await _invoke_multiply_tool_call()
                payload = _first_tool_payload(message)
                tool_message = await multiply_tool.ainvoke(payload)
                assert str(tool_message.content) == "42"
                assert getattr(tool_message, "tool_call_id", None) == payload["id"]

            records = _jsonl(tmp_path, "phase2_bind_tools_async_execution")
            assert [item.get("kind") for item in records].count("tool_call") == 1
            ir = build_graph_ir(records)
            assert any(
                edge["edge_kind"] == "tool_execution" for edge in ir["graph"]["edges"]
            )
        finally:
            replay.uninstall()

    asyncio.run(scenario())


def test_langchain_real_provider_pure_replay_and_live_fork_tool_call(
    tmp_path: Path,
) -> None:
    replay.install(langchain=True)
    try:
        with replay.record("phase2_real_tool_call_base", log_dir=tmp_path):
            first_message = _invoke_add_tool_call_sync()

        with replay.replay(base_run="phase2_real_tool_call_base", log_dir=tmp_path):
            replayed_message = _invoke_add_tool_call_sync()

        with replay.replay(
            base_run="phase2_real_tool_call_base",
            breakpoint_record_uid="rec_000001",
            log_dir=tmp_path,
            fork_run="phase2_real_tool_call_fork",
        ):
            forked_message = _invoke_add_tool_call_sync()

        _tool_calls(first_message)
        _tool_calls(replayed_message)
        _tool_calls(forked_message)

        base_records = _jsonl(tmp_path, "phase2_real_tool_call_base")
        assert [record.get("kind") for record in base_records] == ["llm", "tool_call"]

        fork_records = _jsonl(tmp_path, "phase2_real_tool_call_fork")
        records = [record for record in fork_records if record.get("kind")]
        assert [record.get("kind") for record in records] == ["llm", "tool_call"]
        assert records[1]["input"]["source_llm_record_uid"] == records[0]["record_uid"]
    finally:
        replay.uninstall()


def test_langchain_stream_override_message_writes_adapter_tool_call(
    tmp_path: Path,
) -> None:
    replay.install(langchain=True)
    try:
        prompt = "Reply with exactly PHASE2_STREAM_OVERRIDE_BASE"
        llm = _chat_model()

        with replay.record("phase2_stream_override_base", log_dir=tmp_path):
            base_chunks = list(llm.stream(prompt))
        assert base_chunks

        with replay.replay(
            base_run="phase2_stream_override_base",
            breakpoint_record_uid="rec_000001",
            override_message={
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_stream_override",
                        "name": "lookup",
                        "args": {"query": "stream-override"},
                        "type": "tool_call",
                    }
                ],
            },
            log_dir=tmp_path,
            fork_run="phase2_stream_override_fork",
        ):
            override_chunks = list(llm.stream(prompt))

        assert override_chunks
        merged_chunk = override_chunks[0]
        for chunk in override_chunks[1:]:
            merged_chunk += chunk
        assert merged_chunk.tool_calls[0]["id"] == "call_stream_override"

        fork_records = _jsonl(tmp_path, "phase2_stream_override_fork")
        records = [record for record in fork_records if record.get("kind")]
        assert [record.get("kind") for record in records] == ["llm", "tool_call"]
        assert records[0]["output"]["codec"] == "langchain_message_chunk_list"
        assert records[1]["input"]["tool_call_id"] == "call_stream_override"
        assert records[1]["input"]["tool_name"] == "lookup"
        assert records[1]["input"]["arguments"] == {"query": "stream-override"}
    finally:
        replay.uninstall()
