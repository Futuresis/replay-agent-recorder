from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("openai")

import replay
from replay.edges import build_orchestration_graph, load_graph_records
from replay.import_hook import install_import_hook, uninstall_import_hook
from replay.semantic_runtime import RUNTIME, Source, _WeakProvenanceEntry
from replay.storage import load_replay_records
from openai.resources.chat.completions.completions import Completions
from openai.types.chat.chat_completion import ChatCompletion


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_agent_module(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "agent_ast_tools.py").write_text(
        '''
import replay

calls = []

def first_tool():
    calls.append("first")
    return "alpha"

def second_tool(value):
    calls.append(("second", value))
    return f"beta:{value}"

def branch_tool():
    calls.append("branch")
    return "branched"

def run_flow():
    first = replay.invoke_tool_sync("first", {}, first_tool)
    second = replay.invoke_tool_sync("second", {"value": first}, lambda: second_tool(first))
    branch = None
    if first == "alpha":
        branch = replay.invoke_tool_sync("branch", {}, branch_tool)
    return first, second, branch
''',
        encoding="utf-8",
    )


def write_llm_fork_module(project_root: Path) -> None:
    (project_root / "agent_ast_llm_fork.py").write_text(
        '''
from openai import OpenAI

client = OpenAI(api_key="test", base_url="http://example.invalid/v1")

def ask(prompt):
    response = client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content

def run_flow():
    a = ask("A")
    b = ask("B")
    c = ask("C")
    parent = ask(f"parent uses {a}|{b}|{c}")
    return [a, b, c], parent
''',
        encoding="utf-8",
    )


def write_comprehension_module(project_root: Path) -> None:
    (project_root / "agent_ast_comprehensions.py").write_text(
        '''
import replay

calls = []

def first_tool():
    return ["alpha", "beta", "gamma"]

def second_tool(messages):
    calls.append(messages)
    return "ok"

def run_flow():
    themes = replay.invoke_tool_sync("first", {}, first_tool)
    tasks = [
        {"index": index, "theme": theme}
        for index, theme in enumerate(themes, start=1)
        if theme.strip()
    ]
    messages = []
    for task in tasks:
        messages.append({"content": f"theme {task['theme']}"})
    joined = "\\n".join(
        f"{index}:{message['content']}"
        for index, message in enumerate(messages, start=1)
    )
    return replay.invoke_tool_sync("second", {"joined": joined}, lambda: second_tool(messages))
''',
        encoding="utf-8",
    )


def write_expression_control_module(project_root: Path) -> None:
    (project_root / "agent_ast_expression_control.py").write_text(
        '''
import replay

calls = []

def emit(name, value):
    calls.append(name)
    return value

def sink(name, value):
    calls.append((name, value))
    return value

def run_flow():
    gate_or = replay.invoke_tool_sync("gate_or", {}, lambda: emit("gate_or", ""))
    fallback_or = gate_or or replay.invoke_tool_sync("fallback_or", {}, lambda: emit("fallback_or", "fallback"))
    sink_or = replay.invoke_tool_sync("sink_or", {"value": f"or:{fallback_or}"}, lambda: sink("sink_or", fallback_or))

    gate_and = replay.invoke_tool_sync("gate_and", {}, lambda: emit("gate_and", ""))
    skipped_and = gate_and and replay.invoke_tool_sync("skipped_and", {}, lambda: emit("skipped_and", "should-not-run"))
    sink_and = replay.invoke_tool_sync("sink_and", {"value": f"and:{skipped_and}"}, lambda: sink("sink_and", skipped_and))

    selector = replay.invoke_tool_sync("selector", {}, lambda: emit("selector", True))
    choice = "left" if selector else "right"
    sink_choice = replay.invoke_tool_sync("sink_ifexp", {"value": f"choice:{choice}"}, lambda: sink("sink_ifexp", choice))

    flags = replay.invoke_tool_sync("flags", {}, lambda: emit("flags", [True, False, True]))
    selected = ["selected" for flag in flags if flag]
    sink_comp = replay.invoke_tool_sync("sink_comp_filter", {"value": ",".join(selected)}, lambda: sink("sink_comp_filter", selected))

    return sink_or, sink_and, sink_choice, sink_comp
''',
        encoding="utf-8",
    )


