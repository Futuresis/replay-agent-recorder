from __future__ import annotations

import unittest

from replay.graph_ir import build_graph_ir, filter_graph_ir, summarize_graph_ir


def _synthetic_records() -> list[dict]:
    return [
        {
            "record_uid": "rec_000001",
            "run_id": "base_run",
            "kind": "llm",
            "input_id": "input-a",
            "path_id": "root/0",
            "input": {
                "provider": "openai",
                "api": "chat.completions.create",
                "messages": [{"role": "user", "content": "seed prompt"}],
            },
            "output": {"content": "seed answer"},
            "metadata": {
                "provenance": {"data_sources": [], "control_sources": []},
                "semantic": {"callsite_fingerprint": "agent.py:chat:10"},
            },
        },
        {
            "record_uid": "rec_000002",
            "run_id": "base_run",
            "kind": "tool",
            "input_id": "input-b",
            "path_id": "root/tool/0",
            "input": {"tool_name": "lookup", "arguments": {"query": "seed answer"}},
            "output": {"value": {"result": "lookup result"}},
            "metadata": {
                "provenance": {
                    "data_sources": [
                        {
                            "run_id": "base_run",
                            "record_uid": "rec_000001",
                            "kind": "llm",
                            "path_id": "root/0",
                        }
                    ],
                    "control_sources": [],
                },
                "semantic": {"callsite_fingerprint": "agent.py:lookup:20"},
            },
            "effects": {
                "filesystem": {
                    "root": "sandbox",
                    "changes": [{"type": "create", "path": "note.md"}],
                }
            },
        },
        {
            "schema_version": 2,
            "kind": "edge",
            "edge_kind": "data",
            "from": {
                "run_id": "base_run",
                "record_uid": "rec_000001",
                "kind": "llm",
                "path_id": "root/0",
            },
            "to": {
                "run_id": "base_run",
                "record_uid": "rec_000002",
                "kind": "tool",
                "path_id": "root/tool/0",
            },
        },
    ]


def _synthetic_compare_records() -> list[dict]:
    return [
        *_synthetic_records(),
        {
            "fork_metadata": {
                "base_run": "base_run",
                "breakpoint_record_uid": "rec_000001",
                "mode": "fork",
            },
            "_graph_run_id": "fork_run",
        },
        {
            "record_uid": "rec_000001",
            "run_id": "fork_run",
            "kind": "llm",
            "input_id": "input-a",
            "path_id": "root/0",
            "input": {
                "provider": "openai",
                "api": "chat.completions.create",
                "messages": [{"role": "user", "content": "seed prompt"}],
            },
            "output": {"content": "fork answer"},
            "metadata": {"override": True, "provenance": {"data_sources": [], "control_sources": []}},
        },
        {
            "record_uid": "rec_000003",
            "run_id": "fork_run",
            "kind": "tool",
            "input_id": "input-c",
            "path_id": "root/tool/new",
            "input": {"tool_name": "audit", "arguments": {"query": "fork answer"}},
            "output": {"value": {"result": "new result"}},
            "metadata": {"provenance": {"data_sources": [], "control_sources": []}},
        },
    ]


def _tool_call_llm_record() -> dict:
    return {
        "record_uid": "rec_000010",
        "run_id": "tool_run",
        "kind": "llm",
        "input_id": "input-tool",
        "path_id": "root/0",
        "input": {
            "provider": "openai",
            "api": "chat.completions.create",
            "messages": [{"role": "user", "content": "lookup weather"}],
        },
        "output": {"content": ""},
        "metadata": {
            "spans": [
                {
                    "kind": "langgraph_node",
                    "name": "supervisor",
                    "metadata": {"framework": "langgraph"},
                }
            ]
        },
    }


def _tool_call_record(*, tool_call_id: str | None = "call_lookup") -> dict:
    return {
        "record_uid": "rec_000011",
        "run_id": "tool_run",
        "kind": "tool_call",
        "input_id": "input-tool",
        "path_id": "root/0/tool_call/0",
        "input": {
            "tool_call_id": tool_call_id,
            "tool_name": "lookup",
            "arguments": {"query": "weather"},
            "index": 0,
            "source_llm_record_uid": "rec_000010",
        },
        "output": None,
        "metadata": {
            "component": "tool_call",
            "replayable": False,
            "spans": [
                {
                    "kind": "langgraph_node",
                    "name": "supervisor",
                    "metadata": {"framework": "langgraph"},
                }
            ],
        },
    }


