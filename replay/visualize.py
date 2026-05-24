from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_XYFLOW_ASSET_DIR = _ROOT / "xyflow_assets"
_XYFLOW_JS_NAME = "xyflow-viewer.js"
_XYFLOW_CSS_NAME = "xyflow-viewer.css"


def record_label(record: dict[str, Any], mode: str = "compact") -> str:
    if mode != "compact":
        raise ValueError(f"Unsupported mode: {mode}")

    parts: list[str] = []
    title = _clean_text(record.get("title"))
    if title:
        parts.append(title)
    kind = _clean_text(record.get("kind"))
    if kind:
        parts.append(f"[{kind}]")
    path_id = _clean_text(record.get("path_id") or record.get("branch_id"))
    if path_id:
        parts.append(path_id)
    summary = _truncate(_clean_text(record.get("summary")), 60)
    if summary:
        parts.append(summary)
    return "\\n".join(parts) or _clean_text(record.get("id")) or "node"


def graph_ir_to_mermaid(
    ir: dict[str, Any],
    *,
    layout_direction: str = "LR",
    group_by: str = "none",
    mode: str = "compact",
) -> str:
    if layout_direction not in {"LR", "TD", "RL", "BT"}:
        raise ValueError(f"Unsupported layout_direction: {layout_direction}")
    if group_by not in {"none", "path", "span", "run"}:
        raise ValueError(f"Unsupported group_by: {group_by}")
    if mode != "compact":
        raise ValueError(f"Unsupported mode: {mode}")

    nodes = list(ir.get("graph", {}).get("nodes", []))
    edges = _default_display_edges(ir)
    runs = {
        run.get("run_id"): run
        for run in ir.get("graph", {}).get("runs", [])
        if run.get("run_id")
    }
    lines = [f"flowchart {layout_direction}"]

    if group_by != "none":
        grouped, ungrouped = _group_nodes(nodes, group_by)
        for group_key in sorted(grouped):
            lines.append(
                f'    subgraph {_subgraph_id_for_group(group_by, group_key)}["{_escape_label(_subgraph_label_for_group(group_by, group_key, runs))}"]'
            )
            for node in grouped[group_key]:
                lines.append(_node_line(node))
            lines.append("    end")
        for node in ungrouped:
            lines.append(_node_line(node))
    else:
        for node in nodes:
            lines.append(_node_line(node))

    for edge in edges:
        source = edge.get("source") or edge.get("from")
        target = edge.get("target") or edge.get("to")
        if not source or not target:
            continue
        lines.append(_edge_line(edge, str(source), str(target)))

    lines.extend(
        [
            "    classDef llm fill:#1f4e79,stroke:#7db8ff,color:#ffffff;",
            "    classDef tool fill:#6b3f16,stroke:#ffbf69,color:#ffffff;",
            "    classDef node fill:#374151,stroke:#d1d5db,color:#ffffff;",
            "    classDef base stroke-width:2px;",
            "    classDef fork stroke:#d946ef,stroke-width:3px;",
        ]
    )
    return "\n".join(lines) + "\n"


def write_mermaid_markdown(
    ir: dict[str, Any],
    output_path: str | Path,
    *,
    title: str | None = None,
    layout_direction: str = "LR",
    group_by: str = "none",
    mode: str = "compact",
) -> Path:
    path = Path(output_path)
    mermaid = graph_ir_to_mermaid(
        ir,
        layout_direction=layout_direction,
        group_by=group_by,
        mode=mode,
    )
    heading = title or ir.get("meta", {}).get("title")
    content_lines: list[str] = []
    if heading:
        content_lines.append(f"# {heading}")
        content_lines.append("")
    content_lines.extend(["```mermaid", mermaid.rstrip("\n"), "```", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(content_lines), encoding="utf-8")
    return path


def graph_ir_to_html(
    ir: dict[str, Any],
    *,
    title: str | None = None,
    asset_mode: str = "inline",
    renderer: str = "svg",
) -> str:
    if renderer == "xyflow":
        return graph_ir_to_xyflow_html(ir, title=title, asset_mode=asset_mode)
    if renderer != "svg":
        raise ValueError(f"Unsupported renderer: {renderer}")
    if asset_mode not in {"inline", "vendored"}:
        raise ValueError(f"Unsupported asset_mode: {asset_mode}")

    page_title = title or _clean_text(ir.get("meta", {}).get("title")) or "Replay Graph"
    assets = _inline_assets_html() if asset_mode == "inline" else _vendored_assets_html()
    node_kinds = _graph_ir_node_kinds(ir)
    edge_kinds = _graph_ir_edge_kinds(ir)
    run_roles = _graph_ir_run_roles(ir)
    diff_statuses = _graph_ir_diff_statuses(ir)
    return "".join(
        [
            "<!DOCTYPE html>\n",
            '<html lang="en">\n',
            "<head>\n",
            '  <meta charset="utf-8">\n',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n',
            f"  <title>{escape(page_title)}</title>\n",
            assets,
            "</head>\n",
            f'<body data-asset-mode="{escape(asset_mode)}">\n',
            '  <div id="app">\n',
            '    <header id="top-bar">\n',
            f'      <h1 class="app-title">{escape(page_title)}</h1>\n',
            '      <div id="top-stats" class="top-stats"></div>\n',
            '    </header>\n',
            '    <div id="shell">\n',
            '      <aside id="left-rail">\n',
            '        <section class="panel">\n',
            '          <h2>Runs</h2>\n',
            '          <div id="run-summary"></div>\n',
            '        </section>\n',
            '        <section class="panel control-panel">\n',
            '          <h2>Explore</h2>\n',
            '          <label class="control-field" for="search-input">Search</label>\n',
            '          <div class="control-row">\n',
            '            <input id="search-input" type="search" placeholder="node id, label, run, preview" />\n',
            '            <button type="button" data-action="search">Search</button>\n',
            '          </div>\n',
            '          <div id="search-results" class="search-results"></div>\n',
            '          <label class="control-field" for="focus-direction">Focus</label>\n',
            '          <div class="control-row">\n',
            '            <select id="focus-direction">\n',
            '              <option value="both">both</option>\n',
            '              <option value="upstream">upstream</option>\n',
            '              <option value="downstream">downstream</option>\n',
            '            </select>\n',
            '            <input id="focus-max-depth" type="number" min="1" step="1" placeholder="depth" />\n',
            '          </div>\n',
            '          <div class="control-row">\n',
            '            <button type="button" data-action="apply-focus">Apply focus</button>\n',
            '            <button type="button" data-action="clear-focus">Clear</button>\n',
            '          </div>\n',
            '          <div class="filter-block">\n',
            '            <h3>Node kinds</h3>\n',
            f'{_checkbox_group_html("node-kinds", node_kinds)}\n',
            '          </div>\n',
            '          <div class="filter-block">\n',
            '            <h3>Edge kinds</h3>\n',
            f'{_checkbox_group_html("edge-kinds", edge_kinds)}\n',
            '          </div>\n',
            '          <div class="filter-block">\n',
            '            <h3>Run roles</h3>\n',
            f'{_checkbox_group_html("run-roles", run_roles)}\n',
            '          </div>\n',
            '          <div class="filter-block">\n',
            '            <h3>Diff status</h3>\n',
            f'{_checkbox_group_html("diff-statuses", diff_statuses)}\n',
            '          </div>\n',
            '          <div class="control-row">\n',
            '            <button type="button" data-action="apply-filters">Apply filters</button>\n',
            '            <button type="button" data-action="reset-filters">Reset filters</button>\n',
            '          </div>\n',
            '          <label class="control-field" for="collapse-group-by">Collapse</label>\n',
            '          <div class="control-row">\n',
            '            <select id="collapse-group-by">\n',
            '              <option value="none">none</option>\n',
            '              <option value="run">run</option>\n',
            '              <option value="path">path</option>\n',
            '              <option value="span">span</option>\n',
            '            </select>\n',
            '            <button type="button" data-action="apply-grouping">Group</button>\n',
            '          </div>\n',
            '          <div class="control-row">\n',
            '            <button type="button" data-action="collapse-all-groups">Collapse all</button>\n',
            '            <button type="button" data-action="expand-all-groups">Expand all</button>\n',
            '          </div>\n',
            '          <div id="group-list" class="group-list"></div>\n',
            '          <div class="control-row">\n',
            '            <button type="button" data-action="show-fork-downstream">Fork downstream</button>\n',
            '            <button type="button" data-action="clear-fork-downstream">All graph</button>\n',
            '          </div>\n',
            '          <div class="control-row">\n',
            '            <button type="button" data-action="fit-view">Fit</button>\n',
            '            <button type="button" data-action="reset-view">Reset view</button>\n',
            '          </div>\n',
            '        </section>\n',
            '      </aside>\n',
            '      <main id="canvas-area">\n',
            '        <div id="graph-container">\n',
            '          <svg id="graph-svg" role="img" aria-label="Replay graph"></svg>\n',
            '          <div id="minimap" aria-hidden="true"></div>\n',
            '          <div id="canvas-controls"></div>\n',
            '        </div>\n',
            '        <section id="timeline-panel" aria-label="Replay timeline">\n',
            '          <div class="timeline-header"><h2>Timeline</h2><div id="timeline-stats"></div></div>\n',
            '          <div id="timeline-list"></div>\n',
            '        </section>\n',
            '      </main>\n',
            '      <aside id="inspector">\n',
            '        <div class="tabs" role="tablist" aria-label="Inspector tabs">\n',
            '          <button type="button" class="tab is-active" data-tab="summary">Summary</button>\n',
            '          <button type="button" class="tab" data-tab="payload">Payload</button>\n',
            '          <button type="button" class="tab" data-tab="evidence">Evidence</button>\n',
            '          <button type="button" class="tab" data-tab="actions">Actions</button>\n',
            '        </div>\n',
            '        <section class="tab-panel is-active" data-panel="summary"><h2>Summary</h2><div id="summary-panel"></div></section>\n',
            '        <section class="tab-panel" data-panel="payload"><h2>Payload</h2><div id="payload-panel"></div></section>\n',
            '        <section class="tab-panel" data-panel="evidence"><h2>Evidence</h2><div id="evidence-panel"></div></section>\n',
            '        <section class="tab-panel" data-panel="actions"><h2>Actions</h2><div id="actions-panel"></div></section>\n',
            '      </aside>\n',
            '    </div>\n',
            '  </div>\n',
            '  <script id="graph-ir-data" type="application/json">\n',
            f"{_safe_json_script_text(ir)}\n",
            '  </script>\n',
            "</body>\n",
            "</html>\n",
        ]
    )


def write_html_graph(
    ir: dict[str, Any],
    output_path: str | Path,
    *,
    title: str | None = None,
    asset_mode: str = "inline",
    renderer: str = "svg",
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if renderer == "xyflow":
        if asset_mode == "vendored":
            _write_xyflow_vendored_assets(path.parent)
        path.write_text(graph_ir_to_xyflow_html(ir, title=title, asset_mode=asset_mode), encoding="utf-8")
        return path
    if renderer != "svg":
        raise ValueError(f"Unsupported renderer: {renderer}")
    if asset_mode == "vendored":
        _write_vendored_assets(path.parent)
    path.write_text(graph_ir_to_html(ir, title=title, asset_mode=asset_mode), encoding="utf-8")
    return path


def graph_ir_to_xyflow_html(
    ir: dict[str, Any],
    *,
    title: str | None = None,
    asset_mode: str = "inline",
) -> str:
    if asset_mode not in {"inline", "vendored"}:
        raise ValueError(f"Unsupported asset_mode: {asset_mode}")

    page_title = title or _clean_text(ir.get("meta", {}).get("title")) or "Replay Graph"
    head_assets = _xyflow_inline_style_html() if asset_mode == "inline" else _xyflow_vendored_style_html()
    body_assets = _xyflow_inline_script_html() if asset_mode == "inline" else _xyflow_vendored_script_html()
    return "".join(
        [
            "<!DOCTYPE html>\n",
            '<html lang="en">\n',
            "<head>\n",
            '  <meta charset="utf-8">\n',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n',
            f"  <title>{escape(page_title)}</title>\n",
            head_assets,
            "</head>\n",
            f'<body data-asset-mode="{escape(asset_mode)}" data-renderer="xyflow">\n',
            '  <div id="root"></div>\n',
            '  <script id="graph-ir-data" type="application/json">\n',
            f"{_safe_json_script_text(ir)}\n",
            '  </script>\n',
            body_assets,
            "</body>\n",
            "</html>\n",
        ]
    )


def _inline_assets_html() -> str:
    return f"  <style>\n{_CSS}\n  </style>\n  <script>\n{_JS}\n  </script>\n"


def _vendored_assets_html() -> str:
    return (
        '  <link rel="stylesheet" href="visualize_assets/visualize.css">\n'
        '  <script src="visualize_assets/visualize.js"></script>\n'
    )


def _write_vendored_assets(output_dir: Path) -> None:
    asset_dir = output_dir / "visualize_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "visualize.css").write_text(_CSS, encoding="utf-8")
    (asset_dir / "visualize.js").write_text(_JS, encoding="utf-8")


def _xyflow_inline_style_html() -> str:
    css = _read_xyflow_asset(_XYFLOW_CSS_NAME)
    return f"  <style>\n{css}\n  </style>\n"


def _xyflow_inline_script_html() -> str:
    js = _read_xyflow_asset(_XYFLOW_JS_NAME)
    return f"  <script>\n{js}\n  </script>\n"


def _xyflow_vendored_style_html() -> str:
    return f'  <link rel="stylesheet" href="xyflow_assets/{_XYFLOW_CSS_NAME}">\n'


def _xyflow_vendored_script_html() -> str:
    return f'  <script src="xyflow_assets/{_XYFLOW_JS_NAME}"></script>\n'


def _write_xyflow_vendored_assets(output_dir: Path) -> None:
    asset_dir = output_dir / "xyflow_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / _XYFLOW_CSS_NAME).write_text(_read_xyflow_asset(_XYFLOW_CSS_NAME), encoding="utf-8")
    (asset_dir / _XYFLOW_JS_NAME).write_text(_read_xyflow_asset(_XYFLOW_JS_NAME), encoding="utf-8")


def _read_xyflow_asset(name: str) -> str:
    path = _XYFLOW_ASSET_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"XYFlow viewer asset not found: {path}. Run `npm run build:xyflow-viewer` from the repository root."
        )
    return path.read_text(encoding="utf-8")


def _safe_json_script_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2).replace("</", "<\\/")