def write_keyword_collision_module(project_root: Path) -> None:
    (project_root / "agent_ast_keyword_collisions.py").write_text(
        '''
class ToolLike:
    def method(self, name=None, obj=None, args=None, kwargs=None, arguments=None):
        return {
            "name": name,
            "obj": obj,
            "args": args,
            "kwargs": kwargs,
            "arguments": arguments,
        }


def plain(fn=None, args=None, kwargs=None, arguments=None, obj=None, name=None):
    return {
        "fn": fn,
        "args": args,
        "kwargs": kwargs,
        "arguments": arguments,
        "obj": obj,
        "name": name,
    }


def run_flow():
    return {
        "plain": plain(
            fn="fn-value",
            args={"pos": 1},
            kwargs={"kw": 2},
            arguments={"tool": 3},
            obj="obj-value",
            name="name-value",
        ),
        "method": ToolLike().method(
            name="method-name",
            obj="method-obj",
            args={"method": "args"},
            kwargs={"method": "kwargs"},
            arguments={"method": "arguments"},
        ),
        "format": "{template}:{args}:{kwargs}:{arguments}:{name}".format(
            template="template-value",
            args="args-value",
            kwargs="kwargs-value",
            arguments="arguments-value",
            name="name-value",
        ),
        "join": "|".join(["a", "b"]),
    }
''',
        encoding="utf-8",
    )


def make_completion(model: str, prompt: str, content: str) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": f"fake-{prompt}",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


def source_for(run_id: str, record: dict) -> dict:
    return {
        "run_id": run_id,
        "record_uid": record["record_uid"],
        "kind": record["kind"],
        "path_id": record["path_id"],
    }


def assert_runtime_contexts_are_isolated() -> None:
    source_a = Source(run_id="A", record_uid="rec_a", kind="llm", path_id="root/0")
    source_b = Source(run_id="B", record_uid="rec_b", kind="llm", path_id="root/0")

    token_a = RUNTIME.enter_context(enabled=True)
    try:
        value_a = RUNTIME.seed_value("alpha", source_a)
        assert RUNTIME.get_provenance(value_a) == {source_a}

        token_b = RUNTIME.enter_context(enabled=True)
        try:
            assert RUNTIME.get_provenance(value_a) == set()
            value_b = RUNTIME.seed_value("beta", source_b)
            assert RUNTIME.get_provenance(value_b) == {source_b}
            RUNTIME.reset(enabled=True)
            assert RUNTIME.get_provenance(value_b) == set()
        finally:
            RUNTIME.exit_context(token_b)

        assert RUNTIME.get_provenance(value_a) == {source_a}
    finally:
        RUNTIME.exit_context(token_a)


def assert_provenance_is_lifecycle_safe() -> None:
    source = Source(run_id="lifecycle", record_uid="rec_old", kind="tool", path_id="root/tool/0")
    other = Source(run_id="lifecycle", record_uid="rec_new", kind="tool", path_id="root/tool/1")

    token = RUNTIME.enter_context(enabled=True)
    try:
        value = RUNTIME.seed_value("alpha", source)
        assert RUNTIME.get_provenance(value) == {source}
        assert RUNTIME.get_provenance("alpha") == set()
        RUNTIME.reset(enabled=True)
        assert RUNTIME.get_provenance(value) == set()
        assert RUNTIME.get_provenance(RUNTIME.seed_value("alpha", other)) == {other}

        class WeakObject:
            pass

        stale_target = WeakObject()
        live_target = WeakObject()
        stale_entry = _WeakProvenanceEntry({source})
        stale_entry.ref = lambda: stale_target
        RUNTIME._state().weak_provenance[id(live_target)] = stale_entry
        assert RUNTIME.get_provenance(live_target) == set()

        tracked_list = RUNTIME.pack([])
        tracked_dict = RUNTIME.pack({})
        tracked_set = RUNTIME.pack(set())
        assert type(tracked_list) is list
        assert type(tracked_dict) is dict
        assert type(tracked_set) is set
        tracked_item = RUNTIME.seed_value("item", other)
        RUNTIME.call_method(tracked_list, "append", tracked_item)
        RUNTIME.call_method(tracked_dict, "update", {"item": tracked_item})
        RUNTIME.call_method(tracked_set, "add", tracked_item)
        assert RUNTIME.get_provenance(tracked_list) == {other}
        assert RUNTIME.get_provenance(tracked_dict) == {other}
        assert RUNTIME.get_provenance(tracked_set) == {other}
        assert RUNTIME.plain_value(tracked_list) == ["item"]
        assert RUNTIME.plain_value(tracked_dict) == {"item": "item"}
        assert RUNTIME.plain_value(tracked_set) == {"item"}
    finally:
        RUNTIME.exit_context(token)


