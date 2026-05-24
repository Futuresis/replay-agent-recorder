from __future__ import annotations

import json
from pathlib import Path

import replay
from replay.integration import ReplayRunConfig, replay_session
from replay.langgraph_patch import wrap_compiled_graph
from replay.storage import run_path


class FakeGraph:
    def invoke(self, input_value, config=None):
        replay.invoke_tool_sync("marker", {"x": input_value["x"]}, lambda: {"ok": True})
        return {"done": True}

    async def ainvoke(self, input_value, config=None):
        await replay.invoke_tool("marker", {"x": input_value["x"]}, lambda: {"ok": True})
        return {"done": True}


def test_auto_session_wraps_langgraph_invoke_without_existing_session(tmp_path: Path) -> None:
    from replay.autosession import AutoSessionConfig, disable_auto_session, enable_auto_session

    wrapped = wrap_compiled_graph(FakeGraph())
    token = enable_auto_session(
        AutoSessionConfig(
            mode="record",
            run_id_template="{graph}-{thread_id}-{input_hash}",
            log_dir=tmp_path,
        )
    )
    try:
        result = wrapped.invoke({"x": 1}, config={"configurable": {"thread_id": "t1"}})
    finally:
        disable_auto_session(token)

    files = list(tmp_path.glob("*.jsonl"))
    assert result == {"done": True}
    assert len(files) == 1
    assert files[0].name.startswith("FakeGraph-t1-")
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert any(record["kind"] == "tool" and record["input"]["tool_name"] == "marker" for record in records)


def test_auto_session_does_not_nest_existing_session(tmp_path: Path) -> None:
    from replay.autosession import AutoSessionConfig, disable_auto_session, enable_auto_session

    wrapped = wrap_compiled_graph(FakeGraph())
    token = enable_auto_session(
        AutoSessionConfig(
            mode="record",
            run_id_template="{thread_id}",
            log_dir=tmp_path,
        )
    )
    try:
        with replay_session(ReplayRunConfig(mode="record", run_id="outer", log_dir=tmp_path)):
            wrapped.invoke({"x": 2}, config={"configurable": {"thread_id": "inner"}})
    finally:
        disable_auto_session(token)

    assert run_path(tmp_path, "outer").exists()
    assert not run_path(tmp_path, "inner").exists()


def test_auto_session_mode_none_is_noop(tmp_path: Path) -> None:
    from replay.autosession import AutoSessionConfig, disable_auto_session, enable_auto_session

    wrapped = wrap_compiled_graph(FakeGraph())
    token = enable_auto_session(AutoSessionConfig(mode="none", log_dir=tmp_path))
    try:
        wrapped.invoke({"x": 3}, config={"configurable": {"thread_id": "t3"}})
    finally:
        disable_auto_session(token)

    assert list(tmp_path.glob("*.jsonl")) == []