def _graph_ir_node_kinds(ir: dict[str, Any]) -> list[str]:
    kinds = {
        _clean_text(node.get("kind"))
        for node in ir.get("graph", {}).get("nodes", [])
        if _clean_text(node.get("kind"))
    }
    return sorted(kinds)


def _default_display_edges(ir: dict[str, Any]) -> list[dict[str, Any]]:
    graph = ir.get("graph", {})
    edge_layers = graph.get("edge_layers") or {}
    default_edges = edge_layers.get("default")
    if isinstance(default_edges, list):
        return default_edges
    return list(graph.get("edges", []))


def _graph_ir_edge_kinds(ir: dict[str, Any]) -> list[str]:
    kinds = {
        _clean_text(edge.get("edge_kind") or edge.get("kind"))
        for edge in _default_display_edges(ir)
        if _clean_text(edge.get("edge_kind") or edge.get("kind"))
    }
    return sorted(kinds)


def _graph_ir_run_roles(ir: dict[str, Any]) -> list[str]:
    roles = {
        _clean_text(run.get("run_role"))
        for run in ir.get("graph", {}).get("runs", [])
        if _clean_text(run.get("run_role"))
    }
    return sorted(roles) or ["base"]


def _graph_ir_diff_statuses(ir: dict[str, Any]) -> list[str]:
    statuses = {
        _clean_text((node.get("diff") or {}).get("status") or "baseline")
        for node in ir.get("graph", {}).get("nodes", [])
    }
    preferred = ["changed", "new", "missing", "unchanged", "baseline"]
    ordered = [status for status in preferred if status in statuses]
    ordered.extend(sorted(status for status in statuses if status not in preferred))
    return ordered or ["baseline"]


def _checkbox_group_html(group_name: str, values: list[str]) -> str:
    items = [f'<div class="filter-group" data-filter-group="{escape(group_name)}">']
    for value in values:
        label = escape(value)
        items.append(
            '  <label class="filter-option">'
            f'<input type="checkbox" value="{label}" checked /> '
            f'{label}'
            '</label>'
        )
    items.append("</div>")
    return "\n".join(items)


def _node_line(node: dict[str, Any]) -> str:
    node_id = str(node.get("id") or "node")
    role = str(node.get("run_role") or "base")
    role_prefix = "fork" if role == "fork" else "base"
    label = f"{role_prefix} | {node_id}\\n{record_label(node)}"
    class_name = _node_class(node)
    role_class = "fork" if role == "fork" else "base"
    return f'        {_safe_id(node_id)}["{_escape_label(label)}"]:::{class_name}:::{role_class}'


def _edge_line(edge: dict[str, Any], source: str, target: str) -> str:
    edge_kind = str(edge.get("edge_kind") or edge.get("kind") or "")
    connector = "-.->" if edge_kind == "control" else "-->"
    label = edge_kind if edge_kind == "fork" else ""
    source_ref = _safe_id(source)
    target_ref = _safe_id(target)
    if label:
        return f'    {source_ref} {connector}|{_escape_label(label)}| {target_ref}'
    return f"    {source_ref} {connector} {target_ref}"


def _node_class(node: dict[str, Any]) -> str:
    kind = node.get("kind")
    if kind == "llm":
        return "llm"
    if kind == "tool":
        return "tool"
    return "node"