def assert_runtime_wrapping_is_transparent(scratch: Path) -> None:
    source = Source(run_id="transparent", record_uid="rec_file", kind="tool", path_id="root/tool/0")
    tool_source = Source(run_id="transparent", record_uid="rec_tool", kind="tool", path_id="root/tool/1")

    token = RUNTIME.enter_context(enabled=True)
    try:
        messages: list[dict] = []
        tracked_messages = RUNTIME.seed_value(messages, source)
        assert tracked_messages is messages

        class Conversation:
            def __init__(self) -> None:
                self.messages = messages

        conversation = Conversation()
        attr_messages = RUNTIME.attr(conversation, "messages")
        assert attr_messages is messages
        tool_message = RUNTIME.seed_value({"role": "tool", "content": "result"}, tool_source)
        RUNTIME.call_method(attr_messages, "append", tool_message)
        assert conversation.messages == [tool_message]
        assert tool_source in RUNTIME.get_provenance(conversation.messages)
        captured = RUNTIME.capture_input_provenance({"messages": conversation.messages})
        assert tool_source in captured["data_sources"]

        record_path = scratch / "record.txt"
        handle = record_path.open("w", encoding="utf-8")
        try:
            tracked_handle = RUNTIME.seed_value(handle, source)
            assert tracked_handle is handle
            tracked_handle.write("alpha")
        finally:
            handle.close()
        assert record_path.read_text(encoding="utf-8") == "alpha"

        context_path = scratch / "context-record.txt"
        with RUNTIME.seed_value(context_path.open("w", encoding="utf-8"), source) as record_file:
            assert hasattr(record_file, "write")
            record_file.write("beta")
        assert context_path.read_text(encoding="utf-8") == "beta"

        class CursorLike:
            def __init__(self) -> None:
                self.items = iter(["first"])

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.items)

            def extra_method(self):
                return "extra"

        cursor = CursorLike()
        tracked_cursor = RUNTIME.seed_value(cursor, source)
        assert tracked_cursor is cursor
        assert tracked_cursor.extra_method() == "extra"
        assert next(tracked_cursor) == "first"
        assert RUNTIME.get_provenance(tracked_cursor) == {source}

        number_range = range(3)
        tracked_range = RUNTIME.seed_value(number_range, source)
        assert tracked_range is number_range
        assert list(tracked_range) == [0, 1, 2]

        def external_append(items, item):
            items.append(item)

        external_items: list[str] = []
        tracked_item = RUNTIME.seed_value("inserted", tool_source)
        RUNTIME.call(external_append, external_items, tracked_item)
        assert external_items == ["inserted"]

        mapping: dict[str, str] = {}
        RUNTIME.setitem(mapping, RUNTIME.seed_value("key", source), RUNTIME.seed_value("value", tool_source))
        assert mapping == {"key": "value"}
        assert tool_source in RUNTIME.get_provenance(mapping)
        RUNTIME.delitem(mapping, "key")
        assert mapping == {}
    finally:
        RUNTIME.exit_context(token)


