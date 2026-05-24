from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from replay.entrypoints import (
    MISSING,
    EntryInvocationError,
    ResolvedEntryRef,
    TargetEntry,
    TargetInvocation,
    UnsupportedEntryError,
    add_target_entry_arguments,
    call_factory,
    import_symbol,
    invoke_runnable,
    is_runnable,
    load_json_option,
    maybe_await,
    parse_entry_ref,
    print_entry_result,
    run_target_entry,
    run_target_entry_blocking,
    target_entry_from_args,
    target_invocation_from_args,
)


@contextmanager
def temp_sys_path(path: Path, module_prefixes: tuple[str, ...] = ("pkg",)) -> Iterator[None]:
    old_path = sys.path[:]
    old_modules = {
        name: module
        for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in module_prefixes)
    }
    for name in list(old_modules):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(path))
    try:
        yield
    finally:
        sys.path[:] = old_path
        for name in [
            name
            for name in sys.modules
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in module_prefixes)
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(old_modules)


def write_package(project: Path, body: str) -> None:
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(body, encoding="utf-8")


def test_parse_entry_ref_prefixes_and_inference() -> None:
    assert parse_entry_ref("script:src/main.py") == ResolvedEntryRef(kind="script", ref="src/main.py")
    assert parse_entry_ref("module:pkg.cli") == ResolvedEntryRef(kind="module", ref="pkg.cli")
    assert parse_entry_ref("factory:pkg.agent:build") == ResolvedEntryRef(kind="factory", ref="pkg.agent:build")
    assert parse_entry_ref("runnable:pkg.agent:agent") == ResolvedEntryRef(kind="runnable", ref="pkg.agent:agent")
    assert parse_entry_ref("langgraph.json#agent") == ResolvedEntryRef(
        kind="langgraph-json",
        ref="langgraph.json",
        graph="agent",
    )
    assert parse_entry_ref("./langgraph.json#Deep Researcher") == ResolvedEntryRef(
        kind="langgraph-json",
        ref="./langgraph.json",
        graph="Deep Researcher",
    )
    assert parse_entry_ref("langgraph:agent") == ResolvedEntryRef(
        kind="langgraph-json",
        ref="langgraph.json",
        graph="agent",
    )
    assert parse_entry_ref("pkg.agent:agent") == ResolvedEntryRef(kind="import", ref="pkg.agent:agent")
    assert parse_entry_ref("src/main.py") == ResolvedEntryRef(kind="script", ref="src/main.py")
    assert parse_entry_ref("pkg.cli") == ResolvedEntryRef(kind="module", ref="pkg.cli")


def test_load_json_option_supports_input_values_and_type_checks(tmp_path: Path) -> None:
    assert load_json_option('{"x": 1}', None, label="input") == {"x": 1}
    assert load_json_option("[1, 2]", None, label="input") == [1, 2]
    assert load_json_option('"hello"', None, label="input") == "hello"
    assert load_json_option("null", None, label="input") is None
    assert load_json_option(None, None, label="input") is MISSING

    config_file = tmp_path / "config.json"
    config_file.write_text('{"configurable": {"thread_id": "t1"}}', encoding="utf-8")
    assert load_json_option(None, config_file, label="config", expected_type=dict) == {
        "configurable": {"thread_id": "t1"}
    }

    with pytest.raises(ValueError, match="mutually exclusive"):
        load_json_option("{}", config_file, label="config", expected_type=dict)
    with pytest.raises(ValueError, match="JSON object"):
        load_json_option("[]", None, label="config", expected_type=dict)
    with pytest.raises(ValueError, match="JSON array"):
        load_json_option("{}", None, label="call-args", expected_type=list)


