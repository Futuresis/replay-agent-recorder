from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

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
log_dir = Path("replay/tmp-runs-tool-tests")
shutil.rmtree(log_dir, ignore_errors=True)
log_dir.mkdir(parents=True, exist_ok=True)


async def llm_call(text: str) -> str:
    response = await client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": text}],
        temperature=0.7,
    )
    return response.choices[0].message.content


def load_jsonl(run_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (log_dir / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def add(x: int, y: int = 1) -> dict[str, int]:
    return {"value": x + y}


async def async_double(x: int) -> dict[str, int]:
    await asyncio.sleep(0)
    return {"value": x * 2}


async def async_marker(name: str) -> dict[str, str]:
    await asyncio.sleep(0)
    return {"name": name}


def explode(label: str) -> None:
    raise ValueError(f"boom:{label}")


class UnsupportedOutput:
    pass


def bad_output() -> UnsupportedOutput:
    return UnsupportedOutput()


def ordered_output() -> dict[str, int]:
    return {"z": 1, "a": 2}


async def main() -> None:
    def run_add(x: int, y: int = 1) -> dict[str, int]:
        return replay.invoke_tool_sync("add", {"x": x, "y": y}, lambda: add(x, y))

    async def run_double(x: int) -> dict[str, int]:
        return await replay.invoke_tool("double", {"x": x}, lambda: async_double(x))

    def run_explode(label: str) -> None:
        return replay.invoke_tool_sync("explode", {"label": label}, lambda: explode(label))

    def run_bad_output() -> UnsupportedOutput:
        return replay.invoke_tool_sync("bad_output", {}, bad_output)

    def run_ordered_output() -> dict[str, int]:
        return replay.invoke_tool_sync("ordered_output", {}, ordered_output)

    with replay.record("tool_basic", log_dir=log_dir):
        first_llm = await llm_call("before")
        sync_result = run_add(1)
        async_result = await run_double(3)
        second_llm = await llm_call("after")

    assert first_llm == "live:before"
    assert sync_result == {"value": 2}
    assert async_result == {"value": 6}
    assert second_llm == "live:after"

    basic_records = [item for item in load_jsonl("tool_basic") if item.get("kind")]
    assert [(item["kind"], item["path_id"]) for item in basic_records] == [
        ("llm", "root/0"),
        ("tool", "root/tool/0"),
        ("tool", "root/tool/1"),
        ("llm", "root/1"),
    ]

    with replay.replay(base_run="tool_basic", log_dir=log_dir):
        assert await llm_call("before") == "live:before"
        assert run_add(x=1) == {"value": 2}
        assert await run_double(3) == {"value": 6}
        assert await llm_call("after") == "live:after"

    with replay.record("tool_error", log_dir=log_dir):
        try:
            run_explode("x")
        except ValueError as exc:
            assert str(exc) == "boom:x"
        else:
            raise AssertionError("expected ValueError")

    with replay.replay(base_run="tool_error", log_dir=log_dir):
        try:
            run_explode("x")
        except replay.ReplayedToolError as exc:
            assert exc.tool_name == "explode"
            assert exc.original_type == "ValueError"
            assert exc.message == "boom:x"
        else:
            raise AssertionError("expected ReplayedToolError")

    async def run_marker(tool_name: str, name: str) -> dict[str, str]:
        return await replay.invoke_tool(tool_name, {"name": name}, lambda: async_marker(name))

    with replay.record("tool_concurrent", log_dir=log_dir):
        results = await asyncio.gather(
            run_marker("left", "a"),
            run_marker("middle", "b"),
            run_marker("right", "c"),
        )
    assert results == [{"name": "a"}, {"name": "b"}, {"name": "c"}]

    concurrent_records = [item for item in load_jsonl("tool_concurrent") if item.get("kind") == "tool"]
    assert [item["path_id"] for item in concurrent_records] == [
        "root.0/tool/0",
        "root.1/tool/0",
        "root.2/tool/0",
    ]

    with replay.record("tool_breakpoint_validation", log_dir=log_dir):
        await llm_call("start")
        run_add(10)
    try:
        with replay.replay(
            base_run="tool_breakpoint_validation",
            breakpoint_record_uid="rec_000002",
            log_dir=log_dir,
        ):
            pass
    except replay.InvalidBreakpointError:
        pass
    else:
        raise AssertionError("expected InvalidBreakpointError")

    live_counter = {"count": 0}

    def counted_tool(value: str) -> dict[str, Any]:
        live_counter["count"] += 1
        return {"value": value, "count": live_counter["count"]}

    def run_counted(value: str) -> dict[str, Any]:
        return replay.invoke_tool_sync("counted", {"value": value}, lambda: counted_tool(value))

    with replay.record("tool_live_after_llm_breakpoint", log_dir=log_dir):
        await llm_call("break-here")
        run_counted("record")

    live_counter["count"] = 0
    with replay.replay(
        base_run="tool_live_after_llm_breakpoint",
        breakpoint_record_uid="rec_000001",
        override_output="changed",
        log_dir=log_dir,
        fork_run="tool_live_after_llm_breakpoint_fork",
    ):
        assert await llm_call("break-here") == "changed"
        assert run_counted("live") == {"value": "live", "count": 1}

    fork_records = [item for item in load_jsonl("tool_live_after_llm_breakpoint_fork") if item.get("kind")]
    assert [(item["kind"], item["path_id"]) for item in fork_records] == [
        ("llm", "root/0"),
        ("tool", "root/tool/0"),
    ]

    try:
        with replay.record("tool_bad_output", log_dir=log_dir):
            run_bad_output()
    except replay.ToolSerializationError:
        pass
    else:
        raise AssertionError("expected ToolSerializationError")

    with replay.record("tool_output_order", log_dir=log_dir):
        assert list(run_ordered_output().keys()) == ["z", "a"]
    with replay.replay(base_run="tool_output_order", log_dir=log_dir):
        assert list(run_ordered_output().keys()) == ["z", "a"]

    fs_root = log_dir / "fs-sandbox"
    fs_capture = replay.FilesystemCapture(fs_root)
    fs_counter = {"count": 0}

    def rewrite_files() -> dict[str, Any]:
        fs_counter["count"] += 1
        (fs_root / "keep.txt").write_text("after", encoding="utf-8")
        (fs_root / "created").mkdir(parents=True, exist_ok=True)
        (fs_root / "created" / "note.txt").write_text("hello\n", encoding="utf-8")
        (fs_root / "delete.txt").unlink()
        return {"count": fs_counter["count"]}

    def run_rewrite_files() -> dict[str, Any]:
        return replay.invoke_tool_sync(
            "rewrite_files",
            {},
            rewrite_files,
            fs_capture=fs_capture,
        )

    fs_root.mkdir(parents=True, exist_ok=True)
    (fs_root / "keep.txt").write_text("before", encoding="utf-8")
    (fs_root / "delete.txt").write_text("remove me", encoding="utf-8")
    with replay.record("tool_filesystem_effects", log_dir=log_dir):
        assert run_rewrite_files() == {"count": 1}

    fs_records = [item for item in load_jsonl("tool_filesystem_effects") if item.get("kind") == "tool"]
    fs_effect = fs_records[0]["effects"]["filesystem"]
    assert [change["type"] for change in fs_effect["changes"]] == ["create", "delete", "modify"]
    assert [change["path"] for change in fs_effect["changes"]] == [
        "created/note.txt",
        "delete.txt",
        "keep.txt",
    ]

    shutil.rmtree(fs_root)
    fs_root.mkdir(parents=True, exist_ok=True)
    (fs_root / "keep.txt").write_text("before", encoding="utf-8")
    (fs_root / "delete.txt").write_text("remove me", encoding="utf-8")
    fs_counter["count"] = 0
    with replay.replay(base_run="tool_filesystem_effects", log_dir=log_dir):
        assert run_rewrite_files() == {"count": 1}
    assert fs_counter["count"] == 0
    assert (fs_root / "keep.txt").read_text(encoding="utf-8") == "after"
    assert (fs_root / "created" / "note.txt").read_text(encoding="utf-8") == "hello\n"
    assert not (fs_root / "delete.txt").exists()

    shutil.rmtree(fs_root)
    fs_root.mkdir(parents=True, exist_ok=True)
    (fs_root / "keep.txt").write_text("changed by user", encoding="utf-8")
    (fs_root / "delete.txt").write_text("remove me", encoding="utf-8")
    try:
        with replay.replay(base_run="tool_filesystem_effects", log_dir=log_dir):
            run_rewrite_files()
    except replay.FilesystemReplayConflictError:
        pass
    else:
        raise AssertionError("expected FilesystemReplayConflictError")
    assert (fs_root / "keep.txt").read_text(encoding="utf-8") == "changed by user"

    shutil.rmtree(fs_root)
    fs_root.mkdir(parents=True, exist_ok=True)
    (fs_root / "keep.txt").write_text("before", encoding="utf-8")
    (fs_root / "delete.txt").write_text("remove me", encoding="utf-8")
    try:
        with replay.replay(base_run="tool_filesystem_effects", log_dir=log_dir):
            replay.invoke_tool_sync("rewrite_files", {}, rewrite_files)
    except replay.FilesystemCaptureError:
        pass
    else:
        raise AssertionError("expected FilesystemCaptureError")

    dirty_root = log_dir / "fs-dirty-fork"
    dirty_capture = replay.FilesystemCapture(dirty_root)
    dirty_counter = {"write": 0, "append": 0}

    def prepare_dirty_root() -> None:
        shutil.rmtree(dirty_root, ignore_errors=True)
        dirty_root.mkdir(parents=True, exist_ok=True)
        (dirty_root / "state.txt").write_text("base\n", encoding="utf-8")

    def write_seed_file(seed: str) -> dict[str, Any]:
        dirty_counter["write"] += 1
        (dirty_root / "state.txt").write_text(f"seed={seed}\n", encoding="utf-8")
        return {"seed": seed, "count": dirty_counter["write"]}

    def append_constant_file() -> dict[str, Any]:
        dirty_counter["append"] += 1
        with (dirty_root / "state.txt").open("a", encoding="utf-8", newline="") as file:
            file.write("constant append\n")
        return {"count": dirty_counter["append"]}

    def run_write_seed_file(seed: str) -> dict[str, Any]:
        return replay.invoke_tool_sync(
            "write_seed_file",
            {"seed": seed},
            lambda: write_seed_file(seed),
            fs_capture=dirty_capture,
        )

    def run_append_constant_file() -> dict[str, Any]:
        return replay.invoke_tool_sync(
            "append_constant_file",
            {},
            append_constant_file,
            fs_capture=dirty_capture,
        )

    prepare_dirty_root()
    dirty_counter["write"] = 0
    dirty_counter["append"] = 0
    with replay.record("tool_filesystem_dirty_fork", log_dir=log_dir):
        dirty_seed = await llm_call("fs-break")
        assert run_write_seed_file(dirty_seed) == {"seed": "live:fs-break", "count": 1}
        assert run_append_constant_file() == {"count": 1}
    assert (dirty_root / "state.txt").read_text(encoding="utf-8") == (
        "seed=live:fs-break\nconstant append\n"
    )

    prepare_dirty_root()
    dirty_counter["write"] = 0
    dirty_counter["append"] = 0
    with replay.replay(base_run="tool_filesystem_dirty_fork", log_dir=log_dir):
        dirty_seed = await llm_call("fs-break")
        assert dirty_seed == "live:fs-break"
        assert run_write_seed_file(dirty_seed) == {"seed": "live:fs-break", "count": 1}
        assert run_append_constant_file() == {"count": 1}
    assert dirty_counter == {"write": 0, "append": 0}
    assert (dirty_root / "state.txt").read_text(encoding="utf-8") == (
        "seed=live:fs-break\nconstant append\n"
    )

    prepare_dirty_root()
    dirty_counter["write"] = 0
    dirty_counter["append"] = 0
    with replay.replay(
        base_run="tool_filesystem_dirty_fork",
        breakpoint_record_uid="rec_000001",
        override_output="forked-seed",
        log_dir=log_dir,
        fork_run="tool_filesystem_dirty_fork_output",
    ):
        dirty_seed = await llm_call("fs-break")
        assert dirty_seed == "forked-seed"
        assert run_write_seed_file(dirty_seed) == {"seed": "forked-seed", "count": 1}
        assert run_append_constant_file() == {"count": 1}
    assert dirty_counter == {"write": 1, "append": 1}
    assert (dirty_root / "state.txt").read_text(encoding="utf-8") == (
        "seed=forked-seed\nconstant append\n"
    )

    sandbox_base = log_dir / "sandbox-base"
    sandbox_record = log_dir / "managed" / "record-run"
    sandbox_replay = log_dir / "managed" / "replay-run"
    sandbox_base.mkdir(parents=True, exist_ok=True)
    (sandbox_base / "state.txt").write_text("base", encoding="utf-8")
    (sandbox_base / "remove.txt").write_text("remove", encoding="utf-8")
    managed_counter = {"count": 0}

    def managed_tool(root: Path) -> dict[str, Any]:
        managed_counter["count"] += 1
        (root / "state.txt").write_text("changed", encoding="utf-8")
        (root / "created.txt").write_text("new", encoding="utf-8")
        (root / "remove.txt").unlink()
        return {"count": managed_counter["count"]}

    with replay.sandbox(base_root=sandbox_base, work_root=sandbox_record) as root:
        capture = replay.FilesystemCapture(root)
        with replay.record("tool_managed_sandbox", log_dir=log_dir):
            assert replay.invoke_tool_sync(
                "managed_tool",
                {},
                lambda: managed_tool(root),
                fs_capture=capture,
            ) == {"count": 1}
    assert (sandbox_record / "state.txt").read_text(encoding="utf-8") == "changed"
    assert (sandbox_base / "state.txt").read_text(encoding="utf-8") == "base"

    managed_counter["count"] = 0
    with replay.sandbox(base_root=sandbox_base, work_root=sandbox_replay) as root:
        capture = replay.FilesystemCapture(root)
        with replay.replay(base_run="tool_managed_sandbox", log_dir=log_dir):
            assert replay.invoke_tool_sync(
                "managed_tool",
                {},
                lambda: managed_tool(root),
                fs_capture=capture,
            ) == {"count": 1}
        assert (root / "state.txt").read_text(encoding="utf-8") == "changed"
        assert (root / "created.txt").read_text(encoding="utf-8") == "new"
        assert not (root / "remove.txt").exists()
    assert managed_counter["count"] == 0

    (sandbox_replay / "state.txt").write_text("dirty", encoding="utf-8")
    with replay.sandbox(base_root=sandbox_base, work_root=sandbox_replay) as root:
        assert (root / "state.txt").read_text(encoding="utf-8") == "base"
        assert (root / "remove.txt").read_text(encoding="utf-8") == "remove"
        assert not (root / "created.txt").exists()

    try:
        with replay.sandbox(base_root=sandbox_base, work_root=sandbox_base):
            pass
    except replay.SandboxSafetyError:
        pass
    else:
        raise AssertionError("expected SandboxSafetyError")

    managed_capture_work = log_dir / "managed-capture" / "work"
    managed_capture_base = log_dir / "managed-capture-base"
    managed_capture_base.mkdir(parents=True, exist_ok=True)
    (managed_capture_base / "note.txt").write_text("base", encoding="utf-8")
    managed_capture_counter = {"count": 0}

    def run_managed_capture_tool() -> dict[str, Any]:
        managed_capture_counter["count"] += 1
        (managed_capture_work / "note.txt").write_text("updated", encoding="utf-8")
        return {"count": managed_capture_counter["count"]}

    with replay.managed_sandbox(
        base_root=managed_capture_base,
        work_root=managed_capture_work,
    ) as capture:
        assert capture.root == managed_capture_work.resolve()
        with replay.record("tool_managed_sandbox_capture", log_dir=log_dir):
            assert replay.invoke_tool_sync(
                "managed_capture_tool",
                {},
                run_managed_capture_tool,
                fs_capture=capture,
            ) == {"count": 1}

    managed_capture_counter["count"] = 0
    with replay.managed_sandbox(
        base_root=managed_capture_base,
        work_root=managed_capture_work,
    ) as capture:
        assert (managed_capture_work / "note.txt").read_text(encoding="utf-8") == "base"
        with replay.replay(base_run="tool_managed_sandbox_capture", log_dir=log_dir):
            assert replay.invoke_tool_sync(
                "managed_capture_tool",
                {},
                run_managed_capture_tool,
                fs_capture=capture,
            ) == {"count": 1}
    assert managed_capture_counter["count"] == 0
    assert (managed_capture_work / "note.txt").read_text(encoding="utf-8") == "updated"

    direct_counter = {"count": 0}

    async def direct_invoke() -> dict[str, Any]:
        direct_counter["count"] += 1
        await asyncio.sleep(0)
        return {"value": "direct", "count": direct_counter["count"]}

    with replay.record("tool_core_direct", log_dir=log_dir):
        assert await replay.invoke_tool(
            "custom:direct",
            {"query": "alpha"},
            direct_invoke,
            namespace="custom",
            version="v1",
        ) == {"value": "direct", "count": 1}

    direct_counter["count"] = 0
    with replay.replay(base_run="tool_core_direct", log_dir=log_dir):
        assert await replay.invoke_tool(
            "custom:direct",
            {"query": "alpha"},
            direct_invoke,
            namespace="custom",
            version="v1",
        ) == {"value": "direct", "count": 1}
    assert direct_counter["count"] == 0

    core_records = [item for item in load_jsonl("tool_core_direct") if item.get("kind") == "tool"]
    assert core_records[0]["input"] == {
        "tool_name": "custom:direct",
        "arguments": {"query": "alpha"},
        "namespace": "custom",
        "version": "v1",
    }

    registry_counter = {"count": 0}

    def registry_tool(args: dict[str, Any]) -> dict[str, Any]:
        registry_counter["count"] += 1
        return {"echo": args["text"], "count": registry_counter["count"]}

    registry = {"echo": registry_tool}
    registry_adapter = replay.MappingToolAdapter(registry, namespace="registry")
    registry_adapter.install()
    try:
        with replay.record("tool_mapping_adapter", log_dir=log_dir):
            assert registry["echo"]({"text": "hello"}) == {"echo": "hello", "count": 1}

        registry_counter["count"] = 0
        with replay.replay(base_run="tool_mapping_adapter", log_dir=log_dir):
            assert registry["echo"]({"text": "hello"}) == {"echo": "hello", "count": 1}
        assert registry_counter["count"] == 0
    finally:
        registry_adapter.uninstall()
    assert registry["echo"] is registry_tool

    class RpcClient:
        def __init__(self) -> None:
            self.count = 0

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            self.count += 1
            await asyncio.sleep(0)
            return {"tool": name, "arguments": arguments, "count": self.count}

    rpc_client = RpcClient()
    method_adapter = replay.MethodToolAdapter(rpc_client, "call_tool", namespace="rpc")
    method_adapter.install()
    try:
        with replay.record("tool_method_adapter", log_dir=log_dir):
            assert await rpc_client.call_tool("lookup", {"id": 7}) == {
                "tool": "lookup",
                "arguments": {"id": 7},
                "count": 1,
            }

        rpc_client.count = 0
        with replay.replay(base_run="tool_method_adapter", log_dir=log_dir):
            assert await rpc_client.call_tool("lookup", {"id": 7}) == {
                "tool": "lookup",
                "arguments": {"id": 7},
                "count": 1,
            }
        assert rpc_client.count == 0
    finally:
        method_adapter.uninstall()

    class FrameworkToolClient:
        def __init__(self) -> None:
            self.count = 0

        def call_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
            self.count += 1
            return {"tool": name, "kwargs": kwargs, "count": self.count}

    framework_client = FrameworkToolClient()

    def framework_arguments(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(kwargs)

    class_adapter = replay.ClassMethodToolAdapter(
        FrameworkToolClient,
        "call_tool",
        namespace="framework",
        arguments_factory=framework_arguments,
        tool_filter={"search"},
    )
    class_adapter.install()
    try:
        with replay.record("tool_class_method_adapter", log_dir=log_dir):
            assert framework_client.call_tool("search", query="alpha", limit=2) == {
                "tool": "search",
                "kwargs": {"query": "alpha", "limit": 2},
                "count": 1,
            }
            assert framework_client.call_tool("untracked", query="beta") == {
                "tool": "untracked",
                "kwargs": {"query": "beta"},
                "count": 2,
            }

        class_records = [item for item in load_jsonl("tool_class_method_adapter") if item.get("kind") == "tool"]
        assert len(class_records) == 1
        assert class_records[0]["input"]["tool_name"] == "framework:search"
        assert class_records[0]["input"]["arguments"] == {"query": "alpha", "limit": 2}

        framework_client.count = 0
        with replay.replay(base_run="tool_class_method_adapter", log_dir=log_dir):
            assert framework_client.call_tool("search", query="alpha", limit=2) == {
                "tool": "search",
                "kwargs": {"query": "alpha", "limit": 2},
                "count": 1,
            }
            assert framework_client.count == 0
            assert framework_client.call_tool("untracked", query="beta") == {
                "tool": "untracked",
                "kwargs": {"query": "beta"},
                "count": 1,
            }
    finally:
        class_adapter.uninstall()

    async_class_counter = {"count": 0}

    class AsyncFrameworkToolClient:
        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
            async_class_counter["count"] += 1
            await asyncio.sleep(0)
            return {"tool": name, "arguments": arguments or {}, "count": async_class_counter["count"]}

    async_framework_client = AsyncFrameworkToolClient()
    async_class_adapter = replay.ClassMethodToolAdapter(
        AsyncFrameworkToolClient,
        "call_tool",
        namespace="async-framework",
    )
    async_class_adapter.install()
    try:
        with replay.record("tool_async_class_method_adapter", log_dir=log_dir):
            assert await async_framework_client.call_tool("lookup", {"id": 3}) == {
                "tool": "lookup",
                "arguments": {"id": 3},
                "count": 1,
            }

        async_class_counter["count"] = 0
        with replay.replay(base_run="tool_async_class_method_adapter", log_dir=log_dir):
            assert await async_framework_client.call_tool("lookup", {"id": 3}) == {
                "tool": "lookup",
                "arguments": {"id": 3},
                "count": 1,
            }
        assert async_class_counter["count"] == 0
    finally:
        async_class_adapter.uninstall()

    print("tool test ok")


if __name__ == "__main__":
    AsyncCompletions.create = fake_create
    replay.install()
    try:
        asyncio.run(main())
    finally:
        replay.uninstall()
        AsyncCompletions.create = _ORIGINAL_ASYNC_CREATE
        shutil.rmtree(log_dir, ignore_errors=True)
