# Visualization implementation status

This document describes the current visualization architecture and implementation status of Replay Agent Recorder. It is intended for maintainers and contributors.

For user-facing visualization commands, see [`../visualization.md`](../visualization.md). For the Chinese user guide, see [`../visualization.zh-CN.md`](../visualization.zh-CN.md).

This is not a product roadmap. Workbench/server-backed actions mentioned here are not part of the current static HTML export surface unless explicitly marked as implemented.

## English

This document captures the implemented state of Replay visualization. It is a status document, not a promise that every planned workbench feature exists today.

## Phase 0: Current surface

The visualization pipeline is record-first, not live-rendered during recording.
Replay reads JSONL trace files after a run and builds graph views from those
records.

Current inputs:

- Primary `llm` records.
- Primary `tool` records.
- Sidecar `edge` records with `edge_kind` such as `data` or `control`.
- Optional first-line `fork_metadata` records in fork traces.
- Optional `metadata.provenance`, `metadata.semantic`, `metadata.spans`, and
  `effects.filesystem` fields on primary records.

Current CLI outputs:

| Command | Output | Interactivity |
| --- | --- | --- |
| `python -m replay graph summary <trace.jsonl>` | JSON counts and kinds | None |
| `python -m replay graph export-ir <trace.jsonl> --output graph.json` | Graph IR JSON | Consumer-defined |
| `python -m replay graph mermaid <trace.jsonl>` | Mermaid flowchart text or Markdown | Static |
| `python -m replay graph html <trace.jsonl> --output graph.html` | Offline HTML explorer | Browser-side search, focus, filters, timeline, selection, inspector, copy actions |

Current limits:

- Static HTML does not execute replay actions.
- Tool-node replay breakpoints and tool output overrides are not available.
- Workbench/server mode is intentionally out of scope for the current static
  export surface.

## Phase 1: Graph IR contract

The Graph IR is the stable contract between trace parsing, static exports, and a
future local workbench.

Top-level shape:

```json
{
  "schema_version": 1,
  "meta": {},
  "graph": {
    "nodes": [],
    "edges": [],
    "groups": [],
    "runs": [],
    "stats": {},
    "timeline": {
      "items": []
    },
    "layout": {},
    "diff": {}
  },
  "evidence": {
    "items": []
  }
}
```

Node fields:

- `id`: stable graph id, always `<run_id>:<record_uid>`.
- `run_id`, `record_uid`, `path_id`, `branch_id`, `run_role`.
- `kind`: currently `llm` or `tool`.
- `status`: `recorded`, `replay`, `override`, `live`, or `error`.
- `title`, `summary`, `preview.input`, `preview.output`.
- `callsite`, `spans`, `semantic`, `record`.
- `degree.incoming`, `degree.outgoing`.
- `evidence_refs`: ids into `evidence.items`.
- `actions`: browser/static actions plus disabled workbench action metadata.
- `diff`: optional base/fork comparison status and counterpart details.

Edge fields:

- `id`, `source`, `target`.
- `edge_kind`: `data`, `control`, `fork`, or another sidecar kind.
- `cross_run`, `run_role`, `summary`, `metadata`.
- `evidence_refs`: ids explaining why the edge exists.

Evidence fields:

- `id`, `evidence_kind`, `label`.
- `source_refs`, `target_refs`.
- `details`, including raw sidecar edge records when available.

Timeline fields:

- `graph.timeline.items[]`: ordered by original record sequence.
- Each item carries node id, run, role, kind, status, path, timestamp, duration,
  and span names when present.

Layout fields:

- `graph.layout`: declares the browser-side layered layout contract.
- The layout is cacheable by visible nodes and edges in the static HTML explorer.

Action fields:

- `action`, `label`, `description`, `params`.
- `availability.static_html`: whether the offline HTML can perform the action.
- `availability.workbench`: reserved for a future server-backed workbench.

## Phase 2: Static HTML explorer

The static HTML explorer is intentionally self-contained in default `inline`
mode. It uses browser-native SVG rendering instead of external libraries, so the
generated file can be opened offline.

Implemented interactions:

- Click a node or edge to inspect it.
- Search across id, run, kind, status, title, summary, and previews.
- View clickable search results.
- Focus upstream, downstream, or both by search query or node id.
- Filter by node kind, edge kind, run role, and diff status when diff metadata is
  present.
- Inspect structured Summary, Payload, Evidence, and Actions tabs.
- Copy node ids, record ids, and CLI snippets from action cards.
- Navigate through a timeline panel linked to node selection.
- Collapse or group by run, path, or span.
- Use a real SVG minimap rendered from the current layout.
- Export vendored mode with local `visualize_assets/visualize.css` and
  `visualize_assets/visualize.js` next to the HTML file.

The HTML explorer remains a read-only graph browser. Replay execution and
forking from the browser are future workbench responsibilities.

## Phases 3 and 4: Workbench features not implemented here