def test_target_entry_and_invocation_from_args_support_legacy_target_script(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(parser)
    args = parser.parse_args(
        [
            "--target-root",
            str(tmp_path),
            "--target-script",
            "agent.py",
            "--input-json",
            "null",
            "--config-json",
            '{"x": 1}',
            "--call-args-json",
            "[1, 2]",
            "--call-kwargs-json",
            '{"flag": true}',
            "--invoke-kwargs-json",
            '{"tags": ["demo"]}',
            "--no-collect-stream",
            "--",
            "--task",
            "hello",
        ]
    )

    entry = target_entry_from_args(args)
    invocation = target_invocation_from_args(args)

    assert entry.kind == "script"
    assert entry.entry == "agent.py"
    assert entry.target_root == tmp_path.resolve()
    assert invocation.input_value is None
    assert invocation.config == {"x": 1}
    assert invocation.call_args == (1, 2)
    assert invocation.call_kwargs == {"flag": True}
    assert invocation.invoke_kwargs == {"tags": ["demo"]}
    assert invocation.target_args == ("--task", "hello")
    assert invocation.collect_stream is False

    conflict_args = parser.parse_args(["--entry", "module:pkg.cli", "--target-script", "agent.py"])
    with pytest.raises(ValueError, match="mutually exclusive"):
        target_entry_from_args(conflict_args)


def test_target_entry_from_args_supports_graph_option(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(parser)

    entry = target_entry_from_args(
        parser.parse_args(
            [
                "--target-root",
                str(tmp_path),
                "--entry",
                "langgraph.json",
                "--graph",
                "agent",
            ]
        )
    )
    assert entry.kind == "langgraph-json"
    assert entry.entry == "langgraph.json"
    assert entry.graph == "agent"

    matching = target_entry_from_args(
        parser.parse_args(["--entry", "langgraph.json#agent", "--graph", "agent"])
    )
    assert matching.graph == "agent"

    conflict = parser.parse_args(["--entry", "langgraph.json#agent", "--graph", "reviewer"])
    with pytest.raises(ValueError, match="--graph.*agent.*reviewer"):
        target_entry_from_args(conflict)


def test_explicit_entry_does_not_inherit_default_langgraph_kind_or_graph() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(
        parser,
        defaults={
            "entry": "langgraph.json#agent",
            "entry_kind": "langgraph-json",
            "graph": "agent",
        },
    )

    entry = target_entry_from_args(parser.parse_args(["--entry", "pkg.agent:agent"]))

    assert entry.kind == "import"
    assert entry.entry == "pkg.agent:agent"
    assert entry.graph is None


def test_defaults_used_when_no_explicit_entry() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(
        parser,
        defaults={
            "entry": "langgraph.json#agent",
            "entry_kind": "langgraph-json",
            "graph": "agent",
        },
    )

    entry = target_entry_from_args(parser.parse_args([]))

    assert entry.kind == "langgraph-json"
    assert entry.entry == "langgraph.json"
    assert entry.graph == "agent"


def test_parser_defaults_remain_visible_for_direct_consumers() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(
        parser,
        defaults={
            "entry": "langgraph.json#agent",
            "entry_kind": "langgraph-json",
            "graph": "agent",
            "method": "invoke",
        },
    )

    args = parser.parse_args([])

    assert args.entry == "langgraph.json#agent"
    assert args.entry_kind == "langgraph-json"
    assert args.graph == "agent"
    assert args.method == "invoke"


def test_explicit_langgraph_entry_does_not_inherit_default_graph_conflict() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(
        parser,
        defaults={
            "entry": "langgraph.json#agent",
            "entry_kind": "langgraph-json",
            "graph": "agent",
        },
    )

    entry = target_entry_from_args(parser.parse_args(["--entry", "langgraph.json#reviewer"]))

    assert entry.kind == "langgraph-json"
    assert entry.entry == "langgraph.json"
    assert entry.graph == "reviewer"


def test_explicit_entry_does_not_inherit_default_method() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(
        parser,
        defaults={
            "entry": "langgraph.json#agent",
            "entry_kind": "langgraph-json",
            "graph": "agent",
            "method": "invoke",
        },
    )

    entry = target_entry_from_args(parser.parse_args(["--entry", "pkg.agent:agent"]))

    assert entry.method == "auto"


def test_target_script_preserves_legacy_behavior_with_defaults() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(
        parser,
        defaults={
            "entry": "langgraph.json#agent",
            "entry_kind": "langgraph-json",
            "graph": "agent",
        },
    )

    entry = target_entry_from_args(parser.parse_args(["--target-script", "agent.py"]))

    assert entry.kind == "script"
    assert entry.entry == "agent.py"
    assert entry.graph is None


def test_script_entry_runs_with_target_args(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = project / "script.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(__file__).with_name('out.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )

    asyncio.run(
        run_target_entry(
            TargetEntry(kind="script", entry="script.py", target_root=project),
            TargetInvocation(target_args=("a", "b")),
        )
    )

    assert (project / "out.txt").read_text(encoding="utf-8") == "a b"


def test_module_entry_runs_like_python_m(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(__file__).with_name('module_out.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )

    with temp_sys_path(project):
        asyncio.run(
            run_target_entry(
                TargetEntry(kind="module", entry="pkg.cli", target_root=project),
                TargetInvocation(target_args=("x", "y")),
            )
        )

    assert (package / "module_out.txt").read_text(encoding="utf-8") == "x y"


def test_import_symbol_supports_nested_attributes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "class Holder:\n"
        "    class nested:\n"
        "        value = 42\n",
    )

    with temp_sys_path(project):
        assert import_symbol("pkg.agent:Holder.nested.value", target_root=project) == 42


def test_runnable_object_sync_and_async_auto_methods(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "class FakeRunnable:\n"
        "    def invoke(self, input, config=None, **kwargs):\n"
        "        return {'method': 'invoke', 'input': input, 'config': config, 'kwargs': kwargs}\n"
        "\n"
        "class FakeAsyncRunnable:\n"
        "    async def ainvoke(self, input, config=None, **kwargs):\n"
        "        return {'method': 'ainvoke', 'input': input, 'config': config, 'kwargs': kwargs}\n"
        "\n"
        "agent = FakeRunnable()\n"
        "async_agent = FakeAsyncRunnable()\n",
    )

    with temp_sys_path(project):
        sync_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="runnable", entry="pkg.agent:agent", target_root=project),
                TargetInvocation(
                    input_value={"hello": "sync"},
                    config={"configurable": {"thread_id": "1"}},
                    invoke_kwargs={"tags": ["demo"]},
                ),
            )
        )
        async_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="runnable", entry="pkg.agent:async_agent", target_root=project),
                TargetInvocation(input_value={"hello": "async"}),
            )
        )

    assert sync_result == {
        "method": "invoke",
        "input": {"hello": "sync"},
        "config": {"configurable": {"thread_id": "1"}},
        "kwargs": {"tags": ["demo"]},
    }
    assert async_result["method"] == "ainvoke"
    assert async_result["input"] == {"hello": "async"}


