from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("openai")

from openai import AsyncOpenAI
from openai.resources.chat.completions.completions import AsyncCompletions
from openai.types.chat.chat_completion import ChatCompletion


async def fake_create(self, *args, **kwargs):
    content = kwargs["messages"][-1]["content"]
    return ChatCompletion.model_validate(
        {
            "id": f"fake-{content}",
            "object": "chat.completion",
            "created": 0,
            "model": kwargs["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"live:{content}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


_ORIGINAL_ASYNC_CREATE = AsyncCompletions.create

import replay


client = AsyncOpenAI(api_key="test", base_url="http://example.invalid/v1")
log_dir = Path("replay/tmp-runs-smoke")
shutil.rmtree(log_dir, ignore_errors=True)
log_dir.mkdir(parents=True, exist_ok=True)


async def call(text: str) -> str:
    response = await client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": text}],
        temperature=0.7,
    )
    return response.choices[0].message.content


async def raw_call(text: str) -> ChatCompletion:
    return await client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": text}],
        temperature=0.7,
    )


async def three_calls() -> list[str]:
    return await asyncio.gather(call("a"), call("b"), call("c"))


async def dependent_parent_call() -> tuple[list[str], str]:
    child_results = await three_calls()
    parent_result = await call("|".join(child_results))
    return child_results, parent_result


async def main() -> None:
    with replay.record("smoke", log_dir=log_dir):
        recorded = await three_calls()

    records = [
        json.loads(line)
        for line in (log_dir / "smoke.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    path_ids = [record["path_id"] for record in records]

    assert recorded == ["live:a", "live:b", "live:c"], recorded
    assert path_ids == ["root.0/0", "root.1/0", "root.2/0"], path_ids

    with replay.replay(base_run="smoke", log_dir=log_dir):
        replayed = await three_calls()
    assert replayed == recorded, replayed

    with replay.replay(
        base_run="smoke",
        breakpoint_record_uid="rec_000002",
        override_output="override:b",
        log_dir=log_dir,
        fork_run="smoke_fork_test",
    ):
        forked = await three_calls()

    assert forked[0] == "live:a", forked
    assert "override:b" in forked, forked

    fork_records = [
        json.loads(line)
        for line in (log_dir / "smoke_fork_test.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert fork_records[0]["fork_metadata"]["breakpoint_record_uid"] == "rec_000002"
    assert any(item.get("kind") == "llm" for item in fork_records), fork_records

    with replay.record("smoke_dep", log_dir=log_dir):
        dep_recorded = await dependent_parent_call()

    with replay.replay(
        base_run="smoke_dep",
        breakpoint_record_uid="rec_000002",
        override_output="override:b",
        log_dir=log_dir,
        fork_run="smoke_dep_fork_test",
    ):
        dep_forked = await dependent_parent_call()

    assert dep_forked[0] == ["live:a", "override:b", "live:c"], dep_forked
    assert dep_forked[1] == "live:live:a|override:b|live:c", dep_forked

    dep_fork_records = [
        json.loads(line)
        for line in (log_dir / "smoke_dep_fork_test.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    dep_fork_llm_records = [item for item in dep_fork_records if item.get("kind") == "llm"]
    assert [item["output"]["content"] for item in dep_fork_llm_records] == [
        "override:b",
        "live:live:a|override:b|live:c",
    ], dep_fork_llm_records

    with replay.record("smoke_input_override", log_dir=log_dir):
        input_override_recorded = await dependent_parent_call()

    with replay.replay(
        base_run="smoke_input_override",
        breakpoint_record_uid="rec_000002",
        override_input={"messages": [{"role": "user", "content": "patched:b"}]},
        log_dir=log_dir,
        fork_run="smoke_input_override_fork_test",
    ):
        input_override_forked = await dependent_parent_call()

    assert input_override_recorded[0] == ["live:a", "live:b", "live:c"], input_override_recorded
    assert input_override_forked[0] == ["live:a", "live:patched:b", "live:c"], input_override_forked
    assert input_override_forked[1] == "live:live:a|live:patched:b|live:c", input_override_forked

    input_override_fork_records = [
        json.loads(line)
        for line in (log_dir / "smoke_input_override_fork_test.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    input_override_fork_llm_records = [
        item for item in input_override_fork_records if item.get("kind") == "llm"
    ]
    assert [item["output"]["content"] for item in input_override_fork_llm_records] == [
        "live:patched:b",
        "live:live:a|live:patched:b|live:c",
    ], input_override_fork_llm_records
    assert input_override_fork_llm_records[0]["input"]["messages"] == [
        {"content": "patched:b", "role": "user"}
    ]
    assert input_override_fork_llm_records[0]["metadata"]["input_override"] is True
    assert input_override_fork_llm_records[0]["metadata"]["base_record_uid"] == "rec_000002"

    with replay.record("smoke_message_override", log_dir=log_dir):
        await raw_call("manual-tool")

    override_tool_calls = [
        {
            "id": "call_manual_001",
            "type": "function",
            "function": {
                "name": "lookup",
                "arguments": "{\"query\":\"patched\"}",
            },
        }
    ]
    with replay.replay(
        base_run="smoke_message_override",
        breakpoint_record_uid="rec_000001",
        override_message={
            "content": "manual content",
            "tool_calls": override_tool_calls,
        },
        log_dir=log_dir,
        fork_run="smoke_message_override_fork_test",
    ):
        message_override_response = await raw_call("manual-tool")

    message = message_override_response.choices[0].message
    assert message.content == "manual content"
    assert message.tool_calls is not None
    assert message.tool_calls[0].function.name == "lookup"
    assert message.tool_calls[0].function.arguments == "{\"query\":\"patched\"}"
    assert message_override_response.choices[0].finish_reason == "tool_calls"

    message_override_fork_records = [
        json.loads(line)
        for line in (log_dir / "smoke_message_override_fork_test.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    message_override_llm_records = [
        item for item in message_override_fork_records if item.get("kind") == "llm"
    ]
    assert message_override_llm_records[0]["output"]["content"] == "manual content"
    assert (
        message_override_llm_records[0]["output"]["tool_calls"] == override_tool_calls
    )
    assert (
        message_override_llm_records[0]["output"]["raw_response"]["choices"][0][
            "finish_reason"
        ]
        == "tool_calls"
    )

    print("smoke ok", path_ids, forked, dep_recorded, dep_forked)


if __name__ == "__main__":
    AsyncCompletions.create = fake_create
    replay.install()
    try:
        asyncio.run(main())
    finally:
        replay.uninstall()
        AsyncCompletions.create = _ORIGINAL_ASYNC_CREATE