def _group_nodes(
    nodes: list[dict[str, Any]],
    group_by: str,
) -> tuple[dict[tuple[str, ...], list[dict[str, Any]]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for node in nodes:
        group_key = _group_key(node, group_by)
        if group_key is None:
            ungrouped.append(node)
            continue
        grouped.setdefault(group_key, []).append(node)
    return grouped, ungrouped


def _group_key(node: dict[str, Any], group_by: str) -> tuple[str, ...] | None:
    run_id = str(node.get("run_id") or "")
    if group_by == "run":
        return (run_id,) if run_id else None
    if group_by == "path":
        branch_id = str(node.get("branch_id") or "")
        return (run_id, branch_id) if run_id and branch_id else None
    if group_by == "span":
        spans = node.get("spans") or []
        if not spans:
            return None
        span_name = str((spans[0] or {}).get("name") or "")
        return (run_id, span_name) if run_id and span_name else None
    raise ValueError(f"Unsupported group_by: {group_by}")


def _subgraph_id_for_group(group_by: str, group_key: tuple[str, ...]) -> str:
    return f"group_{group_by}_" + "_".join(_safe_id(part) for part in group_key)


def _subgraph_label_for_group(
    group_by: str,
    group_key: tuple[str, ...],
    runs: dict[str, dict[str, Any]],
) -> str:
    run_id = group_key[0]
    role = str(runs.get(run_id, {}).get("run_role") or "base")
    role_prefix = "fork" if role == "fork" else "base"
    if group_by == "run":
        return f"{role_prefix} run: {run_id}"
    if group_by == "path":
        return f"{role_prefix} path: {run_id} / {group_key[1]}"
    if group_by == "span":
        return f"{role_prefix} span: {run_id} / {group_key[1]}"
    raise ValueError(f"Unsupported group_by: {group_by}")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _escape_label(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', "&quot;")


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


_CSS = r"""
:root {
  color-scheme: dark;
  --bg: #0b1020;
  --panel: #121a2b;
  --panel-2: #172033;
  --line: #263247;
  --text: #e5edf8;
  --muted: #9aa8bc;
  --accent: #4f9df7;
  --good: #33c481;
  --warn: #f5b84b;
  --fork: #d946ef;
  --danger: #fb7185;
  --llm: #1f5fbf;
  --tool: #8a4b16;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
button, input, select {
  font: inherit;
}
button {
  border: 1px solid #33435f;
  background: #1a263a;
  color: var(--text);
  border-radius: 6px;
  padding: 7px 10px;
  cursor: pointer;
}
button:hover { border-color: var(--accent); }
button:disabled {
  color: #6f7d91;
  cursor: not-allowed;
}
input, select {
  width: 100%;
  min-width: 0;
  border: 1px solid #33435f;
  background: #0d1524;
  color: var(--text);
  border-radius: 6px;
  padding: 8px 9px;
}
#app {
  min-height: 100vh;
  display: grid;
  grid-template-rows: auto 1fr;
}
#top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 56px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: #0e1626;
}
.app-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}
.top-stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
}
#shell {
  min-height: 0;
  display: grid;
  grid-template-columns: 280px minmax(420px, 1fr) 360px;
}
#left-rail, #inspector {
  min-height: 0;
  overflow: auto;
  background: var(--panel);
}
#left-rail { border-right: 1px solid var(--line); }
#inspector { border-left: 1px solid var(--line); }
.panel {
  padding: 14px;
  border-bottom: 1px solid var(--line);
}
.panel h2, .tab-panel h2 {
  margin: 0 0 10px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0;
  color: #c9d7eb;
}
.filter-block {
  margin-top: 14px;
}
.filter-block h3, .control-field {
  display: block;
  margin: 10px 0 7px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
}
.control-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  margin-bottom: 8px;
}
.control-row select + input {
  min-width: 78px;
}
.filter-group {
  display: grid;
  gap: 6px;
}
.filter-option {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #d7e1f0;
  font-size: 13px;
}
.filter-option input {
  width: auto;
}
#canvas-area {
  min-width: 0;
  min-height: 0;
  background: #09101d;
  display: grid;
  grid-template-rows: minmax(360px, 1fr) 220px;
}
#graph-container {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 360px;
  overflow: hidden;
}
#graph-svg {
  width: 100%;
  height: 100%;
  display: block;
  background:
    linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
  background-size: 28px 28px;
}
#minimap {
  position: absolute;
  right: 12px;
  bottom: 12px;
  width: 180px;
  height: 84px;
  border: 1px solid #33435f;
  background: rgba(13, 21, 36, 0.86);
  border-radius: 6px;
  padding: 8px;
  color: var(--muted);
  font-size: 12px;
  pointer-events: none;
}
#minimap svg {
  width: 100%;
  height: 100%;
  display: block;
}
#canvas-controls {
  position: absolute;
  left: 12px;
  bottom: 12px;
  color: var(--muted);
  font-size: 12px;
  background: rgba(13, 21, 36, 0.82);
  border: 1px solid #33435f;
  border-radius: 6px;
  padding: 7px 9px;
}
#timeline-panel {
  min-height: 0;
  border-top: 1px solid var(--line);
  background: #0d1524;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto 1fr;
}
.timeline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  border-bottom: 1px solid #263247;
}
.timeline-header h2 {
  margin: 0;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0;
}
#timeline-stats {
  color: var(--muted);
  font-size: 12px;
}
#timeline-list {
  overflow: auto;
  padding: 8px 10px 10px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
}
.timeline-item {
  border: 1px solid #2f3d55;
  border-left-width: 4px;
  background: #121a2b;
  border-radius: 8px;
  padding: 8px 9px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.timeline-item:hover, .timeline-item.is-active {
  border-color: var(--accent);
}
.timeline-item.changed { border-left-color: #f5b84b; }
.timeline-item.new { border-left-color: #33c481; }
.timeline-item.missing { border-left-color: #fb7185; }
.timeline-item.unchanged { border-left-color: #64748b; }
.timeline-item-title {
  font-size: 13px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.timeline-item-meta {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.search-results, .group-list {
  display: grid;
  gap: 6px;
  margin-top: 8px;
}
.search-result, .group-toggle {
  display: block;
  width: 100%;
  text-align: left;
  padding: 7px 8px;
  border: 1px solid #2f3d55;
  background: #10192b;
  border-radius: 6px;
  color: #d7e1f0;
  font-size: 12px;
}
.search-result:hover, .group-toggle:hover {
  border-color: var(--accent);
}
.group-toggle.is-collapsed {
  border-color: #6f5522;
  color: #ffe2a7;
}
.tabs {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border-bottom: 1px solid var(--line);
}
.tab {
  border: 0;
  border-right: 1px solid var(--line);
  border-radius: 0;
  background: #111a2b;
  padding: 10px 4px;
  color: var(--muted);
}
.tab.is-active {
  background: #1b2940;
  color: var(--text);
}
.tab-panel {
  display: none;
  padding: 14px;
}
.tab-panel.is-active {
  display: block;
}
.inspector-card, .run-card {
  border: 1px solid #2f3d55;
  background: var(--panel-2);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 10px;
}
.inspector-card h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.muted {
  color: var(--muted);
}
.badge-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 7px 0;
}
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  border: 1px solid #344762;
  border-radius: 999px;
  padding: 2px 7px;
  color: #cfddf1;
  background: #10192b;
  font-size: 12px;
}
.badge.good { border-color: #23684e; color: #baf5d6; }
.badge.warn { border-color: #6f5522; color: #ffe2a7; }
.badge.fork { border-color: #7a277f; color: #f5b7ff; }
.badge.changed { border-color: #80601f; color: #ffe2a7; }
.badge.new { border-color: #23684e; color: #baf5d6; }
.badge.missing { border-color: #7f2937; color: #fecdd3; }
.badge.unchanged { border-color: #40516b; color: #cbd5e1; }
.meta-grid {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 7px 10px;
  margin: 10px 0;
  font-size: 12px;
}
.meta-grid div:nth-child(odd) {
  color: var(--muted);
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 8px 0 0;
  max-height: 360px;
  overflow: auto;
  border: 1px solid #2d3a52;
  border-radius: 6px;
  background: #0b1322;
  padding: 10px;
  color: #dce8f8;
  font-size: 12px;
  line-height: 1.45;
}
.placeholder-text {
  color: var(--muted);
}
.svg-edge {
  fill: none;
  stroke: #64748b;
  stroke-width: 2;
  marker-end: url(#arrow);
}
.svg-edge.control {
  stroke-dasharray: 6 5;
}
.svg-edge.fork {
  stroke: var(--fork);
  stroke-width: 3;
  marker-end: url(#arrow-fork);
}
.svg-edge.fork-boundary {
  stroke: #f5b84b;
  stroke-width: 4;
  marker-end: url(#arrow-selected);
}
.svg-edge.cross-run {
  stroke-dasharray: 3 5;
}
.svg-edge.is-selected {
  stroke: var(--good);
  stroke-width: 4;
  marker-end: url(#arrow-selected);
}
.svg-node rect {
  fill: #243247;
  stroke: #90a4bf;
  stroke-width: 1.2;
  rx: 8;
}
.svg-node.llm rect {
  fill: var(--llm);
  stroke: #9ac7ff;
}
.svg-node.tool rect {
  fill: var(--tool);
  stroke: #ffc879;
}
.svg-node.fork rect {
  stroke: var(--fork);
  stroke-width: 3;
}
.svg-node.group rect {
  fill: #1a263a;
  stroke: #f5b84b;
  stroke-width: 2;
  stroke-dasharray: 7 4;
}
.svg-node.changed rect {
  stroke: #f5b84b;
  stroke-width: 3;
}
.svg-node.new rect {
  stroke: #33c481;
  stroke-width: 3;
}
.svg-node.missing rect {
  stroke: #fb7185;
  stroke-width: 3;
}
.svg-node.unchanged rect {
  opacity: 0.82;
}
.svg-node.boundary rect {
  stroke: #f5b84b;
  stroke-width: 4;
}
.svg-node.search-match rect {
  stroke: #facc15;
  stroke-width: 3;
}
.svg-node.is-selected rect {
  stroke: var(--good);
  stroke-width: 4;
}
.svg-node text {
  fill: #f8fbff;
  font-size: 12px;
  pointer-events: none;
}
.svg-edge-hit {
  fill: none;
  stroke: transparent;
  stroke-width: 14;
  cursor: pointer;
}
.svg-node {
  cursor: pointer;
}
.action-card button {
  margin-top: 8px;
}
.copy-status {
  min-height: 18px;
  margin-top: 5px;
  color: var(--good);
  font-size: 12px;
}
.diff-grid {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 7px 8px;
  font-size: 12px;
}
.diff-grid strong {
  color: #c9d7eb;
}
@media (max-width: 1100px) {
  #shell {
    grid-template-columns: 240px minmax(360px, 1fr);
  }
  #inspector {
    grid-column: 1 / -1;
    border-left: 0;
    border-top: 1px solid var(--line);
    max-height: 44vh;
  }
}
"""


_JS = r"""
(function () {
  var NS = ['http:', '', 'www.w3.org', '2000', 'svg'].join('/');
  var LAYOUT_CACHE = {};

  function byId(id) {
    return document.getElementById(id);
  }

  function readGraphIR() {
    var el = byId('graph-ir-data');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (error) {
      console.error('Failed to parse graph IR JSON', error);
      return {};
    }
  }

  function escapeHTML(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function pretty(value) {
    return JSON.stringify(value == null ? null : value, null, 2);
  }

  function setHTML(id, html) {
    var el = byId(id);
    if (el) el.innerHTML = html;
  }

  function textSearch(value) {
    return String(value == null ? '' : value).toLowerCase();
  }

  function collectCheckedValues(groupName) {
    return Array.prototype.slice
      .call(document.querySelectorAll('[data-filter-group="' + groupName + '"] input[type="checkbox"]'))
      .filter(function (input) { return input.checked; })
      .map(function (input) { return input.value; });
  }

  function activateTab(tabName) {
    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.classList.toggle('is-active', tab.getAttribute('data-tab') === tabName);
    });
    document.querySelectorAll('.tab-panel').forEach(function (panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-panel') === tabName);
    });
  }

  function wireTabs() {
    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        activateTab(tab.getAttribute('data-tab'));
      });
    });
  }

  function normalizeDepth(value) {
    if (value == null || value === '') return null;
    var parsed = Number(value);
    if (!isFinite(parsed) || parsed < 1) return null;
    return Math.floor(parsed);
  }

  function createSvg(tag) {
    return document.createElementNS(NS, tag);
  }

  function wrapText(text, limit, maxLines) {
    var words = String(text || '').split(/\s+/).filter(Boolean);
    var lines = [];
    var current = '';
    words.forEach(function (word) {
      var next = current ? current + ' ' + word : word;
      if (next.length > limit && current) {
        lines.push(current);
        current = word;
      } else {
        current = next;
      }
    });
    if (current) lines.push(current);
    if (!lines.length) lines.push('');
    if (lines.length > maxLines) {
      lines = lines.slice(0, maxLines);
      lines[maxLines - 1] = lines[maxLines - 1].slice(0, Math.max(0, limit - 3)) + '...';
    }
    return lines;
  }

  function nodeSearchText(node) {
    return [
      node.id,
      node.title,
      node.summary,
      node.run_id,
      node.kind,
      node.branch_id,
      node.status,
      diffStatus(node),
      node.preview && node.preview.input,
      node.preview && node.preview.output
    ].filter(Boolean).join(' ').toLowerCase();
  }

  function edgeSearchText(edge) {
    return [edge.id, edge.edge_kind, edge.summary, edge.source, edge.target]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
  }

  function titleForNode(node) {
    return [node.title || node.kind || 'node', node.id].filter(Boolean).join(' | ');
  }

  function diffStatus(node) {
    return (node && node.diff && node.diff.status) || 'baseline';
  }

  function diffBadgeClass(status) {
    if (status === 'changed' || status === 'new' || status === 'missing' || status === 'unchanged') return status;
    return '';
  }

  function firstSpanName(node) {
    var spans = (node && node.spans) || [];
    if (!spans.length) return '';
    return spans[0] && spans[0].name ? String(spans[0].name) : '';
  }

  function groupKeyForNode(node, groupBy) {
    if (!node || groupBy === 'none') return '';
    if (groupBy === 'run') return node.run_id ? 'run:' + node.run_id : '';
    if (groupBy === 'path') return node.run_id && node.branch_id ? 'path:' + node.run_id + ':' + node.branch_id : '';
    if (groupBy === 'span') {
      var span = firstSpanName(node);
      return node.run_id && span ? 'span:' + node.run_id + ':' + span : '';
    }
    return '';
  }

  function groupLabelForNode(node, groupBy) {
    if (!node || groupBy === 'none') return '';
    if (groupBy === 'run') return 'run ' + (node.run_id || '');
    if (groupBy === 'path') return 'path ' + (node.run_id || '') + ' / ' + (node.branch_id || '');
    if (groupBy === 'span') return 'span ' + (node.run_id || '') + ' / ' + firstSpanName(node);
    return '';
  }

  function aggregateDiffStatus(nodes) {
    var priority = { missing: 5, new: 4, changed: 3, unchanged: 2, baseline: 1 };
    var best = 'baseline';
    (nodes || []).forEach(function (node) {
      var status = diffStatus(node);
      if ((priority[status] || 0) > (priority[best] || 0)) best = status;
    });
    return best;
  }

  function layoutCacheKey(nodes, edges) {
    return nodes.map(function (node) { return node.id; }).sort().join('|') + '::' +
      edges.map(function (edge) { return edge.id || (edge.source + '>' + edge.target + ':' + (edge.edge_kind || '')); }).sort().join('|');
  }

  function makeSet(values) {
    return (values || []).reduce(function (acc, value) {
      if (value != null) acc[value] = true;
      return acc;
    }, {});
  }

  function createExplorer(ir) {
    var graph = ir.graph || {};
    var nodes = Array.isArray(graph.nodes) ? graph.nodes.slice() : [];
    var fullEdges = Array.isArray(graph.edges) ? graph.edges.slice() : [];
    var edgeLayers = graph.edge_layers || {};
    var edges = Array.isArray(edgeLayers.default) ? edgeLayers.default.slice() : fullEdges.slice();
    var runs = Array.isArray(graph.runs) ? graph.runs.slice() : [];
    var timelineItems = graph.timeline && Array.isArray(graph.timeline.items) ? graph.timeline.items.slice() : [];
    var diff = graph.diff || {};
    var comparisons = Array.isArray(diff.comparisons) ? diff.comparisons.slice() : [];
    var evidenceItems = (ir.evidence && Array.isArray(ir.evidence.items)) ? ir.evidence.items.slice() : [];
    var nodeById = {};
    var edgeById = {};
    var evidenceById = {};
    var timelineByNodeId = {};
    var boundaryNodeIds = {};
    var boundaryEdgeIds = {};
    var downstreamNodeIds = {};
    var outgoing = {};
    var incoming = {};
    nodes.forEach(function (node) {
      nodeById[node.id] = node;
      outgoing[node.id] = [];
      incoming[node.id] = [];
    });
    edges.forEach(function (edge) {
      edgeById[edge.id] = edge;
      if (outgoing[edge.source]) outgoing[edge.source].push(edge);
      if (incoming[edge.target]) incoming[edge.target].push(edge);
    });
    evidenceItems.forEach(function (item) {
      if (item && item.id) evidenceById[item.id] = item;
    });
    timelineItems.forEach(function (item) {
      if (item && item.node_id) timelineByNodeId[item.node_id] = item;
    });
    comparisons.forEach(function (comparison) {
      var breakpoint = comparison.breakpoint || {};
      if (breakpoint.base_node_id) boundaryNodeIds[breakpoint.base_node_id] = true;
      if (breakpoint.fork_node_id) boundaryNodeIds[breakpoint.fork_node_id] = true;
      (comparison.boundary_edge_ids || breakpoint.edge_ids || []).forEach(function (id) {
        boundaryEdgeIds[id] = true;
      });
      (comparison.downstream_node_ids || []).forEach(function (id) {
        downstreamNodeIds[id] = true;
      });
    });

    var explorer = {
      ir: ir,
      nodes: nodes,
      edges: edges,
      fullEdges: fullEdges,
      edgeLayers: edgeLayers,
      runs: runs,
      timelineItems: timelineItems,
      timelineByNodeId: timelineByNodeId,
      diff: diff,
      comparisons: comparisons,
      boundaryNodeIds: boundaryNodeIds,
      boundaryEdgeIds: boundaryEdgeIds,
      downstreamNodeIds: downstreamNodeIds,
      nodeById: nodeById,
      edgeById: edgeById,
      evidenceById: evidenceById,
      outgoing: outgoing,
      incoming: incoming,
      view: { nodes: nodes, edges: edges, renderNodes: nodes, renderEdges: edges },
      selection: null,
      zoom: 1,
      pan: { x: 0, y: 0 },
      state: {
        search: '',
        focus: { query: '', direction: 'both', maxDepth: null },
        forkDownstreamOnly: false,
        groupBy: 'none',
        collapsedGroups: {},
        filters: {
          nodeKinds: collectCheckedValues('node-kinds'),
          edgeKinds: collectCheckedValues('edge-kinds'),
          runRoles: collectCheckedValues('run-roles'),
          diffStatuses: collectCheckedValues('diff-statuses')
        }
      }
    };

    explorer.searchNodeIds = function (query) {
      if (!query) return nodes.map(function (node) { return node.id; });
      return nodes.filter(function (node) {
        return nodeSearchText(node).indexOf(query) !== -1;
      }).map(function (node) { return node.id; });
    };

    explorer.focusNodeIds = function () {
      var focus = this.state.focus || {};
      var query = textSearch(focus.query).trim();
      if (!query) return null;
      var seeds = this.searchNodeIds(query);
      if (!seeds.length && nodeById[focus.query]) seeds = [focus.query];
      if (!seeds.length) return [];
      var selected = {};
      var visited = {};
      var queue = [];
      var maxDepth = normalizeDepth(focus.maxDepth);
      seeds.forEach(function (seed) {
        selected[seed] = true;
        visited[seed] = true;
        queue.push({ id: seed, depth: 0 });
      });
      while (queue.length) {
        var current = queue.shift();
        if (maxDepth != null && current.depth >= maxDepth) continue;
        var neighborIds = [];
        if (focus.direction === 'both' || focus.direction === 'downstream') {
          neighborIds = neighborIds.concat((outgoing[current.id] || []).map(function (edge) { return edge.target; }));
        }
        if (focus.direction === 'both' || focus.direction === 'upstream') {
          neighborIds = neighborIds.concat((incoming[current.id] || []).map(function (edge) { return edge.source; }));
        }
        neighborIds.forEach(function (id) {
          selected[id] = true;
          if (!visited[id]) {
            visited[id] = true;
            queue.push({ id: id, depth: current.depth + 1 });
          }
        });
      }
      return Object.keys(selected);
    };

    explorer.deriveVisibleGraph = function () {
      var searchQuery = textSearch(this.state.search).trim();
      var focusedIds = this.focusNodeIds();
      var focusedSet = focusedIds == null ? null : focusedIds.reduce(function (acc, id) {
        acc[id] = true;
        return acc;
      }, {});
      var matchedSet = this.searchNodeIds(searchQuery).reduce(function (acc, id) {
        acc[id] = true;
        return acc;
      }, {});
      var nodeKinds = this.state.filters.nodeKinds;
      var edgeKinds = this.state.filters.edgeKinds;
      var runRoles = this.state.filters.runRoles;
      var diffStatuses = this.state.filters.diffStatuses;

      var visibleNodes = nodes.filter(function (node) {
        if (nodeKinds.length && nodeKinds.indexOf(node.kind) === -1) return false;
        if (runRoles.length && runRoles.indexOf(node.run_role || 'base') === -1) return false;
        if (diffStatuses.length && diffStatuses.indexOf(diffStatus(node)) === -1) return false;
        if (explorer.state.forkDownstreamOnly && !downstreamNodeIds[node.id]) return false;
        if (focusedSet && !focusedSet[node.id]) return false;
        if (searchQuery && !focusedSet && !matchedSet[node.id]) return false;
        return true;
      });
      var visibleSet = visibleNodes.reduce(function (acc, node) {
        acc[node.id] = true;
        return acc;
      }, {});
      var visibleEdges = edges.filter(function (edge) {
        if (!visibleSet[edge.source] || !visibleSet[edge.target]) return false;
        if (edgeKinds.length && edgeKinds.indexOf(edge.edge_kind || edge.kind) === -1) return false;
        if (runRoles.length && runRoles.indexOf(edge.run_role || 'base') === -1) return false;
        if (searchQuery && !edgeSearchText(edge).includes(searchQuery) && !matchedSet[edge.source] && !matchedSet[edge.target]) return false;
        return true;
      });
      var grouped = applyCollapsedGroups(this, visibleNodes, visibleEdges);
      return {
        nodes: visibleNodes,
        edges: visibleEdges,
        renderNodes: grouped.nodes,
        renderEdges: grouped.edges,
        matchedNodeIds: matchedSet
      };
    };

    explorer.refresh = function () {
      this.view = this.deriveVisibleGraph();
      renderGraph(this);
      renderRunSummary(this);
      renderTimeline(this);
      renderSearchResults(this);
      renderGroupList(this);
      if (this.selection) {
        var current = this.selection.type === 'node' ? nodeById[this.selection.id] : edgeById[this.selection.id];
        if (current) {
          this.renderSelection(this.selection.type, current);
        } else {
          this.renderSelection(null, null);
        }
      }
    };

    explorer.renderSelection = function (type, value) {
      if (!type || !value) {
        this.selection = null;
        renderInspectorPlaceholder();
      } else {
        this.selection = { type: type, id: value.id };
        if (type === 'node') renderNodeInspector(this, value);
        if (type === 'edge') renderEdgeInspector(this, value);
      }
      renderGraph(this);
      renderTimeline(this);
    };

    explorer.applySearch = function (value) {
      this.state.search = typeof value === 'string' ? value : '';
      this.refresh();
    };

    explorer.applyFocus = function (options) {
      options = options || {};
      this.state.focus = {
        query: typeof options.query === 'string' ? options.query : this.state.search,
        direction: options.direction || 'both',
        maxDepth: normalizeDepth(options.maxDepth)
      };
      this.refresh();
    };

    explorer.clearFocus = function () {
      this.state.focus = { query: '', direction: 'both', maxDepth: null };
      this.refresh();
    };

    explorer.applyFilters = function () {
      this.state.filters = {
        nodeKinds: collectCheckedValues('node-kinds'),
        edgeKinds: collectCheckedValues('edge-kinds'),
        runRoles: collectCheckedValues('run-roles'),
        diffStatuses: collectCheckedValues('diff-statuses')
      };
      this.refresh();
    };

    explorer.resetFilters = function () {
      document.querySelectorAll('[data-filter-group] input[type="checkbox"]').forEach(function (input) {
        input.checked = true;
      });
      this.applyFilters();
    };

    explorer.fitView = function () {
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      renderGraph(this);
    };

    explorer.applyGrouping = function (groupBy) {
      this.state.groupBy = groupBy || 'none';
      this.state.collapsedGroups = {};
      this.refresh();
    };

    explorer.collapseAllGroups = function () {
      var groupBy = this.state.groupBy || 'none';
      if (groupBy === 'none') return;
      var collapsed = {};
      this.nodes.forEach(function (node) {
        var key = groupKeyForNode(node, groupBy);
        if (key) collapsed[key] = true;
      });
      this.state.collapsedGroups = collapsed;
      this.refresh();
    };

    explorer.expandAllGroups = function () {
      this.state.collapsedGroups = {};
      this.refresh();
    };

    explorer.toggleGroup = function (groupKey) {
      this.state.collapsedGroups[groupKey] = !this.state.collapsedGroups[groupKey];
      this.refresh();
    };

    explorer.showForkDownstream = function () {
      this.state.forkDownstreamOnly = true;
      this.refresh();
    };

    explorer.clearForkDownstream = function () {
      this.state.forkDownstreamOnly = false;
      this.refresh();
    };

    explorer.resetView = explorer.fitView;
    return explorer;
  }

  function applyCollapsedGroups(explorer, visibleNodes, visibleEdges) {
    var groupBy = explorer.state.groupBy || 'none';
    var collapsed = explorer.state.collapsedGroups || {};
    if (groupBy === 'none' || !Object.keys(collapsed).some(function (key) { return collapsed[key]; })) {
      return { nodes: visibleNodes, edges: visibleEdges };
    }

    var groupMembers = {};
    var groupLabels = {};
    visibleNodes.forEach(function (node) {
      var key = groupKeyForNode(node, groupBy);
      if (key && collapsed[key]) {
        if (!groupMembers[key]) groupMembers[key] = [];
        groupMembers[key].push(node);
        groupLabels[key] = groupLabelForNode(node, groupBy);
      }
    });
    var replacementByNode = {};
    Object.keys(groupMembers).forEach(function (key) {
      groupMembers[key].forEach(function (node) {
        replacementByNode[node.id] = key;
      });
    });

    var renderNodes = visibleNodes.filter(function (node) {
      return !replacementByNode[node.id];
    });
    Object.keys(groupMembers).sort().forEach(function (key) {
      var members = groupMembers[key];
      renderNodes.push({
        id: key,
        kind: 'group',
        run_role: members.some(function (node) { return node.run_role === 'fork'; }) ? 'fork' : 'base',
        title: groupLabels[key] || key,
        summary: members.length + ' collapsed nodes',
        status: aggregateDiffStatus(members),
        diff: { status: aggregateDiffStatus(members), comparisons: [] },
        degree: { incoming: 0, outgoing: 0 },
        collapsed_group: {
          key: key,
          group_by: groupBy,
          label: groupLabels[key] || key,
          member_node_ids: members.map(function (node) { return node.id; }),
          count: members.length
        }
      });
    });

    var seenEdges = {};
    var renderEdges = [];
    visibleEdges.forEach(function (edge) {
      var source = replacementByNode[edge.source] || edge.source;
      var target = replacementByNode[edge.target] || edge.target;
      if (source === target) return;
      var edgeKey = source + '>' + target + ':' + (edge.edge_kind || edge.kind || 'edge');
      if (seenEdges[edgeKey]) return;
      seenEdges[edgeKey] = true;
      renderEdges.push({
        id: 'collapsed:' + edgeKey,
        source: source,
        target: target,
        edge_kind: edge.edge_kind || edge.kind || 'edge',
        kind: edge.kind || edge.edge_kind,
        cross_run: edge.cross_run,
        run_role: edge.run_role,
        summary: edge.summary,
        evidence_refs: edge.evidence_refs || [],
        collapsed_from: [edge.id]
      });
    });
    return { nodes: renderNodes, edges: renderEdges };
  }

  function computeLayout(nodes, edges) {
    var cacheKey = layoutCacheKey(nodes, edges);
    if (LAYOUT_CACHE[cacheKey]) {
      return LAYOUT_CACHE[cacheKey];
    }
    var nodeIds = nodes.map(function (node) { return node.id; });
    var visible = nodeIds.reduce(function (acc, id) { acc[id] = true; return acc; }, {});
    var depth = {};
    nodeIds.forEach(function (id) { depth[id] = 0; });
    for (var i = 0; i < nodes.length; i += 1) {
      var changed = false;
      edges.forEach(function (edge) {
        if (!visible[edge.source] || !visible[edge.target]) return;
        var next = (depth[edge.source] || 0) + 1;
        if (next > (depth[edge.target] || 0)) {
          depth[edge.target] = next;
          changed = true;
        }
      });
      if (!changed) break;
    }
    var columns = {};
    nodes.forEach(function (node) {
      var d = depth[node.id] || 0;
      if (!columns[d]) columns[d] = [];
      columns[d].push(node);
    });
    var positions = {};
    var xGap = 250;
    var yGap = 116;
    var nodeWidth = 190;
    var nodeHeight = 74;
    Object.keys(columns).map(Number).sort(function (a, b) { return a - b; }).forEach(function (d) {
      columns[d].sort(function (a, b) {
        var aOrder = (a.order && a.order.index) || 0;
        var bOrder = (b.order && b.order.index) || 0;
        if (aOrder !== bOrder) return aOrder - bOrder;
        return String(a.path_id || a.id).localeCompare(String(b.path_id || b.id));
      }).forEach(function (node, row) {
        positions[node.id] = {
          x: 60 + d * xGap,
          y: 60 + row * yGap,
          width: nodeWidth,
          height: nodeHeight
        };
      });
    });
    var maxX = 0;
    var maxY = 0;
    Object.keys(positions).forEach(function (id) {
      maxX = Math.max(maxX, positions[id].x + nodeWidth + 60);
      maxY = Math.max(maxY, positions[id].y + nodeHeight + 60);
    });
    var layout = { positions: positions, width: Math.max(800, maxX), height: Math.max(520, maxY) };
    LAYOUT_CACHE[cacheKey] = layout;
    return layout;
  }

  function renderGraph(explorer) {
    var svg = byId('graph-svg');
    if (!svg) return;
    var view = explorer.view || { nodes: [], edges: [] };
    var renderNodes = view.renderNodes || view.nodes || [];
    var renderEdges = view.renderEdges || view.edges || [];
    var layout = computeLayout(renderNodes, renderEdges);
    svg.innerHTML = '';
    svg.setAttribute('viewBox', '0 0 ' + layout.width + ' ' + layout.height);

    var defs = createSvg('defs');
    defs.innerHTML = [
      '<marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#64748b"></path></marker>',
      '<marker id="arrow-fork" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#d946ef"></path></marker>',
      '<marker id="arrow-selected" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#33c481"></path></marker>'
    ].join('');
    svg.appendChild(defs);

    var edgeLayer = createSvg('g');
    var nodeLayer = createSvg('g');
    svg.appendChild(edgeLayer);
    svg.appendChild(nodeLayer);

    renderEdges.forEach(function (edge) {
      var source = layout.positions[edge.source];
      var target = layout.positions[edge.target];
      if (!source || !target) return;
      var sx = source.x + source.width;
      var sy = source.y + source.height / 2;
      var tx = target.x;
      var ty = target.y + target.height / 2;
      var mid = Math.max(40, (tx - sx) / 2);
      var d = 'M' + sx + ',' + sy + ' C' + (sx + mid) + ',' + sy + ' ' + (tx - mid) + ',' + ty + ' ' + tx + ',' + ty;
      var path = createSvg('path');
      path.setAttribute('d', d);
      path.setAttribute('class', [
        'svg-edge',
        edge.edge_kind || edge.kind || '',
        edge.cross_run ? 'cross-run' : '',
        explorer.boundaryEdgeIds[edge.id] ? 'fork-boundary' : '',
        explorer.selection && explorer.selection.type === 'edge' && explorer.selection.id === edge.id ? 'is-selected' : ''
      ].filter(Boolean).join(' '));
      edgeLayer.appendChild(path);

      var hit = createSvg('path');
      hit.setAttribute('d', d);
      hit.setAttribute('class', 'svg-edge-hit');
      hit.addEventListener('click', function () {
        explorer.renderSelection('edge', edge);
      });
      edgeLayer.appendChild(hit);

      if (edge.edge_kind === 'fork') {
        var label = createSvg('text');
        label.setAttribute('x', String((sx + tx) / 2));
        label.setAttribute('y', String((sy + ty) / 2 - 8));
        label.setAttribute('fill', '#f5b7ff');
        label.setAttribute('font-size', '12');
        label.textContent = 'fork';
        edgeLayer.appendChild(label);
      }
    });

    renderNodes.forEach(function (node) {
      var box = layout.positions[node.id];
      if (!box) return;
      var group = createSvg('g');
      var match = (view.matchedNodeIds || {})[node.id];
      var status = diffStatus(node);
      group.setAttribute('class', [
        'svg-node',
        node.kind || 'node',
        node.run_role || 'base',
        status,
        node.collapsed_group ? 'group' : '',
        explorer.boundaryNodeIds[node.id] ? 'boundary' : '',
        match ? 'search-match' : '',
        explorer.selection && explorer.selection.type === 'node' && explorer.selection.id === node.id ? 'is-selected' : ''
      ].filter(Boolean).join(' '));
      group.setAttribute('transform', 'translate(' + box.x + ' ' + box.y + ')');
      group.addEventListener('click', function () {
        explorer.renderSelection('node', node);
      });

      var rect = createSvg('rect');
      rect.setAttribute('width', String(box.width));
      rect.setAttribute('height', String(box.height));
      group.appendChild(rect);

      var lines = wrapText(titleForNode(node), 25, 3);
      lines.forEach(function (line, idx) {
        var text = createSvg('text');
        text.setAttribute('x', '12');
        text.setAttribute('y', String(22 + idx * 16));
        text.textContent = line;
        group.appendChild(text);
      });
      var meta = createSvg('text');
      meta.setAttribute('x', '12');
      meta.setAttribute('y', String(box.height - 10));
      meta.setAttribute('fill', '#d5e3f7');
      meta.textContent = (node.kind || 'node') + ' / ' + (node.status || status || 'recorded');
      group.appendChild(meta);
      nodeLayer.appendChild(group);
    });

    renderMinimap(layout, renderNodes);
    setHTML(
      'canvas-controls',
      'Visible ' + escapeHTML(view.nodes.length) + ' nodes / ' + escapeHTML(view.edges.length) +
        ' display edges. Rendered ' + escapeHTML(renderNodes.length) + ' items. Full provenance has ' +
        escapeHTML((explorer.fullEdges || []).length) + ' edges.'
    );
  }

  function renderMinimap(layout, nodes) {
    if (!nodes || !nodes.length) {
      setHTML('minimap', 'No visible graph');
      return;
    }
    var scaleX = 160 / Math.max(1, layout.width);
    var scaleY = 64 / Math.max(1, layout.height);
    var rects = nodes.slice(0, 600).map(function (node) {
      var box = layout.positions[node.id];
      if (!box) return '';
      var status = diffStatus(node);
      var color = status === 'changed' ? '#f5b84b' : status === 'new' ? '#33c481' : status === 'missing' ? '#fb7185' : '#7b8ba3';
      return '<rect x="' + Math.round(box.x * scaleX) + '" y="' + Math.round(box.y * scaleY) + '" width="' +
        Math.max(2, Math.round(box.width * scaleX)) + '" height="' + Math.max(2, Math.round(box.height * scaleY)) +
        '" fill="' + color + '" opacity="0.82"></rect>';
    }).join('');
    setHTML(
      'minimap',
      '<svg viewBox="0 0 160 64" aria-hidden="true">' +
        '<rect x="0" y="0" width="160" height="64" fill="#0b1322" stroke="#33435f"></rect>' +
        rects +
        '</svg>'
    );
  }

  function renderSearchResults(explorer) {
    var query = textSearch(explorer.state.search).trim();
    if (!query) {
      setHTML('search-results', '<p class="placeholder-text">Search results will appear here.</p>');
      return;
    }
    var matches = explorer.nodes.filter(function (node) {
      return nodeSearchText(node).indexOf(query) !== -1;
    });
    var html = matches.slice(0, 24).map(function (node) {
      return '<button type="button" class="search-result" data-node-id="' + escapeHTML(node.id) + '">' +
        '<strong>' + escapeHTML(node.title || node.id) + '</strong><br>' +
        '<span class="muted">' + escapeHTML(node.run_id || '') + ' / ' + escapeHTML(diffStatus(node)) + '</span>' +
        '</button>';
    }).join('');
    if (!html) html = '<p class="placeholder-text">No matching nodes.</p>';
    if (matches.length > 24) html += '<p class="placeholder-text">Showing first 24 of ' + escapeHTML(matches.length) + '.</p>';
    setHTML('search-results', html);
    document.querySelectorAll('#search-results [data-node-id]').forEach(function (button) {
      button.addEventListener('click', function () {
        var id = button.getAttribute('data-node-id');
        if (id && explorer.nodeById[id]) explorer.renderSelection('node', explorer.nodeById[id]);
      });
    });
  }

  function renderGroupList(explorer) {
    var groupBy = explorer.state.groupBy || 'none';
    if (groupBy === 'none') {
      setHTML('group-list', '<p class="placeholder-text">Choose a grouping to collapse large traces.</p>');
      return;
    }
    var groups = {};
    (explorer.view.nodes || []).forEach(function (node) {
      var key = groupKeyForNode(node, groupBy);
      if (!key) return;
      if (!groups[key]) groups[key] = { label: groupLabelForNode(node, groupBy), count: 0 };
      groups[key].count += 1;
    });
    var html = Object.keys(groups).sort().slice(0, 32).map(function (key) {
      var collapsed = explorer.state.collapsedGroups[key];
      return '<button type="button" class="group-toggle ' + (collapsed ? 'is-collapsed' : '') + '" data-group-key="' +
        escapeHTML(key) + '">' +
        escapeHTML(collapsed ? 'Expand ' : 'Collapse ') + escapeHTML(groups[key].label) +
        ' (' + escapeHTML(groups[key].count) + ')' +
        '</button>';
    }).join('');
    setHTML('group-list', html || '<p class="placeholder-text">No groups in this view.</p>');
    document.querySelectorAll('#group-list [data-group-key]').forEach(function (button) {
      button.addEventListener('click', function () {
        explorer.toggleGroup(button.getAttribute('data-group-key'));
      });
    });
  }

  function renderTimeline(explorer) {
    var visibleSet = makeSet((explorer.view.nodes || []).map(function (node) { return node.id; }));
    var items = explorer.timelineItems.filter(function (item) {
      return visibleSet[item.node_id];
    });
    var selectedId = explorer.selection && explorer.selection.type === 'node' ? explorer.selection.id : '';
    var html = items.map(function (item) {
      var node = explorer.nodeById[item.node_id] || {};
      var status = diffStatus(node);
      return '<button type="button" class="timeline-item ' + escapeHTML(status) +
        (item.node_id === selectedId ? ' is-active' : '') + '" data-node-id="' + escapeHTML(item.node_id) + '">' +
        '<div class="timeline-item-title">' + escapeHTML(((item.order && item.order.run_index) || '?') + '. ' + (item.title || item.node_id)) + '</div>' +
        '<div class="timeline-item-meta">' + escapeHTML((item.kind || 'node') + ' / ' + (item.run_id || '') + ' / ' + status) + '</div>' +
        '<div class="timeline-item-meta">' + escapeHTML(item.created_at || item.path_id || '') + '</div>' +
        '</button>';
    }).join('');
    setHTML('timeline-list', html || '<p class="placeholder-text">No timeline items in this view.</p>');
    setHTML('timeline-stats', escapeHTML(items.length) + ' visible calls');
    document.querySelectorAll('#timeline-list [data-node-id]').forEach(function (button) {
      button.addEventListener('click', function () {
        var id = button.getAttribute('data-node-id');
        if (id && explorer.nodeById[id]) explorer.renderSelection('node', explorer.nodeById[id]);
      });
    });
  }

  function renderRunSummary(explorer) {
    var view = explorer.view || { nodes: [], edges: [] };
    var stats = (explorer.ir.graph && explorer.ir.graph.stats) || {};
    setHTML(
      'top-stats',
      '<span>Nodes ' + escapeHTML(view.nodes.length) + '/' + escapeHTML(explorer.nodes.length) + '</span>' +
        '<span>Edges ' + escapeHTML(view.edges.length) + '/' + escapeHTML(explorer.edges.length) + '</span>' +
        '<span>Evidence ' + escapeHTML(stats.evidence_count || 0) + '</span>'
    );
    setHTML(
      'run-summary',
      explorer.runs.map(function (run) {
        var count = explorer.nodes.filter(function (node) { return node.run_id === run.run_id; }).length;
        return '<div class="run-card"><strong>' + escapeHTML(run.run_id) + '</strong>' +
          '<div class="badge-row"><span class="badge ' + escapeHTML(run.run_role || 'base') + '">' + escapeHTML(run.run_role || 'base') + '</span>' +
          '<span class="badge">' + count + ' nodes</span></div>' +
          (run.base_run ? '<div class="muted">base: ' + escapeHTML(run.base_run) + '</div>' : '') +
          '</div>';
      }).join('') || '<p class="placeholder-text">No runs found.</p>'
    );
  }

  function renderInspectorPlaceholder() {
    setHTML('summary-panel', '<p class="placeholder-text">Select a node or edge to inspect.</p>');
    setHTML('payload-panel', '<p class="placeholder-text">Select a node or edge to inspect payload.</p>');
    setHTML('evidence-panel', '<p class="placeholder-text">Select a node or edge to inspect evidence.</p>');
    setHTML('actions-panel', '<p class="placeholder-text">Select a node to inspect actions.</p>');
  }

  function metaGrid(items) {
    return '<div class="meta-grid">' + items.map(function (item) {
      return '<div>' + escapeHTML(item[0]) + '</div><div>' + escapeHTML(item[1] == null ? '' : item[1]) + '</div>';
    }).join('') + '</div>';
  }

  function renderPreviewCard(title, text) {
    return '<div class="inspector-card"><h3>' + escapeHTML(title) + '</h3><pre>' + escapeHTML(text || '') + '</pre></div>';
  }

  function renderNodeInspector(explorer, node) {
    if (node.collapsed_group) {
      renderGroupInspector(explorer, node);
      return;
    }
    var status = diffStatus(node);
    var badges = [
      '<span class="badge">' + escapeHTML(node.kind || 'node') + '</span>',
      '<span class="badge ' + (node.run_role === 'fork' ? 'fork' : '') + '">' + escapeHTML(node.run_role || 'base') + '</span>',
      '<span class="badge ' + (node.status === 'error' ? 'warn' : 'good') + '">' + escapeHTML(node.status || 'recorded') + '</span>',
      '<span class="badge ' + escapeHTML(diffBadgeClass(status)) + '">' + escapeHTML(status) + '</span>',
      explorer.boundaryNodeIds[node.id] ? '<span class="badge changed">fork boundary</span>' : '',
      '<span class="badge">in ' + escapeHTML((node.degree && node.degree.incoming) || 0) + '</span>',
      '<span class="badge">out ' + escapeHTML((node.degree && node.degree.outgoing) || 0) + '</span>'
    ].filter(Boolean).join('');
    var upstream = (explorer.incoming[node.id] || []).map(function (edge) { return edge.source + ' (' + edge.edge_kind + ')'; });
    var downstream = (explorer.outgoing[node.id] || []).map(function (edge) { return edge.target + ' (' + edge.edge_kind + ')'; });
    var timeline = explorer.timelineByNodeId[node.id] || {};
    setHTML(
      'summary-panel',
      '<div class="inspector-card"><h3>' + escapeHTML(node.title || node.id) + '</h3>' +
        '<div class="muted">' + escapeHTML(node.id) + '</div>' +
        '<div class="badge-row">' + badges + '</div>' +
        '<div>' + escapeHTML(node.summary || '') + '</div>' +
        metaGrid([
          ['run', node.run_id],
          ['record', node.record_uid],
          ['path', node.path_id],
          ['branch', node.branch_id],
          ['provider', node.provider],
          ['api', node.api],
          ['order', timeline.order ? timeline.order.index : ''],
          ['created', node.created_at],
          ['duration ms', node.duration_ms],
          ['callsite', node.callsite ? ((node.callsite.file || '') + ':' + (node.callsite.line || '')) : '']
        ]) +
        '</div>' +
        renderPreviewCard('Input preview', node.preview && node.preview.input) +
        renderPreviewCard('Output preview', node.preview && node.preview.output) +
        renderDiffCard(explorer, node) +
        '<div class="inspector-card"><h3>Influence</h3>' +
        '<div class="muted">Upstream</div><pre>' + escapeHTML(upstream.join('\n') || 'No upstream edges.') + '</pre>' +
        '<div class="muted">Downstream</div><pre>' + escapeHTML(downstream.join('\n') || 'No downstream edges.') + '</pre></div>'
    );
    setHTML('payload-panel', '<pre>' + escapeHTML(pretty(node.record || node)) + '</pre>');
    setHTML('evidence-panel', renderEvidenceList(explorer, node.evidence_refs));
    setHTML('actions-panel', renderActionsList(explorer, node.actions || [], node));
    activateGraphSelection(node.id);
  }

  function renderGroupInspector(explorer, node) {
    var group = node.collapsed_group || {};
    var members = (group.member_node_ids || []).map(function (id) { return explorer.nodeById[id]; }).filter(Boolean);
    setHTML(
      'summary-panel',
      '<div class="inspector-card"><h3>' + escapeHTML(group.label || node.title || node.id) + '</h3>' +
        '<div class="badge-row"><span class="badge">collapsed ' + escapeHTML(group.group_by || 'group') + '</span>' +
        '<span class="badge ' + escapeHTML(diffBadgeClass(diffStatus(node))) + '">' + escapeHTML(diffStatus(node)) + '</span>' +
        '<span class="badge">' + escapeHTML(group.count || members.length) + ' nodes</span></div>' +
        '<pre>' + escapeHTML(members.map(function (member) { return member.id + ' / ' + diffStatus(member); }).join('\n')) + '</pre>' +
        '</div>'
    );
    setHTML('payload-panel', '<pre>' + escapeHTML(pretty(group)) + '</pre>');
    setHTML('evidence-panel', '<p class="placeholder-text">Collapsed groups summarize their member nodes.</p>');
    setHTML('actions-panel', '<button type="button" id="expand-current-group">Expand group</button>');
    setTimeout(function () {
      var button = byId('expand-current-group');
      if (button) {
        button.addEventListener('click', function () {
          explorer.state.collapsedGroups[group.key] = false;
          explorer.refresh();
        });
      }
    }, 0);
  }

  function renderDiffCard(explorer, node) {
    var diff = node.diff || {};
    var comparisons = diff.comparisons || [];
    if (!comparisons.length) {
      return '<div class="inspector-card"><h3>Diff</h3><p class="placeholder-text">No fork comparison for this node.</p></div>';
    }
    var items = comparisons.map(function (comparison) {
      var counterpart = comparison.counterpart_id ? explorer.nodeById[comparison.counterpart_id] : null;
      var alignment = findAlignment(explorer, comparison.comparison_id, node.id, comparison.counterpart_id);
      var detail = '';
      if (alignment && alignment.diffs) {
        detail = '<div class="diff-grid">' +
          renderDiffField('input', alignment.diffs.input) +
          renderDiffField('output', alignment.diffs.output) +
          renderDiffField('provenance', alignment.diffs.provenance) +
          '</div>';
      }
      return '<div class="inspector-card"><h3>' + escapeHTML(comparison.status || 'diff') + '</h3>' +
        metaGrid([
          ['comparison', comparison.comparison_id],
          ['counterpart', comparison.counterpart_id || ''],
          ['align by', comparison.alignment_method || ''],
          ['align key', comparison.alignment_key || ''],
          ['changed', (comparison.changed_fields || []).join(', ')]
        ]) +
        (counterpart ? '<div class="muted">Counterpart summary</div><pre>' + escapeHTML(counterpart.summary || counterpart.id) + '</pre>' : '') +
        detail +
        '</div>';
    }).join('');
    return '<div class="inspector-card"><h3>Diff</h3>' +
      '<div class="badge-row"><span class="badge ' + escapeHTML(diffBadgeClass(diff.status)) + '">' + escapeHTML(diff.status || 'baseline') + '</span></div>' +
      '</div>' + items;
  }

  function renderDiffField(name, diff) {
    if (!diff) return '';
    return '<strong>' + escapeHTML(name) + '</strong><div>' +
      '<span class="badge ' + escapeHTML(diff.changed ? 'changed' : 'unchanged') + '">' + escapeHTML(diff.changed ? 'changed' : 'unchanged') + '</span>' +
      '<pre>base: ' + escapeHTML(diff.base_preview || '') + '\n\nfork: ' + escapeHTML(diff.fork_preview || '') + '</pre>' +
      '</div>';
  }

  function findAlignment(explorer, comparisonId, nodeId, counterpartId) {
    var comparison = explorer.comparisons.find(function (item) { return item.id === comparisonId; });
    if (!comparison) return null;
    return (comparison.alignments || []).find(function (alignment) {
      return (alignment.base_node_id === nodeId && alignment.fork_node_id === counterpartId) ||
        (alignment.fork_node_id === nodeId && alignment.base_node_id === counterpartId);
    }) || null;
  }

  function renderEdgeInspector(explorer, edge) {
    setHTML(
      'summary-panel',
      '<div class="inspector-card"><h3>' + escapeHTML(edge.edge_kind || edge.kind || 'edge') + '</h3>' +
        '<div class="muted">' + escapeHTML(edge.id) + '</div>' +
        '<div class="badge-row">' +
        '<span class="badge">' + escapeHTML(edge.edge_kind || edge.kind || 'edge') + '</span>' +
        '<span class="badge ' + (edge.cross_run ? 'fork' : '') + '">' + escapeHTML(edge.cross_run ? 'cross-run' : 'same-run') + '</span>' +
        (explorer.boundaryEdgeIds[edge.id] ? '<span class="badge changed">fork boundary</span>' : '') +
        '</div>' +
        '<div>' + escapeHTML(edge.summary || '') + '</div>' +
        metaGrid([
          ['source', edge.source],
          ['target', edge.target],
          ['role', edge.run_role],
          ['cross run', edge.cross_run ? 'yes' : 'no']
        ]) +
        '</div>'
    );
    setHTML('payload-panel', '<pre>' + escapeHTML(pretty(edge)) + '</pre>');
    setHTML('evidence-panel', renderEvidenceList(explorer, edge.evidence_refs));
    setHTML('actions-panel', '<p class="placeholder-text">Edge actions are not available in this phase.</p>');
    activateGraphSelection(edge.id);
  }

  function renderEvidenceList(explorer, refs) {
    if (!refs || !refs.length) {
      return '<p class="placeholder-text">No evidence linked.</p>';
    }
    return refs.map(function (ref) {
      var item = explorer.evidenceById[ref];
      if (!item) {
        return '<div class="inspector-card"><strong>' + escapeHTML(ref) + '</strong></div>';
      }
      return '<div class="inspector-card"><h3>' + escapeHTML(item.label || item.id) + '</h3>' +
        '<div class="badge-row"><span class="badge">' + escapeHTML(item.evidence_kind || 'evidence') + '</span></div>' +
        metaGrid([
          ['source refs', (item.source_refs || []).join(', ')],
          ['target refs', (item.target_refs || []).join(', ')]
        ]) +
        '<pre>' + escapeHTML(pretty(item.details || item)) + '</pre></div>';
    }).join('');
  }

  function renderActionsList(explorer, actions, node) {
    if (!actions || !actions.length) {
      return '<p class="placeholder-text">No actions available.</p>';
    }
    return actions.map(function (action, index) {
      var availability = (action.availability && action.availability.static_html) || {};
      var params = action.params || {};
      var snippet = params.snippet || params.value || '';
      var canCopy = Boolean(snippet);
      var disabled = !availability.enabled && !canCopy && action.action.indexOf('focus.') !== 0 && action.action !== 'show.raw_record';
      var reason = availability.disabled_reason || action.disabled_reason || '';
      var actionId = 'action-' + index;
      var buttonLabel = canCopy ? 'Copy' : (action.action.indexOf('focus.') === 0 ? 'Apply' : (action.action === 'show.raw_record' ? 'Open' : 'Unavailable'));
      setTimeout(function () {
        var button = byId(actionId);
        if (!button) return;
        button.addEventListener('click', function () {
          if (canCopy) {
            copyText(snippet, actionId + '-status');
          } else if (action.action === 'focus.upstream') {
            var search = byId('search-input');
            if (search) search.value = node.id;
            explorer.applyFocus({ query: node.id, direction: 'upstream' });
          } else if (action.action === 'focus.downstream') {
            var searchDown = byId('search-input');
            if (searchDown) searchDown.value = node.id;
            explorer.applyFocus({ query: node.id, direction: 'downstream' });
          } else if (action.action === 'show.raw_record') {
            activateTab('payload');
          }
        });
      }, 0);
      return '<div class="inspector-card action-card"><h3>' + escapeHTML(action.label || action.action || 'action') + '</h3>' +
        '<div class="muted">' + escapeHTML(action.description || action.action || '') + '</div>' +
        (reason ? '<div class="muted">' + escapeHTML(reason) + '</div>' : '') +
        (snippet ? '<pre>' + escapeHTML(snippet) + '</pre>' : '') +
        '<button id="' + actionId + '" type="button"' + (disabled ? ' disabled' : '') + '>' + escapeHTML(buttonLabel) + '</button>' +
        '<div id="' + actionId + '-status" class="copy-status"></div>' +
        '</div>';
    }).join('');
  }

  function copyText(text, statusId) {
    function done(message) {
      var status = byId(statusId);
      if (status) status.textContent = message;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done('Copied.'); }).catch(function () {
        fallbackCopy(text);
        done('Copied.');
      });
    } else {
      fallbackCopy(text);
      done('Copied.');
    }
  }

  function fallbackCopy(text) {
    var textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    try { document.execCommand('copy'); } catch (error) {}
    document.body.removeChild(textarea);
  }

  function activateGraphSelection(id) {
    document.querySelectorAll('.svg-node, .svg-edge').forEach(function (el) {
      el.classList.toggle('is-selected', false);
    });
  }

  function wireControls(explorer) {
    var searchInput = byId('search-input');
    var focusDirection = byId('focus-direction');
    var focusMaxDepth = byId('focus-max-depth');
    var collapseGroupBy = byId('collapse-group-by');
    function bind(action, handler) {
      var button = document.querySelector('[data-action="' + action + '"]');
      if (button) button.addEventListener('click', handler);
    }
    bind('search', function () { explorer.applySearch(searchInput ? searchInput.value : ''); });
    if (searchInput) {
      searchInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') explorer.applySearch(searchInput.value);
      });
    }
    bind('apply-focus', function () {
      explorer.applyFocus({
        query: searchInput ? searchInput.value : '',
        direction: focusDirection ? focusDirection.value : 'both',
        maxDepth: focusMaxDepth ? focusMaxDepth.value : null
      });
    });
    bind('clear-focus', function () {
      if (searchInput) searchInput.value = '';
      if (focusDirection) focusDirection.value = 'both';
      if (focusMaxDepth) focusMaxDepth.value = '';
      explorer.applySearch('');
      explorer.clearFocus();
    });
    bind('apply-filters', function () { explorer.applyFilters(); });
    bind('reset-filters', function () { explorer.resetFilters(); });
    bind('apply-grouping', function () {
      explorer.applyGrouping(collapseGroupBy ? collapseGroupBy.value : 'none');
    });
    bind('collapse-all-groups', function () { explorer.collapseAllGroups(); });
    bind('expand-all-groups', function () { explorer.expandAllGroups(); });
    bind('show-fork-downstream', function () { explorer.showForkDownstream(); });
    bind('clear-fork-downstream', function () { explorer.clearForkDownstream(); });
    bind('fit-view', function () { explorer.fitView(); });
    bind('reset-view', function () { explorer.resetView(); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    wireTabs();
    var explorer = createExplorer(readGraphIR());
    window.__REPLAY_GRAPH_EXPLORER__ = explorer;
    renderInspectorPlaceholder();
    wireControls(explorer);
    explorer.refresh();
  });
})();
"""