def test_factory_sync_async_and_config_shape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "class FakeRunnable:\n"
        "    def __init__(self, factory_config=None):\n"
        "        self.factory_config = factory_config\n"
        "    def invoke(self, input, config=None, **kwargs):\n"
        "        return {'input': input, 'config': config, 'factory_config': self.factory_config}\n"
        "\n"
        "class FakeAsyncRunnable(FakeRunnable):\n"
        "    async def ainvoke(self, input, config=None, **kwargs):\n"
        "        return {'input': input, 'config': config, 'factory_config': self.factory_config}\n"
        "\n"
        "def build_agent():\n"
        "    return FakeRunnable()\n"
        "\n"
        "async def get_agent(config):\n"
        "    return FakeAsyncRunnable(config)\n",
    )

    with temp_sys_path(project):
        sync_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="factory", entry="pkg.agent:build_agent", target_root=project),
                TargetInvocation(input_value={"x": 1}),
            )
        )
        async_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="factory", entry="pkg.agent:get_agent", target_root=project),
                TargetInvocation(
                    input_value={"x": 2},
                    config={"runtime": True},
                    factory_config={"factory": True},
                ),
            )
        )

    assert sync_result == {"input": {"x": 1}, "config": None, "factory_config": None}
    assert async_result == {
        "input": {"x": 2},
        "config": {"runtime": True},
        "factory_config": {"factory": True},
    }


def test_call_factory_passes_explicit_factory_config_to_optional_config_param() -> None:
    seen: list[dict[str, object] | None] = []

    def build_agent(config=None):
        seen.append(config)
        return object()

    asyncio.run(
        call_factory(
            build_agent,
            TargetInvocation(factory_config={"configurable": {"thread_id": "t1"}}),
        )
    )

    assert seen == [{"configurable": {"thread_id": "t1"}}]