Earlier planning discussions reserved later phases for server-backed workbench
actions such as creating forks directly from the browser. Those features are not
part of the current static visualization implementation. This document therefore
jumps from static export work to the implemented base/fork diff and large-graph
experience.

## Phase 5: Base/fork diff view

The Graph IR derives diff metadata whenever a base trace is loaded with one or
more fork traces.

Diff IR fields:

- `graph.diff.schema_version`: contract version for diff metadata.
- `graph.diff.base_run` and `graph.diff.fork_runs`.
- `graph.diff.comparisons[]`: one comparison per base/fork pair.
- `comparison.breakpoint`: base node, fork node, and fork edge ids for the fork
  boundary.
- `comparison.alignments[]`: base/fork node pairs with alignment method,
  alignment key, changed fields, and input/output/provenance previews.
- `comparison.changed_node_ids`, `unchanged_node_ids`, `new_node_ids`,
  `missing_node_ids`, and `downstream_node_ids`.
- `node.diff.status`: `changed`, `unchanged`, `new`, `missing`, or `baseline`.
- `node.diff.comparisons[]`: counterpart id and alignment details for Inspector
  rendering.

Alignment uses the fork boundary first, then progressively more general keys:
record uid plus path, record uid plus callsite, callsite, then path. This avoids
false matches when fork traces restart record numbering.

The static HTML explorer consumes that IR directly. It highlights fork boundary
nodes and edges, colors changed/new/missing/unchanged nodes, adds a Diff status
filter, provides a "Fork downstream" graph filter, and shows input, output, and
provenance differences in the Inspector.

## Phase 6: Timeline and large graph experience

The Graph IR includes `graph.timeline.items`, ordered by original record
sequence and carrying node id, run, role, kind, status, path, timestamp,
duration, and span names. `graph.layout` declares the browser-side layered
layout contract and marks it cacheable.

The HTML explorer adds:

- A timeline panel linked to node selection.
- A search result list with clickable matching nodes.
- Grouping and collapse controls by run, path, or span.
- Browser-side layout caching keyed by visible nodes and edges.
- A real SVG minimap rendered from the current layout.
- Rendered-node aggregation for collapsed groups, reducing SVG item count on
  larger traces.

The explorer still does not execute backend actions. It remains safe to share as
an offline HTML artifact.

## 中文

本文档记录 Replay 可视化的当前实现状态。它是状态说明，不表示所有曾经规划过的
workbench 功能都已经存在。

## Phase 0：当前能力面

可视化 pipeline 是 record-first 的，不是在 record 过程中 live render。Replay 会在一次运行
结束后读取 JSONL trace 文件，并从这些记录中构建 graph view。

当前输入：

- 主 `llm` 记录。
- 主 `tool` 记录。
- 带 `edge_kind` 的 sidecar `edge` 记录，例如 `data` 或 `control`。
- fork trace 中可选的首行 `fork_metadata` 记录。
- 主记录上可选的 `metadata.provenance`、`metadata.semantic`、`metadata.spans` 和
  `effects.filesystem` 字段。

当前 CLI 输出：

| Command | Output | Interactivity |
| --- | --- | --- |
| `python -m replay graph summary <trace.jsonl>` | JSON 计数和类型 | 无 |
| `python -m replay graph export-ir <trace.jsonl> --output graph.json` | Graph IR JSON | 由消费方决定 |
| `python -m replay graph mermaid <trace.jsonl>` | Mermaid flowchart 文本或 Markdown | 静态 |
| `python -m replay graph html <trace.jsonl> --output graph.html` | 离线 HTML explorer | 浏览器端搜索、focus、过滤、timeline、选择、inspector、复制操作 |

当前限制：

- 静态 HTML 不执行 replay 操作。
- 不支持 tool-node replay breakpoint 和 tool output override。
- workbench/server mode 有意不包含在当前静态导出能力面中。

## Phase 1：Graph IR 契约

Graph IR 是 trace parsing、静态导出和未来本地 workbench 之间的稳定契约。

顶层结构：

```json
{
  "schema_version": 1,
  "meta": {},
  "graph": {
    "nodes": [],
    "edges": [],
    "groups": [],
    "runs": [],
    "stats": {},
    "timeline": {
      "items": []
    },
    "layout": {},
    "diff": {}
  },
  "evidence": {
    "items": []
  }
}
```

节点字段：

- `id`: 稳定 graph id，始终是 `<run_id>:<record_uid>`。
- `run_id`、`record_uid`、`path_id`、`branch_id`、`run_role`。
- `kind`: 当前为 `llm` 或 `tool`。
- `status`: `recorded`、`replay`、`override`、`live` 或 `error`。
- `title`、`summary`、`preview.input`、`preview.output`。
- `callsite`、`spans`、`semantic`、`record`。
- `degree.incoming`、`degree.outgoing`。
- `evidence_refs`: 指向 `evidence.items` 的 id。
- `actions`: 浏览器/静态 action，以及被禁用的 workbench action 元数据。
- `diff`: 可选的 base/fork 对比状态和 counterpart 详情。

