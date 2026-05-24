from __future__ import annotations

from replay.tool_calls import build_tool_call_records, parse_arguments, parse_tool_call


def test_parse_arguments_decodes_json_strings_and_keeps_invalid_text() -> None:
    assert parse_arguments('{"query":"replay"}') == {"query": "replay"}
    assert parse_arguments("not-json") == "not-json"
    assert parse_arguments({"already": "parsed"}) == {"already": "parsed"}


def test_parse_tool_call_supports_openai_and_langchain_shapes() -> None:
    openai_call = {
        "id": "call_openai",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"query":"openai"}'},
    }
    langchain_call = {
        "id": "call_langchain",
        "name": "lookup",
        "args": {"query": "langchain"},
        "type": "tool_call",
    }

    assert parse_tool_call(openai_call, index=0) == {
        "tool_call_id": "call_openai",
        "tool_name": "lookup",
        "arguments": {"query": "openai"},
    }
    assert parse_tool_call(langchain_call, index=1) == {
        "tool_call_id": "call_langchain",
        "tool_name": "lookup",
        "arguments": {"query": "langchain"},
    }
    assert parse_tool_call("bad", index=2) is None


def test_build_tool_call_records_are_non_replayable_children() -> None:
    counter = iter(["rec_000002", "rec_000003"])
    source_record = {
        "record_uid": "rec_000001",
        "kind": "llm",
        "input_id": "sha256:test",
        "path_id": "root/0",
        "output": {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"query":"one"}'},
                },
                {"id": "call_2", "name": "search", "args": {"query": "two"}},
            ]
        },
        "metadata": {"spans": [{"kind": "llm", "name": "chat"}]},
    }

    records = build_tool_call_records(source_record, next_record_uid=lambda: next(counter))

    assert [record["record_uid"] for record in records] == ["rec_000002", "rec_000003"]
    assert [record["kind"] for record in records] == ["tool_call", "tool_call"]
    assert records[0]["path_id"] == "root/0/tool_call/0"
    assert records[0]["input"] == {
        "tool_call_id": "call_1",
        "tool_name": "lookup",
        "arguments": {"query": "one"},
        "index": 0,
        "source_llm_record_uid": "rec_000001",
    }
    assert records[0]["metadata"] == {
        "component": "tool_call",
        "replayable": False,
        "spans": [{"kind": "llm", "name": "chat"}],
    }