def test_call_factory_passes_explicit_factory_config_to_optional_runnable_config_param() -> None:
    seen: list[dict[str, object] | None] = []

    def build_agent(runnable_config=None):
        seen.append(runnable_config)
        return object()

    asyncio.run(
        call_factory(
            build_agent,
            TargetInvocation(factory_config={"configurable": {"thread_id": "t1"}}),
        )
    )

    assert seen == [{"configurable": {"thread_id": "t1"}}]


def test_call_factory_passes_explicit_factory_config_to_keyword_only_config_param() -> None:
    seen: list[dict[str, object] | None] = []

    def build_agent(*, config=None):
        seen.append(config)
        return object()

    asyncio.run(
        call_factory(
            build_agent,
            TargetInvocation(factory_config={"configurable": {"thread_id": "t1"}}),
        )
    )

    assert seen == [{"configurable": {"thread_id": "t1"}}]


def test_call_factory_optional_config_param_still_calls_without_args_when_no_config_provided() -> None:
    seen: list[dict[str, object] | None] = []

    def build_agent(config=None):
        seen.append(config)
        return object()

    asyncio.run(call_factory(build_agent, TargetInvocation()))

    assert seen == [None]


def test_call_factory_optional_config_param_falls_back_to_invocation_config() -> None:
    seen: list[dict[str, object] | None] = []

    def build_agent(config=None):
        seen.append(config)
        return object()

    asyncio.run(
        call_factory(
            build_agent,
            TargetInvocation(config={"configurable": {"thread_id": "t1"}}),
        )
    )

    assert seen == [{"configurable": {"thread_id": "t1"}}]


def test_call_factory_explicit_call_args_or_kwargs_bypass_auto_config_injection() -> None:
    seen_args: list[tuple[object, ...]] = []
    seen_kwargs: list[dict[str, object]] = []

    def build_with_args(*args):
        seen_args.append(args)
        return object()

    def build_with_kwargs(**kwargs):
        seen_kwargs.append(kwargs)
        return object()

    asyncio.run(
        call_factory(
            build_with_args,
            TargetInvocation(
                factory_config={"configurable": {"thread_id": "t1"}},
                call_args=("explicit",),
            ),
        )
    )
    asyncio.run(
        call_factory(
            build_with_kwargs,
            TargetInvocation(
                factory_config={"configurable": {"thread_id": "t1"}},
                call_kwargs={"mode": "explicit"},
            ),
        )
    )

    assert seen_args == [("explicit",)]
    assert seen_kwargs == [{"mode": "explicit"}]


def test_import_callable_uses_input_and_invokes_returned_runnable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "def main(value):\n"
        "    return {'got': value}\n"
        "\n"
        "class FakeRunnable:\n"
        "    def invoke(self, input, **kwargs):\n"
        "        return {'from_runnable': input}\n"
        "\n"
        "def make_runnable(value):\n"
        "    return FakeRunnable()\n",
    )

    with temp_sys_path(project):
        callable_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="import", entry="pkg.agent:main", target_root=project),
                TargetInvocation(input_value={"x": 1}),
            )
        )
        runnable_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="import", entry="pkg.agent:make_runnable", target_root=project),
                TargetInvocation(input_value={"x": 2}),
            )
        )

    assert callable_result == {"got": {"x": 1}}
    assert runnable_result == {"from_runnable": {"x": 2}}


def test_import_callable_injects_factory_config_for_run_config_param(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "def build(run_config=None):\n"
        "    return {'run_config': run_config}\n",
    )

    with temp_sys_path(project):
        result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="import", entry="pkg.agent:build", target_root=project),
                TargetInvocation(factory_config={"x": 1}),
            )
        )

    assert result == {"run_config": {"x": 1}}


def test_run_target_entry_resolves_auto_kind_for_imports(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "class FakeRunnable:\n"
        "    def invoke(self, input, **kwargs):\n"
        "        return {'auto': input}\n"
        "\n"
        "agent = FakeRunnable()\n",
    )

    with temp_sys_path(project):
        result = asyncio.run(
            run_target_entry(
                TargetEntry(entry="pkg.agent:agent", kind="auto", target_root=project),
                TargetInvocation(input_value={"x": 3}),
            )
        )

    assert result == {"auto": {"x": 3}}


