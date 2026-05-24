from __future__ import annotations

import json

from replay.context import RecordSession
from replay.semantic_runtime import RUNTIME
from replay.storage import load_replay_records


def _chat_response(content: str) -> dict[str, object]:
    return {
        "object": "chat.completion",
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": None,
    }


def _jsonl_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_record_llm_boundary_writes_metadata_provider_and_extra_records(tmp_path) -> None:
    log_dir = tmp_path / "runs"
    seen_kinds: list[list[str]] = []

    def extra_records_factory(record):
        return [
            {
                "record_uid": "derived-1",
                "kind": "tool_call",
                "path_id": f"{record['path_id']}/tool_call/0",
                "input": {"source_llm_record_uid": record["record_uid"]},
                "metadata": {"replayable": False},
            }
        ]

    with RecordSession(run_id="record", log_dir=log_dir) as session:
        with RUNTIME.context_span("langgraph_node", "planner", {"graph_id": "g1"}):
            response = session.handle_sync_llm_boundary(
                name="chat",
                record_kwargs={"messages": [{"role": "user", "content": "hello"}], "model": "unit"},
                invoke=lambda: seen_kinds.append(RUNTIME.current_kinds_snapshot()) or _chat_response("hi"),
                metadata={"framework": "langchain", "semantic": {"existing": "keep"}},
                semantic_hint="chat:planner",
                provider="langchain",
                api="chat_model.invoke",
                extra_records_factory=extra_records_factory,
            )

    assert response == _chat_response("hi")
    assert seen_kinds == [["llm"]]
    records = _jsonl_records(log_dir / "record.jsonl")
    assert [record["kind"] for record in records] == ["llm", "tool_call"]
    llm_record = records[0]
    assert llm_record["input"]["provider"] == "langchain"
    assert llm_record["input"]["api"] == "chat_model.invoke"
    assert llm_record["metadata"]["framework"] == "langchain"
    assert llm_record["metadata"]["semantic"] == {
        "existing": "keep",
        "callsite_fingerprint": "chat:planner",
    }
    assert llm_record["metadata"]["spans"] == [
        {"kind": "langgraph_node", "name": "planner", "metadata": {"graph_id": "g1"}}
    ]
    assert records[1]["input"]["source_llm_record_uid"] == llm_record["record_uid"]
    assert [record["kind"] for record in load_replay_records(log_dir / "record.jsonl")] == ["llm"]


def test_record_tool_boundary_pushes_kind_and_records_hint_metadata(tmp_path) -> None:
    log_dir = tmp_path / "runs"
    seen_kinds: list[list[str]] = []

    with RecordSession(run_id="record", log_dir=log_dir) as session:
        result = session.handle_sync_tool_boundary(
            tool_name="lookup",
            input_record={"tool_name": "lookup", "arguments": {"query": "hello"}},
            invoke=lambda: seen_kinds.append(RUNTIME.current_kinds_snapshot()) or {"ok": True},
            metadata={"framework": "langchain"},
            semantic_hint="tool:lookup",
            input_arguments={"query": "hello"},
        )

    assert result == {"ok": True}
    assert seen_kinds == [["tool"]]
    record = _jsonl_records(log_dir / "record.jsonl")[0]
    assert record["kind"] == "tool"
    assert record["metadata"]["framework"] == "langchain"
    assert record["metadata"]["semantic"]["callsite_fingerprint"] == "tool:lookup"
