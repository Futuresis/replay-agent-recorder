from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .edges import build_orchestration_graph, load_graph_records


SCHEMA_VERSION = 1
DISPLAY_FAN_IN_LIMIT = 6

_DEFAULT_CAPABILITIES = {
    "focus": True,
    "compare": True,
    "evidence_panel": True,
    "static_html_actions": True,
    "workbench_actions": False,
    "llm_breakpoint_replay": True,
    "tool_breakpoint_replay": False,
    "override_output": True,
    "rerun_descendants": False,
    "diff_view": True,
    "timeline_view": True,
    "collapse_groups": True,
    "layout_cache": True,
    "search_results": True,
}
_DIRECTION_ALIASES = {
    "both": "both",
    "upstream": "upstream",
    "downstream": "downstream",
    "incoming": "upstream",
    "outgoing": "downstream",
}


def load_trace_records(
    paths: str | Path | Iterable[str | Path],
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    return load_graph_records(paths, run_id=run_id)


def build_graph_ir(
    records: list[dict[str, Any]],
    *,
    title: str | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_records = [dict(record) for record in records]
    record_orders = _derive_record_orders(normalized_records)
    runs = _derive_run_roles(normalized_records)
    run_by_id = {run["run_id"]: run for run in runs}
    graph = build_orchestration_graph(normalized_records)
    _add_tool_call_graph_nodes(graph, normalized_records)
    evidence_items, node_evidence_refs, edge_evidence_refs = _derive_evidence_items(normalized_records)

    groups_by_id: dict[str, dict[str, Any]] = {}
    nodes: list[dict[str, Any]] = []
    node_by_id: dict[str, dict[str, Any]] = {}

    for graph_node in graph["nodes"]:
        record = graph_node.get("record") or {}
        run_id = graph_node.get("run_id") or record.get("run_id") or "synthetic_run"
        record_uid = graph_node.get("record_uid") or record.get("record_uid")
        if not record_uid:
            continue

        node_id = f"{run_id}:{record_uid}"
        path_id = record.get("path_id") or graph_node.get("path_id")
        branch_id = _derive_branch_id(path_id)
        run_role = run_by_id.get(run_id, {}).get("run_role", "base")
        metadata = record.get("metadata") or {}
        order = record_orders.get((str(run_id), str(record_uid)), {})
        node = {
            "id": node_id,
            "run_id": run_id,
            "record_uid": record_uid,
            "kind": graph_node.get("kind") or record.get("kind"),
            "path_id": path_id,
            "branch_id": branch_id,
            "run_role": run_role,
            "status": _derive_node_status(record, run_role),
            "title": _derive_node_title(record or graph_node),
            "summary": _derive_node_summary(record or graph_node),
            "framework": _first_non_empty(metadata.get("framework"), record.get("input", {}).get("framework")),
            "component": _first_non_empty(metadata.get("component"), record.get("input", {}).get("component")),
            "provider": _derive_node_provider(record or graph_node),
            "api": _derive_node_api(record or graph_node),
            "callsite": record.get("callsite"),
            "created_at": metadata.get("created_at"),
            "duration_ms": metadata.get("latency_ms"),
            "order": order,
            "spans": metadata.get("spans") or [],
            "semantic": metadata.get("semantic") or {},
            "preview": {
                "input": _derive_input_preview(record or graph_node),
                "output": _derive_output_preview(record or graph_node),
            },
            "record": record,
            "actions": _derive_node_actions(record or graph_node, run_role),
            "evidence_refs": list(dict.fromkeys(node_evidence_refs.get(node_id, []))),
        }
        node["display"] = _derive_node_display(record or graph_node, node)
        nodes.append(node)
        node_by_id[node_id] = node

        _append_group_child(
            groups_by_id,
            {
                "id": f"group:run:{run_id}",
                "kind": "run_group",
                "label": run_id,
                "run_id": run_id,
            },
            node_id,
        )
        if branch_id is not None:
            _append_group_child(
                groups_by_id,
                {
                    "id": f"group:path:{run_id}:{branch_id}",
                    "kind": "path_group",
                    "label": branch_id,
                    "run_id": run_id,
                    "branch_id": branch_id,
                },
                node_id,
            )
        for span in metadata.get("spans") or []:
            span_name = span.get("name")
            if not span_name:
                continue
            _append_group_child(
                groups_by_id,
                {
                    "id": f"group:span:{run_id}:{span_name}",
                    "kind": "span_group",
                    "label": span_name,
                    "run_id": run_id,
                    "span_kind": span.get("kind"),
                },
                node_id,
            )

    edges: list[dict[str, Any]] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()
    for graph_edge in graph["edges"]:
        source_id = graph_edge.get("from")
        target_id = graph_edge.get("to")
        if not source_id or not target_id:
            continue
        edge_kind = str(graph_edge.get("edge_kind") or "unknown")
        source_node = node_by_id.get(source_id, {})
        target_node = node_by_id.get(target_id, {})
        cross_run = source_node.get("run_id") != target_node.get("run_id")
        run_role = "fork" if "fork" in {source_node.get("run_role"), target_node.get("run_role")} else "base"
        edge_key = (source_id, target_id, edge_kind)
        seen_edge_keys.add(edge_key)
        edges.append(
            {
                "id": f"edge:{source_id}->{target_id}:{edge_kind}",
                "source": source_id,
                "target": target_id,
                "from": source_id,
                "to": target_id,
                "edge_kind": edge_kind,
                "kind": edge_kind,
                "cross_run": cross_run,
                "run_role": run_role,
                "summary": _derive_edge_summary(edge_kind),
                "metadata": graph_edge.get("metadata") or {},
                "evidence_refs": list(dict.fromkeys(edge_evidence_refs.get(edge_key, []))),
            }
        )

    for synthetic_edge in _derive_tool_call_edges(normalized_records, node_by_id):
        edge_key = (
            synthetic_edge["source"],
            synthetic_edge["target"],
            synthetic_edge["edge_kind"],
        )
        if edge_key in seen_edge_keys:
            continue
        seen_edge_keys.add(edge_key)
        edges.append(synthetic_edge)

    for synthetic_edge in _derive_compare_fork_edges(normalized_records, node_by_id):
        edge_key = (
            synthetic_edge["source"],
            synthetic_edge["target"],
            synthetic_edge["edge_kind"],
        )
        if edge_key in seen_edge_keys:
            continue
        seen_edge_keys.add(edge_key)
        evidence_id = f"ev_{len(evidence_items) + 1:06d}"
        synthetic_edge["evidence_refs"] = [evidence_id]
        evidence_items.append(
            {
                "id": evidence_id,
                "evidence_kind": "fork_boundary",
                "label": "Fork boundary",
                "source_refs": [synthetic_edge["source"]],
                "target_refs": [synthetic_edge["target"]],
                "details": dict(synthetic_edge.get("metadata") or {}),
            }
        )
        edges.append(synthetic_edge)

    _attach_node_degrees(nodes, edges)
    timeline = _derive_timeline(nodes)
    graph_diff = _derive_graph_diff(runs, nodes, edges)
    edge_layers = _derive_edge_layers(nodes, edges)

    ir = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "title": title or _derive_default_title(runs),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "theme_default": "dark",
            "base_run": next((run["run_id"] for run in runs if run.get("run_role") == "base"), None),
            "fork_runs": [run["run_id"] for run in runs if run.get("run_role") == "fork"],
            "capabilities": {**_DEFAULT_CAPABILITIES, **(capabilities or {})},
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "edge_layers": edge_layers,
            "groups": list(groups_by_id.values()),
            "runs": runs,
            "timeline": timeline,
            "diff": graph_diff,
            "layout": {
                "strategy": "layered-v1",
                "node_width": 190,
                "node_height": 74,
                "cacheable": True,
            },
            "stats": {},
        },
        "evidence": {
            "items": evidence_items,
        },
    }
    ir["graph"]["stats"] = summarize_graph_ir(ir)
    return ir