def test_blocking_invoke_does_not_run_inside_event_loop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "import asyncio\n"
        "\n"
        "class Agent:\n"
        "    def invoke(self, input, config=None, **kwargs):\n"
        "        async def inner():\n"
        "            return {'ok': input}\n"
        "        return asyncio.run(inner())\n"
        "\n"
        "agent = Agent()\n",
    )

    with temp_sys_path(project):
        result = run_target_entry_blocking(
            TargetEntry(
                kind="import",
                entry="pkg.agent:agent",
                target_root=project,
                method="invoke",
            ),
            TargetInvocation(input_value={"x": 1}),
        )

    assert result == {"ok": {"x": 1}}


def test_blocking_auto_import_sync_invoke_does_not_run_inside_event_loop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "import asyncio\n"
        "\n"
        "class Agent:\n"
        "    def invoke(self, input, config=None, **kwargs):\n"
        "        async def inner():\n"
        "            return {'ok': input}\n"
        "        return asyncio.run(inner())\n"
        "\n"
        "agent = Agent()\n",
    )

    with temp_sys_path(project):
        result = run_target_entry_blocking(
            TargetEntry(
                kind="import",
                entry="pkg.agent:agent",
                target_root=project,
                method="auto",
            ),
            TargetInvocation(input_value={"x": 1}),
        )

    assert result == {"ok": {"x": 1}}


def test_blocking_auto_factory_sync_invoke_does_not_run_inside_event_loop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "import asyncio\n"
        "\n"
        "class Agent:\n"
        "    def invoke(self, input, config=None, **kwargs):\n"
        "        async def inner():\n"
        "            return {'ok': input}\n"
        "        return asyncio.run(inner())\n"
        "\n"
        "def build_agent():\n"
        "    return Agent()\n",
    )

    with temp_sys_path(project):
        result = run_target_entry_blocking(
            TargetEntry(
                kind="factory",
                entry="pkg.agent:build_agent",
                target_root=project,
                method="auto",
            ),
            TargetInvocation(input_value={"x": 2}),
        )

    assert result == {"ok": {"x": 2}}


def test_blocking_stream_collects_sync_stream_without_outer_event_loop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "import asyncio\n"
        "\n"
        "class Agent:\n"
        "    def stream(self, input, config=None, **kwargs):\n"
        "        async def inner(index):\n"
        "            return {'chunk': index, 'input': input}\n"
        "        yield asyncio.run(inner(1))\n"
        "        yield asyncio.run(inner(2))\n"
        "\n"
        "agent = Agent()\n",
    )

    with temp_sys_path(project):
        result = run_target_entry_blocking(
            TargetEntry(
                kind="import",
                entry="pkg.agent:agent",
                target_root=project,
                method="stream",
            ),
            TargetInvocation(input_value={"x": 1}),
        )

    assert result == [
        {"chunk": 1, "input": {"x": 1}},
        {"chunk": 2, "input": {"x": 1}},
    ]


def test_blocking_ainvoke_still_uses_async_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_package(
        project,
        "class Agent:\n"
        "    async def ainvoke(self, input, config=None, **kwargs):\n"
        "        return {'method': 'ainvoke', 'input': input}\n"
        "\n"
        "agent = Agent()\n",
    )

    with temp_sys_path(project):
        result = run_target_entry_blocking(
            TargetEntry(
                kind="import",
                entry="pkg.agent:agent",
                target_root=project,
                method="ainvoke",
            ),
            TargetInvocation(input_value={"x": 1}),
        )

    assert result == {"method": "ainvoke", "input": {"x": 1}}


def test_run_target_entry_executes_langgraph_json_runnable_path_ref(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "my_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "class FakeGraph:\n"
        "    async def ainvoke(self, input, config=None):\n"
        "        return {'input': input, 'config': config}\n"
        "\n"
        "graph = FakeGraph()\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"G": "./src/my_pkg/graph.py:graph"}}),
        encoding="utf-8",
    )

    result = asyncio.run(
        run_target_entry(
            TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="G", target_root=project),
            TargetInvocation(
                input_value={"messages": []},
                config={"configurable": {"thread_id": "t"}},
            ),
        )
    )

    assert result == {
        "input": {"messages": []},
        "config": {"configurable": {"thread_id": "t"}},
    }