def _matching_tool_record(*, tool_call_id: str | None = "call_lookup") -> dict:
    metadata = {
        "tool_name": "lookup",
        "spans": [
            {
                "kind": "langgraph_node",
                "name": "supervisor",
                "metadata": {"framework": "langgraph"},
            }
        ],
    }
    if tool_call_id is not None:
        metadata["tool_call_id"] = tool_call_id
    return {
        "record_uid": "rec_000012",
        "run_id": "tool_run",
        "kind": "tool",
        "input_id": "input-tool-exec",
        "path_id": "root/tool/0",
        "input": {"tool_name": "lookup", "arguments": {"query": "weather"}},
        "output": {"value": {"result": "sunny"}},
        "metadata": metadata,
    }


def _tool_call_records_with_matching_tool() -> list[dict]:
    return [_tool_call_llm_record(), _tool_call_record(), _matching_tool_record()]


def _tool_call_records_weak_match() -> list[dict]:
    return [
        _tool_call_llm_record(),
        _tool_call_record(tool_call_id=None),
        _matching_tool_record(tool_call_id=None),
    ]


def _tool_call_records_link_disabled() -> list[dict]:
    tool_call = _tool_call_record()
    tool_call["metadata"] = {**tool_call["metadata"], "link_tool_executions": False}
    return [_tool_call_llm_record(), tool_call, _matching_tool_record()]


def _synthetic_transitive_records() -> list[dict]:
    records = [
        {
            "record_uid": "rec_000001",
            "run_id": "base_run",
            "kind": "llm",
            "input_id": "input-a",
            "path_id": "root/0",
            "input": {"messages": [{"role": "user", "content": "seed"}]},
            "output": {"content": "a"},
            "metadata": {"provenance": {"data_sources": [], "control_sources": []}},
        },
        {
            "record_uid": "rec_000002",
            "run_id": "base_run",
            "kind": "llm",
            "input_id": "input-b",
            "path_id": "root/1",
            "input": {"messages": [{"role": "user", "content": "a"}]},
            "output": {"content": "b"},
            "metadata": {"provenance": {"data_sources": [], "control_sources": []}},
        },
        {
            "record_uid": "rec_000003",
            "run_id": "base_run",
            "kind": "tool",
            "input_id": "input-c",
            "path_id": "root/2",
            "input": {"tool_name": "write", "arguments": {"text": "a b"}},
            "output": {"value": "c"},
            "metadata": {"provenance": {"data_sources": [], "control_sources": []}},
        },
        {
            "record_uid": "rec_000004",
            "run_id": "base_run",
            "kind": "tool",
            "input_id": "input-d",
            "path_id": "root/3",
            "input": {"tool_name": "final", "arguments": {"text": "a b c"}},
            "output": {"value": "d"},
            "metadata": {"provenance": {"data_sources": [], "control_sources": []}},
        },
    ]
    edges = [
        ("rec_000001", "rec_000002"),
        ("rec_000001", "rec_000003"),
        ("rec_000002", "rec_000003"),
        ("rec_000001", "rec_000004"),
        ("rec_000002", "rec_000004"),
        ("rec_000003", "rec_000004"),
    ]
    for source, target in edges:
        records.append(
            {
                "schema_version": 2,
                "kind": "edge",
                "edge_kind": "data",
                "from": {"run_id": "base_run", "record_uid": source, "kind": "llm"},
                "to": {"run_id": "base_run", "record_uid": target, "kind": "tool"},
            }
        )
    return records