边字段：

- `id`、`source`、`target`。
- `edge_kind`: `data`、`control`、`fork` 或其他 sidecar 类型。
- `cross_run`、`run_role`、`summary`、`metadata`。
- `evidence_refs`: 解释该边为何存在的 evidence id。

Evidence 字段：

- `id`、`evidence_kind`、`label`。
- `source_refs`、`target_refs`。
- `details`: 可包含原始 sidecar edge 记录。

Timeline 字段：

- `graph.timeline.items[]`: 按原始记录顺序排列。
- 每个 item 携带 node id、run、role、kind、status、path、timestamp、duration，以及存在时的
  span 名称。

Layout 字段：

- `graph.layout`: 声明浏览器端 layered layout 契约。
- 静态 HTML explorer 会按可见节点和边缓存 layout。

Action 字段：

- `action`、`label`、`description`、`params`。
- `availability.static_html`: 离线 HTML 是否能执行该 action。
- `availability.workbench`: 预留给未来 server-backed workbench。

## Phase 2：静态 HTML explorer

静态 HTML explorer 在默认 `inline` 模式下是自包含的。它使用浏览器原生 SVG 渲染，而不是
外部库，因此生成的文件可以离线打开。

已实现交互：

- 点击节点或边进行检查。
- 在 id、run、kind、status、title、summary 和 preview 中搜索。
- 查看可点击的搜索结果。
- 通过搜索 query 或 node id focus upstream、downstream 或 both。
- 按 node kind、edge kind、run role 过滤；有 diff metadata 时也可按 diff status 过滤。
- 检查结构化的 Summary、Payload、Evidence 和 Actions tab。
- 从 action card 复制 node id、record id 和 CLI snippet。
- 使用与节点选择联动的 timeline panel 导航。
- 按 run、path 或 span 折叠/分组。
- 使用从当前 layout 渲染出的真实 SVG minimap。
- 以 vendored 模式导出，并在 HTML 文件旁生成本地 `visualize_assets/visualize.css` 和
  `visualize_assets/visualize.js`。

HTML explorer 仍然是只读 graph browser。浏览器中执行 replay 和 fork 属于未来 workbench 的职责。

## Phase 3 和 4：这里未实现的 workbench 功能

早期规划把后续阶段留给 server-backed workbench action，例如直接从浏览器创建 fork。这些功能
不是当前静态可视化实现的一部分。因此本文档会从静态导出直接跳到已实现的 base/fork diff 和
large-graph experience。

## Phase 5：Base/fork diff view

当加载 base trace 并传入一个或多个 fork trace 时，Graph IR 会派生 diff metadata。

Diff IR 字段：

- `graph.diff.schema_version`: diff metadata 的契约版本。
- `graph.diff.base_run` 和 `graph.diff.fork_runs`。
- `graph.diff.comparisons[]`: 每个 base/fork pair 一条 comparison。
- `comparison.breakpoint`: fork 边界的 base node、fork node 和 fork edge id。
- `comparison.alignments[]`: base/fork node pair，包含 alignment method、alignment key、
  changed fields，以及 input/output/provenance preview。
- `comparison.changed_node_ids`、`unchanged_node_ids`、`new_node_ids`、
  `missing_node_ids` 和 `downstream_node_ids`。
- `node.diff.status`: `changed`、`unchanged`、`new`、`missing` 或 `baseline`。
- `node.diff.comparisons[]`: Inspector 渲染使用的 counterpart id 和 alignment 详情。

alignment 会先使用 fork boundary，然后逐步使用更泛化的 key：record uid 加 path、
record uid 加 callsite、callsite，最后是 path。这样可以避免 fork trace 重新开始 record
编号时出现错误匹配。

静态 HTML explorer 会直接消费这些 IR。它会高亮 fork boundary 节点和边，为
changed/new/missing/unchanged 节点着色，添加 Diff status filter，提供 "Fork downstream"
graph filter，并在 Inspector 中显示 input、output 和 provenance 差异。

## Phase 6：Timeline 和大图体验

Graph IR 包含 `graph.timeline.items`，按原始记录顺序排列，并携带 node id、run、role、kind、
status、path、timestamp、duration 和 span 名称。`graph.layout` 声明浏览器端 layered layout
契约，并标记为可缓存。

HTML explorer 增加了：

- 与节点选择联动的 timeline panel。
- 带可点击匹配节点的搜索结果列表。
- 按 run、path 或 span 分组和折叠控制。
- 按可见节点和边缓存的浏览器端 layout。
- 从当前 layout 渲染出的真实 SVG minimap。
- collapsed group 的 rendered-node aggregation，用于减少大 trace 中的 SVG item 数量。

explorer 仍然不执行后端 action。它依然可以作为离线 HTML artifact 安全分享。