def test_run_target_entry_executes_langgraph_json_non_package_path_ref_via_file_import(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    graph_dir = project / "weird.dir"
    graph_dir.mkdir(parents=True)
    (graph_dir / "agent.py").write_text(
        "class FakeGraph:\n"
        "    async def ainvoke(self, input, config=None):\n"
        "        return {'input': input, 'config': config, 'source': __file__}\n"
        "\n"
        "graph = FakeGraph()\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "./weird.dir/agent.py:graph"}}),
        encoding="utf-8",
    )

    result = asyncio.run(
        run_target_entry(
            TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="agent", target_root=project),
            TargetInvocation(
                input_value={"messages": []},
                config={"configurable": {"thread_id": "t"}},
            ),
        )
    )

    assert result == {
        "input": {"messages": []},
        "config": {"configurable": {"thread_id": "t"}},
        "source": str((graph_dir / "agent.py").resolve()),
    }


def test_run_target_entry_langgraph_json_file_import_missing_symbol_mentions_path_and_symbol(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    graph_dir = project / "weird.dir"
    graph_dir.mkdir(parents=True)
    (graph_dir / "agent.py").write_text("graph = object()\n", encoding="utf-8")
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "./weird.dir/agent.py:missing"}}),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="missing"):
        asyncio.run(
            run_target_entry(
                TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="agent", target_root=project),
                TargetInvocation(input_value={"messages": []}),
            )
        )

        with pytest.raises(Exception, match=str((graph_dir / "agent.py").resolve()).replace(".", r"\.")):
            asyncio.run(
                run_target_entry(
                    TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="agent", target_root=project),
                    TargetInvocation(input_value={"messages": []}),
                )
            )


def test_run_target_entry_blocking_executes_langgraph_json_non_package_path_ref_via_file_import(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    graph_dir = project / "weird.dir"
    graph_dir.mkdir(parents=True)
    (graph_dir / "agent.py").write_text(
        "class FakeGraph:\n"
        "    def invoke(self, input, config=None):\n"
        "        return {'input': input, 'config': config, 'source': __file__}\n"
        "\n"
        "graph = FakeGraph()\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "./weird.dir/agent.py:graph"}}),
        encoding="utf-8",
    )

    result = run_target_entry_blocking(
        TargetEntry(
            kind="langgraph-json",
            entry="langgraph.json",
            graph="agent",
            target_root=project,
            method="invoke",
        ),
        TargetInvocation(
            input_value={"messages": []},
            config={"configurable": {"thread_id": "t"}},
        ),
    )

    assert result == {
        "input": {"messages": []},
        "config": {"configurable": {"thread_id": "t"}},
        "source": str((graph_dir / "agent.py").resolve()),
    }


def test_run_target_entry_blocking_auto_executes_langgraph_json_sync_invoke_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    graph_dir = project / "weird.dir"
    graph_dir.mkdir(parents=True)
    (graph_dir / "agent.py").write_text(
        "import asyncio\n"
        "\n"
        "class FakeGraph:\n"
        "    def invoke(self, input, config=None):\n"
        "        async def inner():\n"
        "            return {'ok': input}\n"
        "        return asyncio.run(inner())\n"
        "\n"
        "graph = FakeGraph()\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "./weird.dir/agent.py:graph"}}),
        encoding="utf-8",
    )

    result = run_target_entry_blocking(
        TargetEntry(
            kind="langgraph-json",
            entry="langgraph.json",
            graph="agent",
            target_root=project,
            method="auto",
        ),
        TargetInvocation(input_value={"x": 3}),
    )

    assert result == {"ok": {"x": 3}}


def test_run_target_entry_langgraph_json_path_ref_isolates_module_cache(tmp_path: Path) -> None:
    def write_project(name: str, marker: str) -> Path:
        project = tmp_path / name
        package = project / "src" / "my_pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "graph.py").write_text(
            "class FakeGraph:\n"
            "    async def ainvoke(self, input, config=None):\n"
            f"        return {{'marker': {marker!r}}}\n"
            "\n"
            "graph = FakeGraph()\n",
            encoding="utf-8",
        )
        (project / "langgraph.json").write_text(
            json.dumps({"graphs": {"G": "./src/my_pkg/graph.py:graph"}}),
            encoding="utf-8",
        )
        return project

    first = write_project("first", "one")
    second = write_project("second", "two")

    try:
        first_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="G", target_root=first),
                TargetInvocation(input_value={}),
            )
        )
        second_result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="G", target_root=second),
                TargetInvocation(input_value={}),
            )
        )
    finally:
        for name in [name for name in sys.modules if name == "my_pkg" or name.startswith("my_pkg.")]:
            sys.modules.pop(name, None)

    assert first_result == {"marker": "one"}
    assert second_result == {"marker": "two"}


