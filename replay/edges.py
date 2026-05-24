from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .semantic_runtime import Source


_INTERNAL_RUN_ID_KEY = "_graph_run_id"


def source_to_json(source: Source | dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, Source):
        item = {
            "run_id": source.run_id,
            "record_uid": source.record_uid,
            "kind": source.kind,
        }
        if source.path_id is not None:
            item["path_id"] = source.path_id
        return item

    item = {
        "run_id": source.get("run_id"),
        "record_uid": source.get("record_uid"),
        "kind": source.get("kind"),
    }
    if source.get("path_id") is not None:
        item["path_id"] = source.get("path_id")
    return item


def sources_to_json(sources: Iterable[Source | dict[str, Any]] | None) -> list[dict[str, Any]]:
    items = [source_to_json(source) for source in sources or ()]
    return sorted(
        items,
        key=lambda source: (
            str(source.get("run_id") or ""),
            str(source.get("record_uid") or ""),
            str(source.get("kind") or ""),
            str(source.get("path_id") or ""),
        ),
    )


def provenance_to_json(provenance: dict[str, Iterable[Source | dict[str, Any]]] | None) -> dict[str, list[dict[str, Any]]]:
    provenance = provenance or {}
    return {
        "data_sources": sources_to_json(provenance.get("data_sources")),
        "control_sources": sources_to_json(provenance.get("control_sources")),
    }


def make_edge_records(
    from_sources: Iterable[Source | dict[str, Any]] | None,
    to_source: Source | dict[str, Any],
    edge_kind: str,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    to_json = source_to_json(to_source)
    records: list[dict[str, Any]] = []
    for from_json in sources_to_json(from_sources):
        record: dict[str, Any] = {
            "schema_version": 2,
            "kind": "edge",
            "edge_kind": edge_kind,
            "from": from_json,
            "to": to_json,
        }
        if metadata is not None:
            record["metadata"] = metadata
        records.append(record)
    return records


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    jsonl_path = Path(path)
    records: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {jsonl_path}:{line_number}") from exc
            records.append(item)
    return records


def _load_graph_records_from_path(path: str | Path, run_id: str | None = None) -> list[dict[str, Any]]:
    jsonl_path = Path(path)
    record_run_id = run_id or jsonl_path.stem
    records: list[dict[str, Any]] = []
    for item in load_jsonl_records(jsonl_path):
        if isinstance(item, dict):
            item = dict(item)
            item.setdefault(_INTERNAL_RUN_ID_KEY, record_run_id)
        records.append(item)
    return records


def load_graph_records(
    paths: str | Path | Iterable[str | Path],
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if isinstance(paths, (str, Path)):
        return _load_graph_records_from_path(paths, run_id=run_id)

    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(_load_graph_records_from_path(path, run_id=run_id))
    return records


def build_orchestration_graph(records: Iterable[dict[str, Any]], run_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node_id_for_source(source: dict[str, Any], fallback_run_id: str | None = None) -> str | None:
        source_run_id = source.get("run_id") or fallback_run_id or run_id
        record_uid = source.get("record_uid")
        if not source_run_id or not record_uid:
            return None
        return f"{source_run_id}:{record_uid}"

    def add_source_node(source: dict[str, Any], fallback_run_id: str | None = None, record: dict[str, Any] | None = None) -> str | None:
        node_id = node_id_for_source(source, fallback_run_id)
        if node_id is None:
            return None
        node = nodes_by_id.setdefault(
            node_id,
            {
                "id": node_id,
                "run_id": source.get("run_id") or fallback_run_id or run_id,
                "record_uid": source.get("record_uid"),
                "kind": source.get("kind"),
            },
        )
        if source.get("path_id") is not None:
            node.setdefault("path_id", source.get("path_id"))
        if record is not None:
            node.setdefault("record", {key: value for key, value in record.items() if key != _INTERNAL_RUN_ID_KEY})
        return node_id

    for record in records:
        record_run_id = run_id or record.get("run_id") or record.get(_INTERNAL_RUN_ID_KEY)
        kind = record.get("kind")
        if kind in {"llm", "tool"}:
            add_source_node(
                {
                    "run_id": record_run_id,
                    "record_uid": record.get("record_uid"),
                    "kind": kind,
                    "path_id": record.get("path_id"),
                },
                record_run_id,
                record,
            )
        elif kind == "edge":
            from_source = source_to_json(record.get("from", {}))
            to_source = source_to_json(record.get("to", {}))
            from_id = add_source_node(from_source, record_run_id)
            to_id = add_source_node(to_source, record_run_id)
            if from_id is None or to_id is None:
                continue
            edge = {
                "from": from_id,
                "to": to_id,
                "edge_kind": record.get("edge_kind"),
                "source": from_source,
                "target": to_source,
            }
            if record.get("metadata") is not None:
                edge["metadata"] = record.get("metadata")
            edges.append(edge)

    return {
        "nodes": [nodes_by_id[node_id] for node_id in sorted(nodes_by_id)],
        "edges": edges,
    }