def filter_graph_ir(
    ir: dict[str, Any],
    *,
    focus: str | None = None,
    direction: str = "both",
    max_depth: int | None = None,
    kinds: set[str] | None = None,
    edge_kinds: set[str] | None = None,
    run_roles: set[str] | None = None,
) -> dict[str, Any]:
    normalized_direction = _DIRECTION_ALIASES.get(direction)
    if normalized_direction is None:
        raise ValueError(f"Unsupported direction: {direction}")

    nodes = list(ir.get("graph", {}).get("nodes", []))
    edges = list(ir.get("graph", {}).get("edges", []))
    edge_layers = dict(ir.get("graph", {}).get("edge_layers") or {})
    groups = list(ir.get("graph", {}).get("groups", []))
    runs = list(ir.get("graph", {}).get("runs", []))
    evidence_items = list(ir.get("evidence", {}).get("items", []))

    node_map = {node["id"]: node for node in nodes}
    if focus is not None and focus not in node_map:
        raise ValueError(f"Focus node not found: {focus}")

    allowed_run_ids = None
    if run_roles is not None:
        allowed_run_ids = {run["run_id"] for run in runs if run.get("run_role") in run_roles}

    selected_node_ids = set(node_map)
    if focus is not None:
        outgoing: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            source = edge.get("source") or edge.get("from")
            target = edge.get("target") or edge.get("to")
            if not source or not target:
                continue
            outgoing[source].append(target)
            incoming[target].append(source)

        selected_node_ids = {focus}
        queue = deque([(focus, 0)])
        visited = {focus}
        while queue:
            current, depth = queue.popleft()
            if max_depth is not None and depth >= max_depth:
                continue
            neighbors: list[str] = []
            if normalized_direction in {"both", "downstream"}:
                neighbors.extend(outgoing.get(current, []))
            if normalized_direction in {"both", "upstream"}:
                neighbors.extend(incoming.get(current, []))
            for neighbor in neighbors:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                selected_node_ids.add(neighbor)
                queue.append((neighbor, depth + 1))

    filtered_nodes = []
    for node in nodes:
        if node["id"] not in selected_node_ids:
            continue
        if kinds is not None and node.get("kind") not in kinds:
            continue
        if allowed_run_ids is not None and node.get("run_id") not in allowed_run_ids:
            continue
        filtered_nodes.append(node)
    filtered_node_ids = {node["id"] for node in filtered_nodes}

    filtered_edges = []
    for edge in edges:
        source = edge.get("source") or edge.get("from")
        target = edge.get("target") or edge.get("to")
        if source not in filtered_node_ids or target not in filtered_node_ids:
            continue
        if edge_kinds is not None and edge.get("edge_kind") not in edge_kinds:
            continue
        filtered_edges.append(edge)
    filtered_edge_layers = _filter_edge_layers(edge_layers, filtered_node_ids, edge_kinds, run_roles)

    filtered_runs = [run for run in runs if any(node.get("run_id") == run.get("run_id") for node in filtered_nodes)]
    filtered_groups = [
        group for group in groups if any(child in filtered_node_ids for child in group.get("children", []))
    ]
    filtered_edge_ids = {edge.get("id") for edge in filtered_edges if edge.get("id")}
    filtered_timeline = _filter_timeline(ir.get("graph", {}).get("timeline"), filtered_node_ids)
    filtered_diff = _filter_graph_diff(
        ir.get("graph", {}).get("diff"),
        filtered_node_ids,
        filtered_edge_ids,
    )

    used_evidence_refs = {
        evidence_ref
        for item in filtered_nodes + filtered_edges
        for evidence_ref in item.get("evidence_refs", [])
    }
    filtered_evidence = [item for item in evidence_items if item.get("id") in used_evidence_refs]

    result = {
        "schema_version": ir.get("schema_version", SCHEMA_VERSION),
        "meta": dict(ir.get("meta", {})),
        "graph": {
            "nodes": filtered_nodes,
            "edges": filtered_edges,
            "edge_layers": filtered_edge_layers,
            "groups": filtered_groups,
            "runs": filtered_runs,
            "timeline": filtered_timeline,
            "diff": filtered_diff,
            "layout": dict(ir.get("graph", {}).get("layout") or {}),
            "stats": {},
        },
        "evidence": {"items": filtered_evidence},
    }
    _attach_node_degrees(result["graph"]["nodes"], result["graph"]["edges"])
    result["graph"]["stats"] = summarize_graph_ir(result)
    return result


def summarize_graph_ir(ir: dict[str, Any]) -> dict[str, Any]:
    graph = ir.get("graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    edge_layers = graph.get("edge_layers") or {}
    default_edges = edge_layers.get("default") or edges
    runs = graph.get("runs", [])
    groups = graph.get("groups", [])
    evidence_items = ir.get("evidence", {}).get("items", [])
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "default_edge_count": len(default_edges),
        "group_count": len(groups),
        "run_count": len(runs),
        "timeline_count": len((graph.get("timeline") or {}).get("items") or []),
        "evidence_count": len(evidence_items),
        "node_kinds": _count_values(node.get("kind") for node in nodes),
        "edge_kinds": _count_values(edge.get("edge_kind") for edge in edges),
        "default_edge_kinds": _count_values(edge.get("edge_kind") for edge in default_edges),
        "run_roles": _count_values(run.get("run_role") for run in runs),
        "status_counts": _count_values(node.get("status") for node in nodes),
        "diff_status_counts": _count_values((node.get("diff") or {}).get("status") for node in nodes),
        "cross_run_edge_count": sum(1 for edge in edges if edge.get("cross_run")),
    }


def _add_tool_call_graph_nodes(graph: dict[str, list[dict[str, Any]]], records: list[dict[str, Any]]) -> None:
    existing_node_ids = {node.get("id") for node in graph.get("nodes", [])}
    for record in records:
        if record.get("kind") != "tool_call":
            continue
        run_id = record.get("run_id") or record.get("_graph_run_id") or "synthetic_run"
        record_uid = record.get("record_uid")
        if not run_id or not record_uid:
            continue
        node_id = f"{run_id}:{record_uid}"
        if node_id in existing_node_ids:
            continue
        graph.setdefault("nodes", []).append(
            {
                "id": node_id,
                "run_id": str(run_id),
                "record_uid": record_uid,
                "kind": "tool_call",
                "path_id": record.get("path_id"),
                "record": record,
            }
        )
        existing_node_ids.add(node_id)
    graph["nodes"].sort(key=lambda node: str(node.get("id") or ""))


def _derive_record_orders(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, int]]:
    orders: dict[tuple[str, str], dict[str, int]] = {}
    run_indices: dict[str, int] = defaultdict(int)
    global_index = 0
    for record in records:
        if record.get("kind") not in {"llm", "tool", "tool_call"}:
            continue
        run_id = record.get("run_id") or record.get("_graph_run_id")
        record_uid = record.get("record_uid")
        if not run_id or not record_uid:
            continue
        run_id = str(run_id)
        record_uid = str(record_uid)
        global_index += 1
        run_indices[run_id] += 1
        orders[(run_id, record_uid)] = {
            "index": global_index,
            "run_index": run_indices[run_id],
        }
    return orders