def test_run_target_entry_executes_langgraph_json_async_factory_module_ref(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "server.py").write_text(
        "class FakeGraph:\n"
        "    def __init__(self, factory_config):\n"
        "        self.factory_config = factory_config\n"
        "    async def ainvoke(self, input, config=None):\n"
        "        return {'factory_config': self.factory_config, 'input': input, 'config': config}\n"
        "\n"
        "async def get_agent(config):\n"
        "    return FakeGraph(config)\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.server:get_agent"}}),
        encoding="utf-8",
    )

    with temp_sys_path(project, module_prefixes=("agent",)):
        result = asyncio.run(
            run_target_entry(
                TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="agent", target_root=project),
                TargetInvocation(
                    input_value={"messages": [{"role": "user", "content": "hello"}]},
                    config={"runtime": True},
                    factory_config={"factory": True},
                ),
            )
        )

    assert result == {
        "factory_config": {"factory": True},
        "input": {"messages": [{"role": "user", "content": "hello"}]},
        "config": {"runtime": True},
    }


def test_invoke_runnable_validates_missing_input_config_conflicts_and_streams() -> None:
    class StreamRunnable:
        def stream(self, input, **kwargs):
            yield {"chunk": 1, "input": input, "kwargs": kwargs}
            yield {"chunk": 2, "input": input, "kwargs": kwargs}

    assert is_runnable(StreamRunnable())
    with pytest.raises(EntryInvocationError, match="Pass --input-json or --input-file"):
        asyncio.run(invoke_runnable(StreamRunnable(), "auto", TargetInvocation()))
    with pytest.raises(EntryInvocationError, match="config"):
        asyncio.run(
            invoke_runnable(
                StreamRunnable(),
                "stream",
                TargetInvocation(
                    input_value={"x": 1},
                    config={"a": 1},
                    invoke_kwargs={"config": {"b": 2}},
                ),
            )
        )

    chunks = asyncio.run(
        invoke_runnable(
            StreamRunnable(),
            "auto",
            TargetInvocation(input_value={"x": 1}, invoke_kwargs={"metadata": {"m": 1}}),
        )
    )
    assert chunks == [
        {"chunk": 1, "input": {"x": 1}, "kwargs": {"metadata": {"m": 1}}},
        {"chunk": 2, "input": {"x": 1}, "kwargs": {"metadata": {"m": 1}}},
    ]


def test_maybe_await_and_unsupported_entry_errors() -> None:
    async def make_value() -> int:
        return 3

    assert asyncio.run(maybe_await(make_value())) == 3
    assert asyncio.run(maybe_await(4)) == 4

    with pytest.raises(UnsupportedEntryError, match="method=serve"):
        asyncio.run(invoke_runnable(StreamlessRunnable(), "serve", TargetInvocation(input_value={})))


class StreamlessRunnable:
    def invoke(self, input, **kwargs):
        return input


def test_print_entry_result_writes_json_and_repr_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    print_entry_result({"b": 2, "a": 1}, output_file=output)

    assert json.loads(output.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert json.loads(capsys.readouterr().out) == {"a": 1, "b": 2}

    class BrokenReprOnly:
        def __repr__(self) -> str:
            return "<repr-only>"

    import replay.normalization

    monkeypatch.setattr(
        replay.normalization,
        "normalize_for_json",
        lambda _value: (_ for _ in ()).throw(RuntimeError("normalization failed")),
    )
    print_entry_result(BrokenReprOnly())
    assert "<repr-only>" in capsys.readouterr().out
