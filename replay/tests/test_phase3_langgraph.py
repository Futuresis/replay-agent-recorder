from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openai")
pytest.importorskip("langgraph")

import replay
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from replay.semantic_runtime import RUNTIME, Source


class _NativeReturnState(TypedDict, total=False):
    x: int
    result: str


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
def lookup(city: str) -> str:
    """Lookup a city summary."""
    return f"lookup:{city}"


@tool
def echo_x(x: int) -> str:
    """Echo an integer state value."""
    return f"x:{x}"


def _jsonl(log_dir: Path, run_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (log_dir / f"{run_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def _has_langgraph_span(records: list[dict[str, Any]]) -> bool:
    for item in records:
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        for span in metadata.get("spans") or []:
            span_metadata = span.get("metadata")
            if (
                span.get("kind") == "langgraph_node"
                and isinstance(span_metadata, dict)
                and span_metadata.get("framework") == "langgraph"
            ):
                return True
    return False


def _source(name: str) -> Source:
    return Source(
        run_id="phase3-native-return", record_uid=name, kind="test", path_id=name
    )


@contextmanager
def _runtime_enabled():
    token = RUNTIME.enter_context(enabled=True)
    try:
        yield
    finally:
        RUNTIME.exit_context(token)


def test_record_sync_node_can_return_tracked_none_as_noop(tmp_path: Path) -> None:
    replay.install(semantic=False, langgraph=True)
    try:
        graph = StateGraph(_NativeReturnState)
        seen_outputs: list[Any] = []

        def node(state: _NativeReturnState):
            out = RUNTIME.seed_value(None, _source("sync-none"))
            seen_outputs.append(out)
            return out

        graph.add_node("node", node)
        graph.add_edge(START, "node")
        graph.add_edge("node", END)
        compiled = graph.compile()

        with _runtime_enabled():
            with replay.record("sync_none", log_dir=tmp_path):
                result = compiled.invoke({"x": 1})

        assert result == {"x": 1}
        assert type(seen_outputs[0]).__name__ == "_TrackedNone"
    finally:
        replay.uninstall()


def test_record_async_node_can_return_tracked_none_as_noop(tmp_path: Path) -> None:
    async def scenario() -> None:
        replay.install(semantic=False, langgraph=True)
        try:
            graph = StateGraph(_NativeReturnState)
            seen_outputs: list[Any] = []

            async def node(state: _NativeReturnState):
                out = RUNTIME.seed_value(None, _source("async-none"))
                seen_outputs.append(out)
                return out

            graph.add_node("node", node)
            graph.add_edge(START, "node")
            graph.add_edge("node", END)
            compiled = graph.compile()

            with _runtime_enabled():
                with replay.record("async_none", log_dir=tmp_path):
                    result = await compiled.ainvoke({"x": 1})

            assert result == {"x": 1}
            assert type(seen_outputs[0]).__name__ == "_TrackedNone"
        finally:
            replay.uninstall()

    asyncio.run(scenario())


def test_record_node_dict_update_records_tool_with_langgraph_span(tmp_path: Path) -> None:
    replay.install(semantic=False, langchain=True, langgraph=True)
    try:
        graph = StateGraph(_NativeReturnState)
        seen_values: list[Any] = []

        def node(state: _NativeReturnState):
            value = RUNTIME.seed_value(1, _source("dict-value"))
            seen_values.append(value)
            return {"x": value}

        def tool_node(state: _NativeReturnState):
            return {"result": echo_x.invoke({"x": state["x"]})}

        graph.add_node("node", node)
        graph.add_node("tool_node", tool_node)
        graph.add_edge(START, "node")
        graph.add_edge("node", "tool_node")
        graph.add_edge("tool_node", END)
        compiled = graph.compile()

        with _runtime_enabled():
            with replay.record("dict_value", log_dir=tmp_path):
                result = compiled.invoke({})

        assert result == {"x": 1, "result": "x:1"}
        assert type(seen_values[0]).__name__ == "_TrackedInt"
        tool_records = [
            item for item in _jsonl(tmp_path, "dict_value") if item.get("kind") == "tool"
        ]
        assert tool_records
        assert _has_langgraph_span(tool_records)
    finally:
        replay.uninstall()


def _simple_graph() -> Any:
    llm = _chat_model()

    async def planner(state: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"Plan one city to research. User asked: {state['request']}. "
            "Reply with exactly one city name."
        )
        message = await llm.ainvoke(prompt)
        assert isinstance(message, AIMessage)
        city = message.content.strip().split()[0]
        return {"city": city}

    async def tool_step(state: dict[str, Any]) -> dict[str, Any]:
        tool_result = await lookup.ainvoke({"city": state["city"]})
        return {"city": state["city"], "tool_result": tool_result}

    async def summarize(state: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"Summarize findings for {state['city']} using tool result "
            f"{state['tool_result']}. Reply briefly."
        )
        message = await llm.ainvoke(prompt)
        assert isinstance(message, AIMessage)
        return {"summary": message.content}

    graph = StateGraph(dict)
    graph.add_node("planner", planner)
    graph.add_node("tool", tool_step)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "tool")
    graph.add_edge("tool", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


def test_langgraph_simple_graph_record_replay_with_real_api(tmp_path: Path) -> None:
    async def scenario() -> None:
        replay.install(langchain=True, langgraph=True)
        try:
            simple = _simple_graph()
            with replay.record("phase3_simple", log_dir=tmp_path):
                simple_result = await simple.ainvoke({"request": "Research one city"})
            simple_records = _jsonl(tmp_path, "phase3_simple")
            simple_nodes = [
                item for item in simple_records if item.get("kind") in {"llm", "tool"}
            ]
            assert [item["kind"] for item in simple_nodes] == ["llm", "tool", "llm"]
            assert all(
                item["metadata"]["spans"][0]["kind"] == "langgraph_node"
                for item in simple_nodes
            )
            assert all(
                item["metadata"]["spans"][0]["metadata"]["framework"] == "langgraph"
                for item in simple_nodes
            )

            with replay.replay(base_run="phase3_simple", log_dir=tmp_path):
                replayed_simple = await simple.ainvoke({"request": "Research one city"})
            assert replayed_simple["summary"] == simple_result["summary"]
        finally:
            replay.uninstall()

    asyncio.run(scenario())