def _derive_timeline(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_nodes = sorted(
        nodes,
        key=lambda node: (
            (node.get("order") or {}).get("index") or 10**9,
            str(node.get("run_id") or ""),
            str(node.get("record_uid") or ""),
        ),
    )
    return {
        "items": [
            {
                "id": f"timeline:{node['id']}",
                "node_id": node["id"],
                "run_id": node.get("run_id"),
                "run_role": node.get("run_role"),
                "record_uid": node.get("record_uid"),
                "kind": node.get("kind"),
                "status": node.get("status"),
                "title": node.get("title"),
                "summary": node.get("summary"),
                "path_id": node.get("path_id"),
                "branch_id": node.get("branch_id"),
                "created_at": node.get("created_at"),
                "duration_ms": node.get("duration_ms"),
                "order": node.get("order") or {},
                "span_names": [
                    span.get("name")
                    for span in node.get("spans") or []
                    if isinstance(span, dict) and span.get("name")
                ],
            }
            for node in ordered_nodes
        ]
    }


def _derive_edge_layers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    node_by_id = {node["id"]: node for node in nodes}
    full_edges = [_edge_for_layer(edge, layer="full_provenance") for edge in edges]
    fork_edges = [_edge_for_layer(edge, layer="fork") for edge in edges if edge.get("edge_kind") == "fork"]
    flow_edges = _derive_flow_edges(nodes, edges)
    reduced_edges = _derive_reduced_provenance_edges(nodes, edges)
    collapsed_edges, collapsed_groups = _collapse_display_fan_in(reduced_edges, node_by_id)

    default_edges = _dedupe_layer_edges([*flow_edges, *fork_edges, *collapsed_edges])
    return {
        "schema_version": 1,
        "default_layer": "default",
        "fan_in_limit": DISPLAY_FAN_IN_LIMIT,
        "default": default_edges,
        "flow": flow_edges,
        "reduced_provenance": reduced_edges,
        "full_provenance": full_edges,
        "collapsed_groups": collapsed_groups,
        "stats": {
            "full_edge_count": len(full_edges),
            "default_edge_count": len(default_edges),
            "flow_edge_count": len(flow_edges),
            "reduced_provenance_edge_count": len(reduced_edges),
            "collapsed_group_count": len(collapsed_groups),
            "hidden_provenance_edge_count": sum(group["hidden_edge_count"] for group in collapsed_groups),
        },
    }


def _derive_flow_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edge_keys = {
        (str(edge.get("source")), str(edge.get("target")), str(edge.get("edge_kind")))
        for edge in edges
        if edge.get("source") and edge.get("target")
    }
    nodes_by_run_branch: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        run_id = str(node.get("run_id") or "")
        if not run_id:
            continue
        branch_id = str(node.get("branch_id") or node.get("path_id") or "main")
        nodes_by_run_branch[(run_id, branch_id)].append(node)

    flow_edges: list[dict[str, Any]] = []
    for (run_id, branch_id), run_nodes in sorted(nodes_by_run_branch.items()):
        ordered = sorted(run_nodes, key=_node_order_key)
        for previous, current in zip(ordered, ordered[1:]):
            source = previous["id"]
            target = current["id"]
            edge_kind = "flow"
            edge = {
                "id": f"display:flow:{source}->{target}",
                "source": source,
                "target": target,
                "from": source,
                "to": target,
                "edge_kind": edge_kind,
                "kind": edge_kind,
                "cross_run": False,
                "run_role": current.get("run_role") or previous.get("run_role") or "base",
                "summary": "Execution flow",
                "metadata": {
                    "display_layer": "flow",
                    "synthetic": True,
                    "run_id": run_id,
                    "branch_id": branch_id,
                    "duplicates_full_edge": (source, target, edge_kind) in edge_keys,
                },
                "evidence_refs": [],
            }
            flow_edges.append(edge)
    return flow_edges


def _derive_reduced_provenance_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node_ids = {node["id"] for node in nodes}
    provenance_edges = [
        edge
        for edge in edges
        if edge.get("edge_kind") in {"data", "control"}
        and edge.get("source") in node_ids
        and edge.get("target") in node_ids
    ]
    incoming_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reachability = _build_reachability(provenance_edges)
    for edge in provenance_edges:
        incoming_by_target[str(edge["target"])].append(edge)

    reduced: list[dict[str, Any]] = []
    for target, incoming in incoming_by_target.items():
        for edge in incoming:
            source = str(edge["source"])
            edge_kind = str(edge.get("edge_kind") or "")
            hidden = False
            for other in incoming:
                other_source = str(other["source"])
                if other_source == source:
                    continue
                if edge_kind != str(other.get("edge_kind") or ""):
                    continue
                if other_source in reachability.get(source, set()):
                    hidden = True
                    break
            if hidden:
                continue
            reduced.append(_edge_for_layer(edge, layer="reduced_provenance"))
    return _dedupe_layer_edges(reduced)


def _build_reachability(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    outgoing: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if not source or not target:
            continue
        source = str(source)
        target = str(target)
        outgoing[source].append(target)
        nodes.add(source)
        nodes.add(target)

    reachability: dict[str, set[str]] = {}
    for node_id in nodes:
        reached: set[str] = set()
        queue = deque(outgoing.get(node_id, []))
        while queue:
            current = queue.popleft()
            if current in reached:
                continue
            reached.add(current)
            queue.extend(outgoing.get(current, []))
        reachability[node_id] = reached
    return reachability


def _collapse_display_fan_in(
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    incoming_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        incoming_by_target[str(edge.get("target"))].append(edge)

    visible_edges: list[dict[str, Any]] = []
    collapsed_groups: list[dict[str, Any]] = []
    for target, incoming in incoming_by_target.items():
        if len(incoming) <= DISPLAY_FAN_IN_LIMIT:
            visible_edges.extend(incoming)
            continue
        ordered = sorted(incoming, key=lambda edge: _display_edge_priority(edge, node_by_id))
        kept = ordered[:DISPLAY_FAN_IN_LIMIT]
        hidden = ordered[DISPLAY_FAN_IN_LIMIT:]
        visible_edges.extend(kept)
        group = {
            "id": f"edge-bundle:{target}",
            "target": target,
            "target_title": (node_by_id.get(target) or {}).get("title"),
            "hidden_edge_count": len(hidden),
            "visible_edge_count": len(kept),
            "full_edge_count": len(incoming),
            "hidden_edge_ids": [edge["id"] for edge in hidden if edge.get("id")],
            "hidden_source_ids": list(dict.fromkeys(str(edge.get("source")) for edge in hidden if edge.get("source"))),
            "edge_kinds": _count_values(edge.get("edge_kind") for edge in incoming),
        }
        for edge in kept:
            edge.setdefault("metadata", {})["collapsed_fan_in"] = {
                "group_id": group["id"],
                "hidden_edge_count": group["hidden_edge_count"],
                "full_edge_count": group["full_edge_count"],
            }
        collapsed_groups.append(group)
    return _dedupe_layer_edges(visible_edges), collapsed_groups


def _display_edge_priority(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> tuple[int, int, str]:
    source = node_by_id.get(str(edge.get("source"))) or {}
    target = node_by_id.get(str(edge.get("target"))) or {}
    source_order = (source.get("order") or {}).get("index") or 0
    target_order = (target.get("order") or {}).get("index") or 0
    distance = abs(int(target_order) - int(source_order)) if source_order and target_order else 10**9
    kind_priority = 0 if edge.get("edge_kind") == "control" else 1
    return (kind_priority, distance, str(edge.get("id") or ""))


def _edge_for_layer(edge: dict[str, Any], *, layer: str) -> dict[str, Any]:
    item = dict(edge)
    item["id"] = str(item.get("id") or f"edge:{item.get('source')}->{item.get('target')}:{item.get('edge_kind')}")
    metadata = dict(item.get("metadata") or {})
    metadata["display_layer"] = layer
    item["metadata"] = metadata
    return item


def _dedupe_layer_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for edge in edges:
        key = (
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            str(edge.get("edge_kind") or edge.get("kind") or ""),
            str((edge.get("metadata") or {}).get("display_layer") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _filter_edge_layers(
    edge_layers: dict[str, Any],
    node_ids: set[str],
    edge_kinds: set[str] | None,
    run_roles: set[str] | None,
) -> dict[str, Any]:
    if not edge_layers:
        return {}
    filtered: dict[str, Any] = {}
    for key, value in edge_layers.items():
        if key in {"default", "flow", "reduced_provenance", "full_provenance"} and isinstance(value, list):
            filtered[key] = [
                edge
                for edge in value
                if edge.get("source") in node_ids
                and edge.get("target") in node_ids
                and (edge_kinds is None or edge.get("edge_kind") in edge_kinds or edge.get("edge_kind") == "flow")
                and (run_roles is None or edge.get("run_role") in run_roles)
            ]
            continue
        if key == "collapsed_groups" and isinstance(value, list):
            filtered[key] = [
                group
                for group in value
                if group.get("target") in node_ids
            ]
            continue
        filtered[key] = value
    filtered["stats"] = {
        **dict((edge_layers.get("stats") or {}) if isinstance(edge_layers.get("stats"), dict) else {}),
        "filtered_default_edge_count": len(filtered.get("default") or []),
        "filtered_full_edge_count": len(filtered.get("full_provenance") or []),
    }
    return filtered


def _filter_timeline(timeline: Any, node_ids: set[str]) -> dict[str, Any]:
    if not isinstance(timeline, dict):
        return {"items": []}
    items = [
        dict(item)
        for item in timeline.get("items") or []
        if item.get("node_id") in node_ids
    ]
    return {"items": items}


def _filter_graph_diff(diff: Any, node_ids: set[str], edge_ids: set[str]) -> dict[str, Any]:
    if not isinstance(diff, dict):
        return {"comparisons": [], "status_counts": {}}
    result = {
        "schema_version": diff.get("schema_version", 1),
        "base_run": diff.get("base_run"),
        "fork_runs": list(diff.get("fork_runs") or []),
        "comparisons": [],
        "status_counts": _count_values(()),
    }
    status_counts: dict[str, int] = {}
    for comparison in diff.get("comparisons") or []:
        filtered_alignments = [
            dict(alignment)
            for alignment in comparison.get("alignments") or []
            if alignment.get("base_node_id") in node_ids or alignment.get("fork_node_id") in node_ids
        ]
        filtered = {
            key: value
            for key, value in comparison.items()
            if key
            not in {
                "alignments",
                "new_node_ids",
                "missing_node_ids",
                "changed_node_ids",
                "unchanged_node_ids",
                "downstream_node_ids",
                "boundary_edge_ids",
                "summary",
            }
        }
        filtered["alignments"] = filtered_alignments
        filtered["new_node_ids"] = [node_id for node_id in comparison.get("new_node_ids") or [] if node_id in node_ids]
        filtered["missing_node_ids"] = [
            node_id for node_id in comparison.get("missing_node_ids") or [] if node_id in node_ids
        ]
        filtered["changed_node_ids"] = [
            node_id for node_id in comparison.get("changed_node_ids") or [] if node_id in node_ids
        ]
        filtered["unchanged_node_ids"] = [
            node_id for node_id in comparison.get("unchanged_node_ids") or [] if node_id in node_ids
        ]
        filtered["downstream_node_ids"] = [
            node_id for node_id in comparison.get("downstream_node_ids") or [] if node_id in node_ids
        ]
        filtered["boundary_edge_ids"] = [
            edge_id for edge_id in comparison.get("boundary_edge_ids") or [] if edge_id in edge_ids
        ]
        filtered["summary"] = {
            "changed": len(filtered["changed_node_ids"]),
            "unchanged": len(filtered["unchanged_node_ids"]),
            "new": len(filtered["new_node_ids"]),
            "missing": len(filtered["missing_node_ids"]),
        }
        for key, count in filtered["summary"].items():
            status_counts[key] = status_counts.get(key, 0) + count
        result["comparisons"].append(filtered)
    result["status_counts"] = status_counts
    return result


def _derive_graph_diff(
    runs: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    for node in nodes:
        node["diff"] = {"status": "baseline", "comparisons": []}

    base_runs = [run for run in runs if run.get("run_role") == "base"]
    fork_runs = [run for run in runs if run.get("run_role") == "fork"]
    if not fork_runs:
        return {
            "schema_version": 1,
            "base_run": base_runs[0]["run_id"] if base_runs else None,
            "fork_runs": [],
            "comparisons": [],
            "status_counts": {"baseline": len(nodes)},
        }

    nodes_by_id = {node["id"]: node for node in nodes}
    nodes_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_run[str(node.get("run_id"))].append(node)
    for run_nodes in nodes_by_run.values():
        run_nodes.sort(key=_node_order_key)

    comparisons: list[dict[str, Any]] = []
    aggregate_status_counts: dict[str, int] = {}
    first_base_run_id: str | None = base_runs[0]["run_id"] if base_runs else None

    for fork_run in fork_runs:
        fork_run_id = str(fork_run.get("run_id"))
        base_run_id = str(fork_run.get("base_run") or first_base_run_id or "")
        if not base_run_id:
            continue
        comparison_id = f"diff:{base_run_id}..{fork_run_id}"
        base_nodes = nodes_by_run.get(base_run_id, [])
        fork_nodes = nodes_by_run.get(fork_run_id, [])
        breakpoint_record_uid = fork_run.get("breakpoint_record_uid")
        boundary_edges = [
            edge
            for edge in edges
            if edge.get("edge_kind") == "fork"
            and edge.get("source", "").startswith(f"{base_run_id}:")
            and edge.get("target", "").startswith(f"{fork_run_id}:")
        ]
        if breakpoint_record_uid:
            boundary_edges = [
                edge for edge in boundary_edges if edge.get("source") == f"{base_run_id}:{breakpoint_record_uid}"
            ] or boundary_edges
        boundary_source_id = boundary_edges[0].get("source") if boundary_edges else None
        boundary_target_id = boundary_edges[0].get("target") if boundary_edges else None
        base_alignment_nodes = _base_nodes_after_breakpoint(
            base_nodes,
            breakpoint_record_uid=str(breakpoint_record_uid or ""),
            boundary_source_id=boundary_source_id,
        )
        alignments = _align_base_fork_nodes(
            base_alignment_nodes,
            fork_nodes,
            preferred_pairs=[
                (
                    boundary_source_id,
                    boundary_target_id,
                    "fork_boundary",
                    str(breakpoint_record_uid or ""),
                )
            ],
        )
        paired_base_ids = {base_node["id"] for base_node, _, _, _ in alignments}
        paired_fork_ids = {fork_node["id"] for _, fork_node, _, _ in alignments}
        changed_node_ids: list[str] = []
        unchanged_node_ids: list[str] = []
        alignment_items: list[dict[str, Any]] = []

        for base_node, fork_node, method, key in alignments:
            payload_diff = _compare_node_payloads(base_node, fork_node)
            changed_fields = [
                field
                for field, details in payload_diff.items()
                if details.get("changed")
            ]
            status = "changed" if changed_fields else "unchanged"
            if status == "changed":
                changed_node_ids.extend([base_node["id"], fork_node["id"]])
            else:
                unchanged_node_ids.extend([base_node["id"], fork_node["id"]])

            alignment = {
                "base_node_id": base_node["id"],
                "fork_node_id": fork_node["id"],
                "status": status,
                "alignment_method": method,
                "alignment_key": key,
                "changed_fields": changed_fields,
                "diffs": payload_diff,
            }
            alignment_items.append(alignment)
            _append_node_diff(
                base_node,
                comparison_id=comparison_id,
                status=status,
                counterpart_id=fork_node["id"],
                alignment_method=method,
                alignment_key=key,
                changed_fields=changed_fields,
            )
            _append_node_diff(
                fork_node,
                comparison_id=comparison_id,
                status=status,
                counterpart_id=base_node["id"],
                alignment_method=method,
                alignment_key=key,
                changed_fields=changed_fields,
            )

        missing_node_ids = [node["id"] for node in base_alignment_nodes if node["id"] not in paired_base_ids]
        new_node_ids = [node["id"] for node in fork_nodes if node["id"] not in paired_fork_ids]
        for node_id in missing_node_ids:
            _append_node_diff(nodes_by_id[node_id], comparison_id=comparison_id, status="missing")
        for node_id in new_node_ids:
            _append_node_diff(nodes_by_id[node_id], comparison_id=comparison_id, status="new")

        downstream_node_ids = _derive_downstream_after_fork(
            fork_run_id=fork_run_id,
            base_run_id=base_run_id,
            breakpoint_record_uid=str(breakpoint_record_uid or ""),
            boundary_source_id=boundary_source_id,
            boundary_target_id=boundary_target_id,
            nodes_by_id=nodes_by_id,
            nodes_by_run=nodes_by_run,
            edges=edges,
        )

        summary = {
            "changed": len(changed_node_ids),
            "unchanged": len(unchanged_node_ids),
            "new": len(new_node_ids),
            "missing": len(missing_node_ids),
        }
        for key, count in summary.items():
            aggregate_status_counts[key] = aggregate_status_counts.get(key, 0) + count
        comparisons.append(
            {
                "id": comparison_id,
                "base_run": base_run_id,
                "fork_run": fork_run_id,
                "breakpoint": {
                    "record_uid": breakpoint_record_uid,
                    "base_node_id": boundary_source_id or (
                        f"{base_run_id}:{breakpoint_record_uid}" if breakpoint_record_uid else None
                    ),
                    "fork_node_id": boundary_target_id,
                    "edge_ids": [edge["id"] for edge in boundary_edges if edge.get("id")],
                },
                "boundary_edge_ids": [edge["id"] for edge in boundary_edges if edge.get("id")],
                "alignments": alignment_items,
                "changed_node_ids": list(dict.fromkeys(changed_node_ids)),
                "unchanged_node_ids": list(dict.fromkeys(unchanged_node_ids)),
                "new_node_ids": new_node_ids,
                "missing_node_ids": missing_node_ids,
                "downstream_node_ids": downstream_node_ids,
                "summary": summary,
            }
        )

    for node in nodes:
        node["diff"]["status"] = _aggregate_node_diff_status(node["diff"].get("comparisons") or [])

    return {
        "schema_version": 1,
        "base_run": first_base_run_id,
        "fork_runs": [run.get("run_id") for run in fork_runs if run.get("run_id")],
        "comparisons": comparisons,
        "status_counts": aggregate_status_counts,
    }


def _align_base_fork_nodes(
    base_nodes: list[dict[str, Any]],
    fork_nodes: list[dict[str, Any]],
    *,
    preferred_pairs: list[tuple[str | None, str | None, str, str]] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], str, str]]:
    alignments: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []
    used_base: set[str] = set()
    used_fork: set[str] = set()
    base_by_id = {node["id"]: node for node in base_nodes}
    fork_by_id = {node["id"]: node for node in fork_nodes}

    for base_id, fork_id, method, key in preferred_pairs or []:
        if not base_id or not fork_id:
            continue
        base_node = base_by_id.get(base_id)
        fork_node = fork_by_id.get(fork_id)
        if not base_node or not fork_node:
            continue
        if base_id in used_base or fork_id in used_fork:
            continue
        used_base.add(base_id)
        used_fork.add(fork_id)
        alignments.append((base_node, fork_node, method, key))

    def pair_by_key(method: str, key_fn: Any) -> None:
        base_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        fork_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in base_nodes:
            if node["id"] in used_base:
                continue
            key = key_fn(node)
            if key:
                base_by_key[str(key)].append(node)
        for node in fork_nodes:
            if node["id"] in used_fork:
                continue
            key = key_fn(node)
            if key:
                fork_by_key[str(key)].append(node)
        for key in sorted(set(base_by_key) & set(fork_by_key)):
            bases = sorted(base_by_key[key], key=_node_order_key)
            forks = sorted(fork_by_key[key], key=_node_order_key)
            for base_node, fork_node in zip(bases, forks):
                if base_node["id"] in used_base or fork_node["id"] in used_fork:
                    continue
                used_base.add(base_node["id"])
                used_fork.add(fork_node["id"])
                alignments.append((base_node, fork_node, method, key))

    pair_by_key(
        "callsite",
        lambda node: _join_key(_node_callsite_key(node), node.get("kind")),
    )
    pair_by_key(
        "path",
        lambda node: _join_key(node.get("path_id"), node.get("kind")),
    )
    return alignments


def _append_node_diff(
    node: dict[str, Any],
    *,
    comparison_id: str,
    status: str,
    counterpart_id: str | None = None,
    alignment_method: str | None = None,
    alignment_key: str | None = None,
    changed_fields: list[str] | None = None,
) -> None:
    node.setdefault("diff", {"status": "baseline", "comparisons": []})
    node["diff"].setdefault("comparisons", []).append(
        {
            "comparison_id": comparison_id,
            "status": status,
            "counterpart_id": counterpart_id,
            "alignment_method": alignment_method,
            "alignment_key": alignment_key,
            "changed_fields": changed_fields or [],
        }
    )


def _aggregate_node_diff_status(comparisons: list[dict[str, Any]]) -> str:
    if not comparisons:
        return "baseline"
    priority = {
        "new": 4,
        "missing": 4,
        "changed": 3,
        "unchanged": 2,
        "baseline": 1,
    }
    return max(
        (str(comparison.get("status") or "baseline") for comparison in comparisons),
        key=lambda status: priority.get(status, 0),
    )


def _compare_node_payloads(base_node: dict[str, Any], fork_node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base_record = base_node.get("record") or {}
    fork_record = fork_node.get("record") or {}
    fields = {
        "input": (base_record.get("input"), fork_record.get("input")),
        "output": (
            _strip_volatile_output(base_record.get("output")),
            _strip_volatile_output(fork_record.get("output")),
        ),
        "provenance": (
            (base_record.get("metadata") or {}).get("provenance"),
            (fork_record.get("metadata") or {}).get("provenance"),
        ),
    }
    diffs: dict[str, dict[str, Any]] = {}
    for field, (base_value, fork_value) in fields.items():
        base_canonical = _canonical_json(base_value)
        fork_canonical = _canonical_json(fork_value)
        diffs[field] = {
            "changed": base_canonical != fork_canonical,
            "base_preview": _shorten(base_canonical, 240),
            "fork_preview": _shorten(fork_canonical, 240),
        }
    return diffs


def _strip_volatile_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_volatile_output(item)
            for key, item in value.items()
            if key not in {"raw_response"}
        }
    if isinstance(value, list):
        return [_strip_volatile_output(item) for item in value]
    return value


def _derive_downstream_after_fork(
    *,
    fork_run_id: str,
    base_run_id: str,
    breakpoint_record_uid: str,
    boundary_source_id: str | None,
    boundary_target_id: str | None,
    nodes_by_id: dict[str, dict[str, Any]],
    nodes_by_run: dict[str, list[dict[str, Any]]],
    edges: list[dict[str, Any]],
) -> list[str]:
    selected: set[str] = set()
    if boundary_source_id:
        selected.add(boundary_source_id)
    if boundary_target_id:
        selected.add(boundary_target_id)

    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            outgoing[str(source)].append(str(target))

    if boundary_target_id:
        queue = deque([boundary_target_id])
        while queue:
            current = queue.popleft()
            for target in outgoing.get(current, []):
                if target in selected:
                    continue
                selected.add(target)
                queue.append(target)

    target_node = nodes_by_id.get(boundary_target_id or "")
    target_order = (target_node.get("order") or {}).get("run_index") if target_node else None
    for node in nodes_by_run.get(fork_run_id, []):
        node_order = (node.get("order") or {}).get("run_index")
        if target_order is None or node_order is None or node_order >= target_order:
            selected.add(node["id"])

    if breakpoint_record_uid:
        base_boundary_id = f"{base_run_id}:{breakpoint_record_uid}"
        base_boundary = nodes_by_id.get(base_boundary_id)
        base_order = (base_boundary.get("order") or {}).get("run_index") if base_boundary else None
        for node in nodes_by_run.get(base_run_id, []):
            node_order = (node.get("order") or {}).get("run_index")
            if base_order is not None and node_order is not None and node_order >= base_order:
                selected.add(node["id"])

    return [
        node["id"]
        for node in sorted(
            (nodes_by_id[node_id] for node_id in selected if node_id in nodes_by_id),
            key=_node_order_key,
        )
    ]


def _base_nodes_after_breakpoint(
    base_nodes: list[dict[str, Any]],
    *,
    breakpoint_record_uid: str,
    boundary_source_id: str | None,
) -> list[dict[str, Any]]:
    if not breakpoint_record_uid and not boundary_source_id:
        return base_nodes
    boundary_node = next(
        (
            node
            for node in base_nodes
            if node.get("id") == boundary_source_id or node.get("record_uid") == breakpoint_record_uid
        ),
        None,
    )
    if boundary_node is None:
        return base_nodes
    boundary_order = (boundary_node.get("order") or {}).get("run_index")
    if boundary_order is None:
        return base_nodes
    return [
        node
        for node in base_nodes
        if ((node.get("order") or {}).get("run_index") or 0) >= boundary_order
    ]


def _node_order_key(node: dict[str, Any]) -> tuple[int, int, str]:
    order = node.get("order") or {}
    return (
        int(order.get("index") or 10**9),
        int(order.get("run_index") or 10**9),
        str(node.get("id") or ""),
    )


def _node_callsite_key(node: dict[str, Any]) -> str | None:
    semantic = node.get("semantic") or {}
    if semantic.get("callsite_fingerprint"):
        return str(semantic["callsite_fingerprint"])
    callsite = node.get("callsite") or {}
    if not callsite:
        return None
    return _join_key(callsite.get("file"), callsite.get("function"), callsite.get("line"))


def _join_key(*parts: Any) -> str | None:
    cleaned = [str(part) for part in parts if part is not None and part != ""]
    if not cleaned:
        return None
    return "|".join(cleaned)


def _canonical_json(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _derive_branch_id(path_id: str | None) -> str | None:
    if not path_id:
        return None
    return path_id.split("/", 1)[0]


def _derive_run_roles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_info: dict[str, dict[str, Any]] = {}
    ordered_run_ids: list[str] = []
    fork_base_runs: set[str] = set()

    def ensure_run(run_id: str) -> dict[str, Any]:
        if run_id not in run_info:
            ordered_run_ids.append(run_id)
            run_info[run_id] = {
                "id": f"run:{run_id}",
                "run_id": run_id,
                "run_role": "base",
            }
        return run_info[run_id]

    for record in records:
        run_id = record.get("run_id") or record.get("_graph_run_id")
        record_kind = record.get("kind")
        fork_metadata = record.get("fork_metadata") or {}
        if run_id is None and record_kind in {"llm", "tool", "tool_call", "run"}:
            run_id = "synthetic_run"
        if run_id is None:
            continue
        if record_kind in {"llm", "tool", "tool_call", "edge"} and record.get("run_id") is None:
            record["run_id"] = run_id
        current = ensure_run(str(run_id))
        if fork_metadata.get("base_run"):
            fork_base_runs.add(str(fork_metadata["base_run"]))
        if fork_metadata.get("base_run") or fork_metadata.get("mode") == "fork":
            current["run_role"] = "fork"
            current["fork_metadata"] = fork_metadata
            if fork_metadata.get("base_run"):
                current["base_run"] = fork_metadata["base_run"]
            if fork_metadata.get("breakpoint_record_uid"):
                current["breakpoint_record_uid"] = fork_metadata["breakpoint_record_uid"]

    for base_run_id in fork_base_runs:
        if base_run_id in run_info:
            run_info[base_run_id]["run_role"] = "base"
    return [run_info[run_id] for run_id in ordered_run_ids]


def _derive_compare_fork_edges(
    records: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fork_records_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fork_metadata_by_run: dict[str, dict[str, Any]] = {}

    for record in records:
        run_id = record.get("run_id") or record.get("_graph_run_id")
        if not run_id:
            continue
        fork_metadata = record.get("fork_metadata") or {}
        if fork_metadata:
            fork_metadata_by_run.setdefault(str(run_id), fork_metadata)
        if record.get("kind") in {"llm", "tool", "tool_call"} and (fork_metadata or str(run_id) in fork_metadata_by_run):
            fork_records_by_run[str(run_id)].append(record)

    synthetic_edges: list[dict[str, Any]] = []
    for fork_run_id, fork_metadata in fork_metadata_by_run.items():
        base_run_id = fork_metadata.get("base_run")
        breakpoint_record_uid = fork_metadata.get("breakpoint_record_uid")
        if not base_run_id or not breakpoint_record_uid:
            continue
        source_id = f"{base_run_id}:{breakpoint_record_uid}"
        if source_id not in node_by_id:
            continue

        target_record = _choose_fork_boundary_record(fork_records_by_run.get(fork_run_id, []))
        if not target_record:
            continue
        target_id = f"{fork_run_id}:{target_record.get('record_uid')}"
        if target_id not in node_by_id:
            continue

        synthetic_edges.append(
            {
                "id": f"edge:{source_id}->{target_id}:fork",
                "source": source_id,
                "target": target_id,
                "from": source_id,
                "to": target_id,
                "edge_kind": "fork",
                "kind": "fork",
                "cross_run": True,
                "run_role": "fork",
                "summary": _derive_edge_summary("fork"),
                "metadata": {
                    "synthetic": True,
                    "base_run": base_run_id,
                    "fork_run": fork_run_id,
                    "breakpoint_record_uid": breakpoint_record_uid,
                    "target_record_uid": target_record.get("record_uid"),
                },
                "evidence_refs": [],
            }
        )
    return synthetic_edges


def _derive_tool_call_edges(
    records: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if record.get("kind") == "tool_call":
            tool_calls.append({**record, "_record_index": index})
        elif record.get("kind") == "tool":
            tools.append({**record, "_record_index": index})

    tool_by_call_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tool in tools:
        for tool_call_id in _tool_record_call_ids(tool):
            tool_by_call_id[str(tool_call_id)].append(tool)

    edges: list[dict[str, Any]] = []
    used_tool_node_ids: set[str] = set()
    for tool_call in tool_calls:
        run_id = str(tool_call.get("run_id") or tool_call.get("_graph_run_id") or "synthetic_run")
        record_uid = tool_call.get("record_uid")
        if not record_uid:
            continue
        tool_call_node_id = f"{run_id}:{record_uid}"
        if tool_call_node_id not in node_by_id:
            continue

        source_llm_record_uid = (tool_call.get("input") or {}).get("source_llm_record_uid")
        if source_llm_record_uid:
            llm_node_id = f"{run_id}:{source_llm_record_uid}"
            if llm_node_id in node_by_id:
                edges.append(
                    _synthetic_edge(
                        llm_node_id,
                        tool_call_node_id,
                        "llm_intent",
                        {
                            "synthetic": True,
                            "source": "tool_call",
                            "source_llm_record_uid": source_llm_record_uid,
                            "tool_call_record_uid": record_uid,
                        },
                        node_by_id,
                    )
                )

        if (tool_call.get("metadata") or {}).get("link_tool_executions") is False:
            continue

        matching_tool = _match_tool_execution(tool_call, tools, tool_by_call_id, used_tool_node_ids)
        if matching_tool is None:
            continue
        tool_node_id = f"{matching_tool.get('run_id') or matching_tool.get('_graph_run_id') or run_id}:{matching_tool.get('record_uid')}"
        if tool_node_id not in node_by_id:
            continue
        used_tool_node_ids.add(tool_node_id)
        edges.append(
            _synthetic_edge(
                tool_call_node_id,
                tool_node_id,
                "tool_execution",
                {
                    "synthetic": True,
                    "source": "tool_call",
                    "match": "tool_call_id" if _tool_call_id(tool_call) else "weak",
                    "tool_call_record_uid": record_uid,
                    "tool_record_uid": matching_tool.get("record_uid"),
                },
                node_by_id,
            )
        )
    return edges


def _synthetic_edge(
    source_id: str,
    target_id: str,
    edge_kind: str,
    metadata: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_node = node_by_id.get(source_id, {})
    target_node = node_by_id.get(target_id, {})
    cross_run = source_node.get("run_id") != target_node.get("run_id")
    run_role = "fork" if "fork" in {source_node.get("run_role"), target_node.get("run_role")} else "base"
    return {
        "id": f"edge:{source_id}->{target_id}:{edge_kind}",
        "source": source_id,
        "target": target_id,
        "from": source_id,
        "to": target_id,
        "edge_kind": edge_kind,
        "kind": edge_kind,
        "cross_run": cross_run,
        "run_role": run_role,
        "summary": _derive_edge_summary(edge_kind),
        "metadata": metadata,
        "evidence_refs": [],
    }


def _match_tool_execution(
    tool_call: dict[str, Any],
    tools: list[dict[str, Any]],
    tool_by_call_id: dict[str, list[dict[str, Any]]],
    used_tool_node_ids: set[str],
) -> dict[str, Any] | None:
    run_id = str(tool_call.get("run_id") or tool_call.get("_graph_run_id") or "synthetic_run")
    tool_call_id = _tool_call_id(tool_call)
    if tool_call_id:
        for tool in sorted(tool_by_call_id.get(str(tool_call_id), []), key=lambda item: int(item.get("_record_index") or 0)):
            tool_node_id = f"{tool.get('run_id') or tool.get('_graph_run_id') or run_id}:{tool.get('record_uid')}"
            if _same_run(tool_call, tool) and tool_node_id not in used_tool_node_ids:
                return tool

    candidates = [
        tool
        for tool in tools
        if _same_run(tool_call, tool)
        and int(tool.get("_record_index") or 0) > int(tool_call.get("_record_index") or 0)
        and _same_span(tool_call, tool)
        and _tool_name(tool_call) == _tool_name(tool)
        and _canonical_json(_tool_arguments(tool_call)) == _canonical_json(_tool_arguments(tool))
        and f"{tool.get('run_id') or tool.get('_graph_run_id') or run_id}:{tool.get('record_uid')}" not in used_tool_node_ids
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: int(item.get("_record_index") or 0))[0]


def _same_run(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_run = left.get("run_id") or left.get("_graph_run_id") or "synthetic_run"
    right_run = right.get("run_id") or right.get("_graph_run_id") or "synthetic_run"
    return str(left_run) == str(right_run)


def _same_span(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_spans = (left.get("metadata") or {}).get("spans") or []
    right_spans = (right.get("metadata") or {}).get("spans") or []
    left_key = _span_key(left_spans)
    right_key = _span_key(right_spans)
    if left_key is not None and left_key == right_key:
        return True
    left_langgraph = _langgraph_span_keys(left_spans)
    right_langgraph = _langgraph_span_keys(right_spans)
    common_count = 0
    for left_span, right_span in zip(left_langgraph, right_langgraph):
        if left_span != right_span:
            break
        common_count += 1
    return common_count >= 2


def _span_key(spans: Any) -> str | None:
    if not isinstance(spans, list):
        return None
    keys = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        keys.append(_join_key(span.get("kind"), span.get("name")))
    keys = [key for key in keys if key]
    if not keys:
        return None
    return _canonical_json(keys)


def _langgraph_span_keys(spans: Any) -> list[str]:
    if not isinstance(spans, list):
        return []
    keys = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        metadata = span.get("metadata") if isinstance(span.get("metadata"), dict) else {}
        if span.get("kind") != "langgraph_node" and metadata.get("framework") != "langgraph":
            continue
        key = _join_key(span.get("kind"), span.get("name"))
        if key:
            keys.append(key)
    return keys


def _tool_call_id(record: dict[str, Any]) -> Any:
    input_payload = record.get("input") or {}
    return input_payload.get("tool_call_id")


def _tool_record_call_ids(record: dict[str, Any]) -> list[Any]:
    input_payload = record.get("input") or {}
    metadata = record.get("metadata") or {}
    values = [
        metadata.get("tool_call_id"),
        input_payload.get("tool_call_id"),
        ((input_payload.get("tool_call") or {}) if isinstance(input_payload.get("tool_call"), dict) else {}).get("id"),
    ]
    return [value for value in values if value is not None and value != ""]


def _tool_name(record: dict[str, Any]) -> str | None:
    input_payload = record.get("input") or {}
    metadata = record.get("metadata") or {}
    return _first_non_empty(
        input_payload.get("tool_name"),
        input_payload.get("name"),
        metadata.get("tool_name"),
        ((input_payload.get("tool_call") or {}) if isinstance(input_payload.get("tool_call"), dict) else {}).get("name"),
    )


def _tool_arguments(record: dict[str, Any]) -> Any:
    input_payload = record.get("input") or {}
    if input_payload.get("arguments") is not None:
        arguments = input_payload.get("arguments")
        if isinstance(arguments, dict) and isinstance(arguments.get("tool_call"), dict):
            return _normalize_tool_arguments(arguments["tool_call"].get("args") or arguments["tool_call"].get("arguments"))
        return _normalize_tool_arguments(arguments)
    if isinstance(input_payload.get("tool_call"), dict):
        tool_call = input_payload["tool_call"]
        return _normalize_tool_arguments(tool_call.get("args") if tool_call.get("args") is not None else tool_call.get("arguments"))
    if input_payload.get("input") is not None:
        return _normalize_tool_arguments(input_payload.get("input"))
    return None


def _normalize_tool_arguments(arguments: Any) -> Any:
    if isinstance(arguments, dict) and set(arguments) == {"input"}:
        return arguments["input"]
    return arguments


def _choose_fork_boundary_record(fork_records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not fork_records:
        return None
    return fork_records[0]


def _derive_node_title(record: dict[str, Any]) -> str:
    kind = record.get("kind")
    metadata = record.get("metadata") or {}
    if kind == "tool":
        return str(
            metadata.get("tool_name")
            or record.get("input", {}).get("tool_name")
            or record.get("input", {}).get("name")
            or "tool"
        )
    if kind == "tool_call":
        return str(record.get("input", {}).get("tool_name") or record.get("input", {}).get("name") or "tool_call")
    if kind == "llm":
        spans = metadata.get("spans") or []
        if spans and spans[0].get("name"):
            return str(spans[0]["name"])
        semantic_fingerprint = (metadata.get("semantic") or {}).get("callsite_fingerprint")
        if semantic_fingerprint:
            return str(semantic_fingerprint.replace("\\", "/").split("/")[-1])
        provider = record.get("input", {}).get("provider")
        api = record.get("input", {}).get("api")
        if provider and api:
            return f"{provider}.{api}"
        if provider:
            return str(provider)
        return "llm"
    return str(kind or "node")


def _derive_node_summary(record: dict[str, Any]) -> str:
    input_preview = _derive_input_preview(record)
    output_preview = _derive_output_preview(record)
    if input_preview and output_preview:
        return _shorten(f"{input_preview} -> {output_preview}", 160)
    if input_preview:
        return input_preview
    if output_preview:
        return output_preview
    if record.get("kind") == "tool":
        return "Tool call"
    if record.get("kind") == "tool_call":
        return "Tool intent"
    if record.get("kind") == "llm":
        return "LLM call"
    return str(record.get("record_uid") or record.get("kind") or "")


def _derive_node_display(record: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    kind = record.get("kind") or node.get("kind")
    if kind == "tool":
        return _derive_tool_display(record, node)
    if kind == "tool_call":
        return _derive_tool_call_display(record, node)
    if kind == "llm":
        return _derive_llm_display(record, node)
    index = (node.get("order") or {}).get("run_index") or (node.get("order") or {}).get("index")
    return {
        "title": str(node.get("title") or node.get("id") or "节点"),
        "summary": str(node.get("summary") or ""),
        "kind_label": "节点",
        "stage": "node",
        "tone": "node",
        "ordinal": index,
    }


def _derive_tool_call_display(record: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    input_payload = record.get("input") or {}
    tool_name = str(input_payload.get("tool_name") or input_payload.get("name") or node.get("title") or "tool_call")
    arguments = input_payload.get("arguments")
    target = _derive_tool_target(tool_name, arguments, {})
    title = f"请求工具：{target}" if target else f"请求工具：{tool_name}"
    summary = _shorten(str(node.get("summary") or ""), 180)
    return {
        "title": _shorten(title, 86),
        "summary": summary,
        "kind_label": "工具意图",
        "stage": "tool_request",
        "tone": "tool_call",
        "tool_name": tool_name,
        "ordinal": (node.get("order") or {}).get("run_index") or (node.get("order") or {}).get("index"),
    }


def _derive_tool_display(record: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    input_payload = record.get("input") or {}
    tool_name = str(input_payload.get("tool_name") or input_payload.get("name") or node.get("title") or "tool")
    short_tool = tool_name.split(":")[-1]
    arguments = input_payload.get("arguments")
    output_payload = record.get("output") or {}
    error = record.get("error")
    target = _derive_tool_target(short_tool, arguments, output_payload)
    status_label = "工具失败" if error is not None else "工具调用"
    title = f"{_tool_display_verb(short_tool)}：{target}" if target else _tool_display_verb(short_tool)
    summary = _derive_tool_display_summary(short_tool, arguments, output_payload, error)
    return {
        "title": _shorten(title, 86),
        "summary": _shorten(summary or str(node.get("summary") or ""), 180),
        "kind_label": status_label,
        "stage": _tool_display_stage(short_tool),
        "tone": "error" if error is not None else "tool",
        "tool_name": tool_name,
        "ordinal": (node.get("order") or {}).get("run_index") or (node.get("order") or {}).get("index"),
    }


def _derive_llm_display(record: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    output_payload = record.get("output") or {}
    input_payload = record.get("input") or {}
    output_text = _shorten(str(output_payload.get("content") or ""), 220)
    messages = input_payload.get("messages") or []
    last_input = ""
    for message in reversed(messages):
        content = _message_content(message)
        if content:
            last_input = content
            break
    tool_calls = output_payload.get("tool_calls") or []
    content_blob = " ".join([last_input, output_text]).lower()
    status = str(node.get("status") or "")

    if status == "override":
        title = "复写后的模型回答"
        stage = "fork"
        kind_label = "复写回答"
    elif tool_calls:
        names = [
            str((call.get("function") or {}).get("name") or call.get("name") or "")
            for call in tool_calls
            if isinstance(call, dict)
        ]
        title = f"请求工具：{', '.join(name for name in names if name) or '工具调用'}"
        stage = "tool_request"
        kind_label = "模型决策"
    elif _looks_final_answer(content_blob):
        title = "形成最终回答"
        stage = "final"
        kind_label = "最终回答"
    elif "tool_result" in content_blob or "successful tool call" in content_blob or "search results" in content_blob:
        title = "分析工具结果"
        stage = "analysis"
        kind_label = "模型分析"
    elif "search" in content_blob or "query" in content_blob or "evidence" in content_blob:
        title = "规划证据检索"
        stage = "planning"
        kind_label = "模型规划"
    else:
        ordinal = (node.get("order") or {}).get("run_index") or (node.get("order") or {}).get("index")
        title = f"模型调用 #{ordinal}" if ordinal else "模型调用"
        stage = "llm"
        kind_label = "模型调用"

    summary = output_text or _shorten(last_input, 180) or str(node.get("summary") or "")
    return {
        "title": _shorten(title, 86),
        "summary": _shorten(summary, 180),
        "kind_label": kind_label,
        "stage": stage,
        "tone": "llm",
        "ordinal": (node.get("order") or {}).get("run_index") or (node.get("order") or {}).get("index"),
    }


def _derive_tool_target(tool_name: str, arguments: Any, output_payload: dict[str, Any]) -> str:
    if isinstance(arguments, dict):
        for key in ("query", "url", "path", "request", "task"):
            if arguments.get(key):
                return _shorten(str(arguments[key]), 72)
        if tool_name in {"record_forecast", "final"}:
            prediction = arguments.get("prediction") or arguments.get("answer")
            if prediction is not None:
                return f"prediction={prediction}"
        visible = {
            key: value
            for key, value in arguments.items()
            if not any(token in key.lower() for token in ("prompt", "system", "message", "content", "scratch"))
        }
        if visible:
            return _shorten(json.dumps(visible, ensure_ascii=False, sort_keys=True), 72)
    value = output_payload.get("value")
    if isinstance(value, dict):
        for key in ("title", "summary", "result"):
            if value.get(key):
                return _shorten(str(value[key]), 72)
    return ""


def _derive_tool_display_summary(
    tool_name: str,
    arguments: Any,
    output_payload: dict[str, Any],
    error: Any,
) -> str:
    if error is not None:
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or error)
        return str(error)
    if tool_name == "record_forecast" and isinstance(arguments, dict):
        return str(arguments.get("rationale") or arguments.get("prediction") or "记录最终结果")
    value = output_payload.get("value")
    if value is not None:
        return _shorten(str(value), 180)
    result = output_payload.get("result")
    if result is not None:
        return _shorten(str(result), 180)
    if isinstance(arguments, dict):
        return _shorten(json.dumps(arguments, ensure_ascii=False, sort_keys=True), 180)
    return ""


def _tool_display_verb(tool_name: str) -> str:
    lowered = tool_name.lower()
    if "search" in lowered:
        return "搜索"
    if "read" in lowered or "snapshot" in lowered:
        return "读取"
    if "write" in lowered or "record" in lowered:
        return "记录"
    if "calculate" in lowered or "python" in lowered or "execute" in lowered:
        return "计算"
    if "list" in lowered:
        return "列出"
    if "delete" in lowered:
        return "删除"
    if "move" in lowered:
        return "移动"
    return tool_name


def _tool_display_stage(tool_name: str) -> str:
    lowered = tool_name.lower()
    if "search" in lowered:
        return "search"
    if "read" in lowered or "snapshot" in lowered:
        return "read"
    if "record" in lowered or "final" in lowered:
        return "final"
    if "calculate" in lowered or "python" in lowered or "execute" in lowered:
        return "compute"
    return "tool"


def _looks_final_answer(text: str) -> bool:
    return any(token in text for token in ("final answer", "最终", "boxed", "prediction", "forecast recorded", "answer:"))


def _derive_node_actions(record: dict[str, Any], run_role: str) -> list[dict[str, Any]]:
    record_uid = record.get("record_uid")
    run_id = record.get("run_id") or record.get("_graph_run_id") or "synthetic_run"
    node_id = f"{run_id}:{record_uid}" if record_uid else str(run_id)
    actions = [
        _action("focus.upstream", "Focus upstream", "Show upstream causes in this browser.", static_enabled=True),
        _action("focus.downstream", "Focus downstream", "Show downstream effects in this browser.", static_enabled=True),
        _action(
            "copy.node_id",
            "Copy node id",
            "Copy the stable graph node id.",
            static_enabled=True,
            params={"value": node_id},
        ),
        _action(
            "copy.record_uid",
            "Copy record UID",
            "Copy the replay record UID.",
            static_enabled=True,
            params={"value": record_uid},
        ),
        _action("show.raw_record", "Show raw record", "Open the raw record payload tab.", static_enabled=True),
    ]
    if record.get("kind") == "llm":
        replay_breakpoint_snippet = (
            "python -m replay python "
            f"--base-run {run_id} "
            f"--breakpoint-record-uid {record_uid} "
            "/absolute/path/to/script.py [script_args...]"
        )
        override_output_snippet = (
            "python -m replay python "
            f"--base-run {run_id} "
            f"--breakpoint-record-uid {record_uid} "
            "--override-output 'OVERRIDE_OUTPUT_HERE' "
            "/absolute/path/to/script.py [script_args...]"
        )
        actions.extend(
            [
                _action(
                    "copy_cli_snippet.replay_breakpoint",
                    "Copy replay breakpoint CLI",
                    "Copy a CLI template for replaying from this LLM record.",
                    static_enabled=True,
                    params={
                        "record_uid": record_uid,
                        "run_role": run_role,
                        "snippet": replay_breakpoint_snippet,
                    },
                ),
                _action(
                    "copy_cli_snippet.override_output_fork",
                    "Copy override fork CLI",
                    "Copy a CLI template for overriding this LLM output and forking.",
                    static_enabled=True,
                    params={
                        "record_uid": record_uid,
                        "run_role": run_role,
                        "snippet": override_output_snippet,
                    },
                ),
                _action(
                    "replay.breakpoint",
                    "Replay from here",
                    "Run replay from this LLM record.",
                    static_enabled=False,
                    static_disabled_reason="Static HTML cannot execute replay. Use the copyable CLI template.",
                    workbench_disabled_reason="Workbench action API is planned for a later phase.",
                    params={"record_uid": record_uid, "run_role": run_role},
                ),
                _action(
                    "replay.override_output",
                    "Override output and fork",
                    "Patch this LLM output and fork the run.",
                    static_enabled=False,
                    static_disabled_reason="Static HTML cannot execute override/fork. Use the copyable CLI template.",
                    workbench_disabled_reason="Workbench action API is planned for a later phase.",
                    params={"record_uid": record_uid, "run_role": run_role},
                ),
                _action(
                    "replay.rerun_descendants",
                    "Rerun descendants",
                    "Rerun affected downstream calls.",
                    static_enabled=False,
                    static_disabled_reason="Generic rerun-from-node is not available in static HTML.",
                    workbench_disabled_reason="Generic rerun semantics are not exposed yet.",
                ),
            ]
        )
    elif record.get("kind") == "tool":
        actions.extend(
            [
                _action(
                    "replay.breakpoint",
                    "Replay from here",
                    "Tool breakpoints are not supported yet.",
                    static_enabled=False,
                    static_disabled_reason="Breakpoints currently target LLM records only.",
                    workbench_disabled_reason="Tool-node breakpoint replay is not exposed yet.",
                ),
                _action(
                    "replay.override_output",
                    "Override output and fork",
                    "Tool output override is not supported yet.",
                    static_enabled=False,
                    static_disabled_reason="Tool-node override is not exposed yet.",
                    workbench_disabled_reason="Tool-node override/fork is not exposed yet.",
                ),
            ]
        )
    return actions


def _action(
    action: str,
    label: str,
    description: str,
    *,
    static_enabled: bool,
    static_disabled_reason: str | None = None,
    workbench_disabled_reason: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "label": label,
        "description": description,
        "enabled": static_enabled,
        "params": params or {},
        "availability": {
            "static_html": {
                "enabled": static_enabled,
                "mode": "browser" if static_enabled else "disabled",
                "disabled_reason": static_disabled_reason,
            },
            "workbench": {
                "enabled": False,
                "mode": "api",
                "disabled_reason": workbench_disabled_reason or "Workbench mode is not implemented in this phase.",
            },
        },
    }


def _derive_evidence_items(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[tuple[str, str, str], list[str]]]:
    evidence_items: list[dict[str, Any]] = []
    node_evidence_refs: dict[str, list[str]] = defaultdict(list)
    edge_evidence_refs: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    next_id = 1

    def add_evidence(
        *,
        evidence_kind: str,
        label: str,
        source_refs: list[str],
        target_refs: list[str],
        details: dict[str, Any],
        attach_to_nodes: list[str] | None = None,
        attach_to_edges: list[tuple[str, str, str]] | None = None,
    ) -> None:
        nonlocal next_id
        evidence_id = f"ev_{next_id:06d}"
        next_id += 1
        evidence_items.append(
            {
                "id": evidence_id,
                "evidence_kind": evidence_kind,
                "label": label,
                "source_refs": source_refs,
                "target_refs": target_refs,
                "details": details,
            }
        )
        for node_id in attach_to_nodes or []:
            node_evidence_refs[node_id].append(evidence_id)
        for edge_key in attach_to_edges or []:
            edge_evidence_refs[edge_key].append(evidence_id)

    for record in records:
        record_kind = record.get("kind")
        run_id = record.get("run_id") or record.get("_graph_run_id") or "synthetic_run"
        if record_kind in {"llm", "tool", "tool_call"}:
            record_uid = record.get("record_uid")
            if not record_uid:
                continue
            node_id = f"{run_id}:{record_uid}"
            metadata = record.get("metadata") or {}
            provenance = metadata.get("provenance") or {}
            data_sources = provenance.get("data_sources") or []
            control_sources = provenance.get("control_sources") or []
            semantic_fingerprint = (metadata.get("semantic") or {}).get("callsite_fingerprint")
            spans = metadata.get("spans") or []
            filesystem_effects = (record.get("effects") or {}).get("filesystem")

            if data_sources:
                add_evidence(
                    evidence_kind="provenance_summary",
                    label="Data provenance",
                    source_refs=_source_refs(data_sources, fallback_run_id=str(run_id)),
                    target_refs=[node_id],
                    details={
                        "data_sources": data_sources,
                        "control_sources": [],
                    },
                    attach_to_nodes=[node_id],
                )
            if control_sources:
                add_evidence(
                    evidence_kind="provenance_summary",
                    label="Control provenance",
                    source_refs=_source_refs(control_sources, fallback_run_id=str(run_id)),
                    target_refs=[node_id],
                    details={
                        "data_sources": [],
                        "control_sources": control_sources,
                    },
                    attach_to_nodes=[node_id],
                )
            if semantic_fingerprint:
                add_evidence(
                    evidence_kind="callsite_fingerprint",
                    label="Semantic callsite fingerprint",
                    source_refs=[],
                    target_refs=[node_id],
                    details={"semantic_fingerprint": semantic_fingerprint},
                    attach_to_nodes=[node_id],
                )
            if spans:
                add_evidence(
                    evidence_kind="span_summary",
                    label="Execution spans",
                    source_refs=[],
                    target_refs=[node_id],
                    details={"spans": spans},
                    attach_to_nodes=[node_id],
                )
            if filesystem_effects:
                add_evidence(
                    evidence_kind="filesystem_effects",
                    label="Filesystem effects",
                    source_refs=[],
                    target_refs=[node_id],
                    details=filesystem_effects,
                    attach_to_nodes=[node_id],
                )
        elif record_kind == "edge":
            from_ref = _source_ref(record.get("from"), fallback_run_id=str(run_id))
            to_ref = _source_ref(record.get("to"), fallback_run_id=str(run_id))
            if not from_ref or not to_ref:
                continue
            edge_kind = str(record.get("edge_kind") or "unknown")
            add_evidence(
                evidence_kind="edge_summary",
                label=_derive_edge_summary(edge_kind),
                source_refs=[from_ref],
                target_refs=[to_ref],
                details={
                    "edge_kind": edge_kind,
                    "metadata": record.get("metadata") or {},
                    "raw_edge_record": {key: value for key, value in record.items() if key != "_graph_run_id"},
                },
                attach_to_edges=[(from_ref, to_ref, edge_kind)],
            )

    return evidence_items, node_evidence_refs, edge_evidence_refs


def _derive_node_status(record: dict[str, Any], run_role: str) -> str:
    metadata = record.get("metadata") or {}
    if record.get("error") is not None:
        return "error"
    if metadata.get("override"):
        return "override"
    if metadata.get("matched_by"):
        return "replay"
    if run_role == "fork":
        return "live"
    return "recorded"


def _derive_node_provider(record: dict[str, Any]) -> str | None:
    provider = record.get("input", {}).get("provider")
    if provider:
        return str(provider)
    if record.get("kind") == "tool":
        return "local"
    if record.get("kind") == "tool_call":
        return "intent"
    return None


def _derive_node_api(record: dict[str, Any]) -> str | None:
    api = record.get("input", {}).get("api")
    if api:
        return str(api)
    method = record.get("input", {}).get("method")
    if method:
        return str(method)
    return None


def _derive_input_preview(record: dict[str, Any]) -> str:
    input_payload = record.get("input") or {}
    if record.get("kind") == "llm":
        messages = input_payload.get("messages") or []
        for message in reversed(messages):
            content = _message_content(message)
            if content:
                return _shorten(content)
    if record.get("kind") in {"tool", "tool_call"}:
        tool_name = input_payload.get("tool_name") or input_payload.get("name") or "tool"
        arguments = input_payload.get("arguments")
        if arguments is not None:
            return _shorten(f"{tool_name}({arguments})")
        return _shorten(f"{tool_name}()")
    return ""


def _derive_output_preview(record: dict[str, Any]) -> str:
    output_payload = record.get("output") or {}
    if record.get("kind") == "llm":
        content = output_payload.get("content")
        if isinstance(content, str) and content:
            return _shorten(content)
    if record.get("kind") == "tool":
        value = output_payload.get("value")
        if value is not None:
            return _shorten(str(value))
        result = output_payload.get("result")
        if result is not None:
            return _shorten(str(result))
    return ""


def _derive_edge_summary(edge_kind: str) -> str:
    if edge_kind == "data":
        return "Data influence"
    if edge_kind == "control":
        return "Control or branch influence"
    if edge_kind == "fork":
        return "Replay fork boundary"
    if edge_kind == "llm_intent":
        return "LLM requested tool"
    if edge_kind == "tool_execution":
        return "Tool execution matched"
    return f"{edge_kind} influence"


def _derive_default_title(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "Replay Graph"
    base_runs = [run["run_id"] for run in runs if run.get("run_role") == "base"]
    fork_runs = [run["run_id"] for run in runs if run.get("run_role") == "fork"]
    if base_runs and not fork_runs:
        return base_runs[0]
    if base_runs and fork_runs:
        return f"{base_runs[0]} + {len(fork_runs)} fork(s)"
    return runs[0]["run_id"]


def _append_group_child(groups_by_id: dict[str, dict[str, Any]], group: dict[str, Any], child_id: str) -> None:
    current = groups_by_id.setdefault(group["id"], {**group, "children": []})
    if child_id not in current["children"]:
        current["children"].append(child_id)


def _source_ref(source: dict[str, Any] | None, *, fallback_run_id: str) -> str | None:
    if not source:
        return None
    run_id = source.get("run_id") or fallback_run_id
    record_uid = source.get("record_uid")
    if not run_id or not record_uid:
        return None
    return f"{run_id}:{record_uid}"


def _source_refs(sources: list[dict[str, Any]], *, fallback_run_id: str) -> list[str]:
    return list(
        dict.fromkeys(
            source_ref
            for source in sources
            for source_ref in [_source_ref(source, fallback_run_id=fallback_run_id)]
            if source_ref is not None
        )
    )


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        data = message.get("data")
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            return data["content"]
    return ""


def _shorten(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _count_values(values: Iterable[Any]) -> dict[Any, int]:
    counts: dict[Any, int] = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _attach_node_degrees(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source:
            outgoing[str(source)] += 1
        if target:
            incoming[str(target)] += 1
    for node in nodes:
        node["degree"] = {
            "incoming": incoming.get(node["id"], 0),
            "outgoing": outgoing.get(node["id"], 0),
        }