class GraphIRTests(unittest.TestCase):
    def test_build_graph_ir_contract_contains_nodes_edges_runs_and_evidence(self) -> None:
        ir = build_graph_ir(_synthetic_records(), title="Synthetic")

        self.assertEqual(ir["schema_version"], 1)
        self.assertEqual(ir["meta"]["title"], "Synthetic")
        self.assertEqual(
            ir["graph"]["runs"],
            [{"id": "run:base_run", "run_id": "base_run", "run_role": "base"}],
        )
        self.assertEqual(ir["graph"]["stats"]["node_count"], 2)
        self.assertEqual(ir["graph"]["stats"]["edge_kinds"], {"data": 1})

        node = next(item for item in ir["graph"]["nodes"] if item["id"] == "base_run:rec_000002")
        self.assertEqual(node["kind"], "tool")
        self.assertEqual(node["degree"], {"incoming": 1, "outgoing": 0})
        self.assertEqual(node["order"], {"index": 2, "run_index": 2})
        self.assertTrue(node["preview"]["input"].startswith("lookup("))
        self.assertEqual(node["display"]["kind_label"], "工具调用")
        self.assertIn("lookup", node["display"]["title"])
        self.assertNotIn("agent.py:lookup:20", node["display"]["title"])
        self.assertTrue(node["evidence_refs"])
        self.assertEqual(ir["graph"]["timeline"]["items"][0]["node_id"], "base_run:rec_000001")
        self.assertEqual(ir["graph"]["diff"]["comparisons"], [])
        self.assertIn("edge_layers", ir["graph"])

        llm_node = next(item for item in ir["graph"]["nodes"] if item["id"] == "base_run:rec_000001")
        self.assertEqual(llm_node["display"]["kind_label"], "模型调用")
        self.assertNotIn("agent.py:chat:10", llm_node["display"]["title"])

        edge = ir["graph"]["edges"][0]
        self.assertEqual(edge["source"], "base_run:rec_000001")
        self.assertEqual(edge["target"], "base_run:rec_000002")
        self.assertTrue(edge["evidence_refs"])

    def test_build_graph_ir_derives_reduced_default_display_edges(self) -> None:
        ir = build_graph_ir(_synthetic_transitive_records())

        full_edges = ir["graph"]["edges"]
        edge_layers = ir["graph"]["edge_layers"]
        default_edges = edge_layers["default"]
        reduced_edges = edge_layers["reduced_provenance"]

        self.assertEqual(len(full_edges), 6)
        self.assertLess(len(reduced_edges), len(full_edges))
        self.assertLess(len(default_edges), len(full_edges) + len(edge_layers["flow"]))
        self.assertNotIn(
            ("base_run:rec_000001", "base_run:rec_000004", "data"),
            {
                (edge["source"], edge["target"], edge["edge_kind"])
                for edge in reduced_edges
            },
        )
        self.assertIn(
            ("base_run:rec_000003", "base_run:rec_000004", "data"),
            {
                (edge["source"], edge["target"], edge["edge_kind"])
                for edge in reduced_edges
            },
        )
        self.assertGreaterEqual(ir["graph"]["stats"]["edge_count"], ir["graph"]["stats"]["default_edge_count"])

    def test_build_graph_ir_contract_adds_static_and_workbench_action_metadata(self) -> None:
        ir = build_graph_ir(_synthetic_records())
        llm_node = next(item for item in ir["graph"]["nodes"] if item["kind"] == "llm")
        replay_action = next(item for item in llm_node["actions"] if item["action"] == "replay.breakpoint")
        copy_action = next(item for item in llm_node["actions"] if item["action"] == "copy_cli_snippet.replay_breakpoint")

        self.assertFalse(replay_action["availability"]["static_html"]["enabled"])
        self.assertFalse(replay_action["availability"]["workbench"]["enabled"])
        self.assertIn("python -m replay python", copy_action["params"]["snippet"])
        self.assertTrue(copy_action["availability"]["static_html"]["enabled"])

    def test_build_graph_ir_contract_marks_fork_runs_and_synthetic_fork_edges(self) -> None:
        ir = build_graph_ir(_synthetic_compare_records())

        self.assertEqual(ir["meta"]["base_run"], "base_run")
        self.assertEqual(ir["meta"]["fork_runs"], ["fork_run"])
        self.assertEqual(
            {run["run_id"]: run["run_role"] for run in ir["graph"]["runs"]},
            {"base_run": "base", "fork_run": "fork"},
        )
        fork_edges = [edge for edge in ir["graph"]["edges"] if edge["edge_kind"] == "fork"]
        self.assertEqual(len(fork_edges), 1)
        self.assertTrue(fork_edges[0]["cross_run"])
        self.assertTrue(fork_edges[0]["evidence_refs"])
        self.assertEqual(fork_edges[0]["source"], "base_run:rec_000001")
        self.assertEqual(fork_edges[0]["target"], "fork_run:rec_000001")
        comparison = ir["graph"]["diff"]["comparisons"][0]
        self.assertEqual(comparison["base_run"], "base_run")
        self.assertEqual(comparison["fork_run"], "fork_run")
        self.assertEqual(comparison["breakpoint"]["base_node_id"], "base_run:rec_000001")
        self.assertEqual(comparison["breakpoint"]["fork_node_id"], "fork_run:rec_000001")
        self.assertIn("fork_run:rec_000003", comparison["new_node_ids"])
        self.assertIn("base_run:rec_000002", comparison["missing_node_ids"])
        self.assertIn("base_run:rec_000001", comparison["changed_node_ids"])
        fork_node = next(item for item in ir["graph"]["nodes"] if item["id"] == "fork_run:rec_000001")
        self.assertEqual(fork_node["diff"]["status"], "changed")
        self.assertEqual(fork_node["diff"]["comparisons"][0]["alignment_method"], "fork_boundary")

    def test_build_graph_ir_fork_boundary_targets_first_fork_record_not_matching_uid(self) -> None:
        records = _synthetic_compare_records()
        fork_start = next(record for record in records if record.get("run_id") == "fork_run" and record.get("record_uid") == "rec_000001")
        fork_start["record_uid"] = "rec_000010"

        records.append(
            {
                "record_uid": "rec_000001",
                "run_id": "fork_run",
                "kind": "tool",
                "input_id": "unrelated-same-uid",
                "path_id": "root/tool/unrelated",
                "input": {"tool_name": "late", "arguments": {}},
                "output": {"value": "late"},
                "metadata": {"provenance": {"data_sources": [], "control_sources": []}},
            }
        )

        ir = build_graph_ir(records)

        fork_edge = next(edge for edge in ir["graph"]["edges"] if edge["edge_kind"] == "fork")
        self.assertEqual(fork_edge["source"], "base_run:rec_000001")
        self.assertEqual(fork_edge["target"], "fork_run:rec_000010")
        self.assertEqual(fork_edge["metadata"]["target_record_uid"], "rec_000010")
        comparison = ir["graph"]["diff"]["comparisons"][0]
        self.assertEqual(comparison["breakpoint"]["fork_node_id"], "fork_run:rec_000010")

    def test_build_graph_ir_diff_alignment_does_not_match_cross_run_record_uid(self) -> None:
        records = _synthetic_compare_records()
        records[3]["fork_metadata"]["breakpoint_record_uid"] = "rec_000002"
        fork_start = next(record for record in records if record.get("run_id") == "fork_run" and record.get("record_uid") == "rec_000001")
        fork_start["record_uid"] = "rec_000010"
        fork_start["path_id"] = "root/tool/0"
        fork_start["metadata"]["semantic"] = {"callsite_fingerprint": "agent.py:lookup:20"}
        same_uid_late_fork = next(record for record in records if record.get("run_id") == "fork_run" and record.get("record_uid") == "rec_000003")
        same_uid_late_fork["record_uid"] = "rec_000002"
        same_uid_late_fork["path_id"] = "root/tool/later"
        same_uid_late_fork["metadata"]["semantic"] = {"callsite_fingerprint": "agent.py:other:99"}

        ir = build_graph_ir(records)

        comparison = ir["graph"]["diff"]["comparisons"][0]
        aligned_pairs = {
            (alignment["base_node_id"], alignment["fork_node_id"], alignment["alignment_method"])
            for alignment in comparison["alignments"]
        }
        self.assertIn(("base_run:rec_000002", "fork_run:rec_000010", "fork_boundary"), aligned_pairs)
        self.assertNotIn(("base_run:rec_000002", "fork_run:rec_000002", "record_uid_path"), aligned_pairs)
        self.assertNotIn(("base_run:rec_000002", "fork_run:rec_000002", "record_uid_callsite"), aligned_pairs)

    def test_tool_call_records_become_nodes_with_intent_execution_edges_and_layers(self) -> None:
        ir = build_graph_ir(_tool_call_records_with_matching_tool())

        self.assertEqual(ir["graph"]["stats"]["node_kinds"], {"llm": 1, "tool": 1, "tool_call": 1})
        self.assertEqual(ir["graph"]["stats"]["edge_kinds"], {"llm_intent": 1, "tool_execution": 1})
        self.assertGreaterEqual(ir["graph"]["stats"]["default_edge_count"], 1)

        tool_call_node = next(node for node in ir["graph"]["nodes"] if node["kind"] == "tool_call")
        self.assertEqual(tool_call_node["id"], "tool_run:rec_000011")
        self.assertEqual(tool_call_node["title"], "lookup")
        self.assertEqual(tool_call_node["provider"], "intent")
        self.assertIn("lookup(", tool_call_node["preview"]["input"])
        self.assertEqual(tool_call_node["degree"], {"incoming": 1, "outgoing": 1})
        self.assertEqual(tool_call_node["display"]["kind_label"], "工具意图")

        edges = {(edge["source"], edge["target"], edge["edge_kind"]) for edge in ir["graph"]["edges"]}
        self.assertIn(("tool_run:rec_000010", "tool_run:rec_000011", "llm_intent"), edges)
        self.assertIn(("tool_run:rec_000011", "tool_run:rec_000012", "tool_execution"), edges)

        full_layer_edges = {
            (edge["source"], edge["target"], edge["edge_kind"])
            for edge in ir["graph"]["edge_layers"]["full_provenance"]
        }
        self.assertIn(("tool_run:rec_000010", "tool_run:rec_000011", "llm_intent"), full_layer_edges)
        self.assertIn(("tool_run:rec_000011", "tool_run:rec_000012", "tool_execution"), full_layer_edges)
        self.assertTrue(ir["graph"]["edge_layers"]["default"])

    def test_tool_call_weak_match_links_same_run_span_name_and_arguments(self) -> None:
        ir = build_graph_ir(_tool_call_records_weak_match())

        edges = {(edge["source"], edge["target"], edge["edge_kind"]) for edge in ir["graph"]["edges"]}
        self.assertIn(("tool_run:rec_000011", "tool_run:rec_000012", "tool_execution"), edges)

    def test_tool_call_link_disabled_keeps_intent_without_execution_edge(self) -> None:
        ir = build_graph_ir(_tool_call_records_link_disabled())

        self.assertEqual(ir["graph"]["stats"]["node_kinds"], {"llm": 1, "tool": 1, "tool_call": 1})
        edges = {(edge["source"], edge["target"], edge["edge_kind"]) for edge in ir["graph"]["edges"]}
        self.assertIn(("tool_run:rec_000010", "tool_run:rec_000011", "llm_intent"), edges)
        self.assertNotIn(("tool_run:rec_000011", "tool_run:rec_000012", "tool_execution"), edges)

    def test_filter_graph_ir_supports_focus_and_depth(self) -> None:
        ir = build_graph_ir(_synthetic_records())
        filtered = filter_graph_ir(
            ir,
            focus="base_run:rec_000001",
            direction="downstream",
            max_depth=1,
        )

        self.assertEqual(summarize_graph_ir(filtered)["node_count"], 2)
        self.assertEqual(summarize_graph_ir(filtered)["edge_count"], 1)
        self.assertEqual(len(filtered["graph"]["timeline"]["items"]), 2)

    def test_filter_graph_ir_rejects_unknown_focus_node(self) -> None:
        ir = build_graph_ir(_synthetic_records())

        with self.assertRaisesRegex(ValueError, "Focus node not found"):
            filter_graph_ir(ir, focus="missing:rec")


if __name__ == "__main__":
    unittest.main()