def main() -> None:
    base = Path("tmp-runs-ast-provenance")
    shutil.rmtree(base, ignore_errors=True)
    project_root = base / "project"
    log_dir = base / "runs"
    write_agent_module(project_root)
    write_llm_fork_module(project_root)
    write_comprehension_module(project_root)
    write_expression_control_module(project_root)
    write_keyword_collision_module(project_root)

    original_create = Completions.create
    phase = {"prefix": "base"}

    def fake_create(self, *args, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        return make_completion(kwargs["model"], prompt, f"{phase['prefix']}:{prompt}")

    Completions.create = fake_create
    replay.install(semantic=False)
    token = install_import_hook(project_root)
    sys.path.insert(0, str(project_root))
    try:
        assert_runtime_contexts_are_isolated()
        assert_provenance_is_lifecycle_safe()
        assert_runtime_wrapping_is_transparent(base)

        module = importlib.import_module("agent_ast_tools")

        with replay.record("semantic_tools", log_dir=log_dir):
            assert module.run_flow() == ("alpha", "beta:alpha", "branched")

        records = read_jsonl(log_dir / "semantic_tools.jsonl")
        primary = [record for record in records if record.get("kind") in {"llm", "tool"}]
        edges = [record for record in records if record.get("kind") == "edge"]

        assert [record["kind"] for record in primary] == ["tool", "tool", "tool"]
        assert load_replay_records(log_dir / "semantic_tools.jsonl") == primary

        first_source = {
            "run_id": "semantic_tools",
            "record_uid": primary[0]["record_uid"],
            "kind": "tool",
            "path_id": primary[0]["path_id"],
        }
        second_source = {
            "run_id": "semantic_tools",
            "record_uid": primary[1]["record_uid"],
            "kind": "tool",
            "path_id": primary[1]["path_id"],
        }
        branch_source = {
            "run_id": "semantic_tools",
            "record_uid": primary[2]["record_uid"],
            "kind": "tool",
            "path_id": primary[2]["path_id"],
        }

        assert any(
            edge["edge_kind"] == "data"
            and edge["from"] == first_source
            and edge["to"] == second_source
            for edge in edges
        )
        assert any(
            edge["edge_kind"] == "control"
            and edge["from"] == first_source
            and edge["to"] == branch_source
            for edge in edges
        )
        assert first_source in primary[1]["metadata"]["provenance"]["data_sources"]
        assert first_source in primary[2]["metadata"]["provenance"]["control_sources"]

        graph = build_orchestration_graph(load_graph_records(log_dir / "semantic_tools.jsonl"))
        assert any(
            edge["from"] == f"semantic_tools:{primary[0]['record_uid']}"
            and edge["to"] == f"semantic_tools:{primary[1]['record_uid']}"
            and edge["edge_kind"] == "data"
            for edge in graph["edges"]
        )
        assert any(
            edge["from"] == f"semantic_tools:{primary[0]['record_uid']}"
            and edge["to"] == f"semantic_tools:{primary[2]['record_uid']}"
            and edge["edge_kind"] == "control"
            for edge in graph["edges"]
        )

        calls_after_record = list(module.calls)
        with replay.replay(base_run="semantic_tools", log_dir=log_dir):
            assert module.run_flow() == ("alpha", "beta:alpha", "branched")
        assert module.calls == calls_after_record

        comprehension_module = importlib.import_module("agent_ast_comprehensions")
        with replay.record("semantic_comprehensions", log_dir=log_dir):
            assert comprehension_module.run_flow() == "ok"

        comprehension_records = read_jsonl(log_dir / "semantic_comprehensions.jsonl")
        comprehension_primary = [
            record
            for record in comprehension_records
            if record.get("kind") in {"llm", "tool"}
        ]
        comprehension_edges = [
            record
            for record in comprehension_records
            if record.get("kind") == "edge"
        ]
        comprehension_first_source = source_for("semantic_comprehensions", comprehension_primary[0])
        comprehension_second_source = source_for("semantic_comprehensions", comprehension_primary[1])
        assert comprehension_first_source in comprehension_primary[1]["metadata"]["provenance"]["data_sources"]
        assert any(
            edge["edge_kind"] == "data"
            and edge["from"] == comprehension_first_source
            and edge["to"] == comprehension_second_source
            for edge in comprehension_edges
        )

        expression_module = importlib.import_module("agent_ast_expression_control")
        with replay.record("semantic_expression_control", log_dir=log_dir):
            assert expression_module.run_flow() == ("fallback", "", "left", ["selected", "selected"])

        assert expression_module.calls == [
            "gate_or",
            "fallback_or",
            ("sink_or", "fallback"),
            "gate_and",
            ("sink_and", ""),
            "selector",
            ("sink_ifexp", "left"),
            "flags",
            ("sink_comp_filter", ["selected", "selected"]),
        ]

        expression_records = read_jsonl(log_dir / "semantic_expression_control.jsonl")
        expression_primary = [
            record
            for record in expression_records
            if record.get("kind") in {"llm", "tool"}
        ]
        expression_edges = [
            record
            for record in expression_records
            if record.get("kind") == "edge"
        ]
        expression_sources = {
            record["input"]["tool_name"]: source_for("semantic_expression_control", record)
            for record in expression_primary
        }
        assert expression_sources["gate_or"] in expression_primary[2]["metadata"]["provenance"]["control_sources"]
        assert expression_sources["fallback_or"] in expression_primary[2]["metadata"]["provenance"]["data_sources"]
        assert expression_sources["gate_and"] in expression_primary[4]["metadata"]["provenance"]["data_sources"]
        assert expression_sources["selector"] in expression_primary[6]["metadata"]["provenance"]["control_sources"]
        assert expression_sources["flags"] in expression_primary[8]["metadata"]["provenance"]["control_sources"]
        assert "skipped_and" not in expression_sources
        assert any(
            edge["edge_kind"] == "control"
            and edge["from"] == expression_sources["gate_or"]
            and edge["to"] == expression_sources["sink_or"]
            for edge in expression_edges
        )
        assert any(
            edge["edge_kind"] == "control"
            and edge["from"] == expression_sources["selector"]
            and edge["to"] == expression_sources["sink_ifexp"]
            for edge in expression_edges
        )
        assert any(
            edge["edge_kind"] == "control"
            and edge["from"] == expression_sources["flags"]
            and edge["to"] == expression_sources["sink_comp_filter"]
            for edge in expression_edges
        )

        collision_module = importlib.import_module("agent_ast_keyword_collisions")
        assert collision_module.run_flow() == {
            "plain": {
                "fn": "fn-value",
                "args": {"pos": 1},
                "kwargs": {"kw": 2},
                "arguments": {"tool": 3},
                "obj": "obj-value",
                "name": "name-value",
            },
            "method": {
                "name": "method-name",
                "obj": "method-obj",
                "args": {"method": "args"},
                "kwargs": {"method": "kwargs"},
                "arguments": {"method": "arguments"},
            },
            "format": "template-value:args-value:kwargs-value:arguments-value:name-value",
            "join": "a|b",
        }

        llm_module = importlib.import_module("agent_ast_llm_fork")
        with replay.record("llm_fork_base", log_dir=log_dir):
            recorded_llm_flow = llm_module.run_flow()
            assert recorded_llm_flow == (
                ["base:A", "base:B", "base:C"],
                "base:parent uses base:A|base:B|base:C",
            ), recorded_llm_flow

        phase["prefix"] = "fork"
        with replay.replay(
            base_run="llm_fork_base",
            breakpoint_record_uid="rec_000002",
            override_output="override:B",
            log_dir=log_dir,
            fork_run="llm_fork_selective",
        ):
            forked_llm_flow = llm_module.run_flow()
            assert forked_llm_flow == (
                ["base:A", "override:B", "base:C"],
                "fork:parent uses base:A|override:B|base:C",
            ), forked_llm_flow

        base_llms = [
            record
            for record in read_jsonl(log_dir / "llm_fork_base.jsonl")
            if record.get("kind") == "llm"
        ]
        fork_records = read_jsonl(log_dir / "llm_fork_selective.jsonl")
        fork_llms = [record for record in fork_records if record.get("kind") == "llm"]
        assert [record["output"]["content"] for record in fork_llms] == [
            "override:B",
            "fork:parent uses base:A|override:B|base:C",
        ]

        fork_b_source = source_for("llm_fork_selective", fork_llms[0])
        fork_parent_source = source_for("llm_fork_selective", fork_llms[1])
        base_a_source = source_for("llm_fork_base", base_llms[0])
        base_b_source = source_for("llm_fork_base", base_llms[1])
        base_c_source = source_for("llm_fork_base", base_llms[2])
        parent_sources = fork_llms[1]["metadata"]["provenance"]["data_sources"]
        assert fork_b_source in parent_sources
        assert base_a_source in parent_sources
        assert base_c_source in parent_sources
        assert base_b_source not in parent_sources

        parent_edges = [
            edge
            for edge in fork_records
            if edge.get("kind") == "edge" and edge.get("to") == fork_parent_source
        ]
        edge_sources = [edge["from"] for edge in parent_edges if edge.get("edge_kind") == "data"]
        assert fork_b_source in edge_sources
        assert base_b_source not in edge_sources

        print("ast provenance test ok")
    finally:
        sys.path.remove(str(project_root))
        sys.modules.pop("agent_ast_tools", None)
        sys.modules.pop("agent_ast_llm_fork", None)
        sys.modules.pop("agent_ast_comprehensions", None)
        sys.modules.pop("agent_ast_expression_control", None)
        sys.modules.pop("agent_ast_keyword_collisions", None)
        uninstall_import_hook(token)
        replay.uninstall()
        RUNTIME.reset(enabled=False)
        Completions.create = original_create
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
