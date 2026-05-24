# Visualization quickstart

## English

Replay visualization is an offline, trace-first workflow. It reads existing
JSONL run files and exports summaries, Graph IR JSON, Mermaid diagrams, or a
self-contained HTML explorer. It does not record runs by itself, and the HTML
viewer does not execute replay or fork actions.

The examples below assume you already activated the repository's local `uv`
virtual environment and installed the package from the repository root:

```bash
python -m replay graph --help
```

If you prefer not to activate the virtual environment manually, prefix the same
commands with `uv run`.

## Inputs

Use one or more JSONL trace files, usually from `replay/runs/`. The checked-in
showcase traces are good starting points:

- `replay/runs/agent4-demo.jsonl`
- `replay/runs/agent4-demo.jsonl`
- `replay/runs/agent4-demo-fork.jsonl`

The current CLI shape is:

```text
python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...] [options]
```

`paths` are the primary trace files. Add fork traces with repeated `--fork`
options when you want comparison metadata.

## Commands

### `summary`

Print a JSON summary to stdout:

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl
```

The summary contains `node_count`, `edge_count`, `default_edge_count`,
`group_count`, `run_count`, `timeline_count`, `evidence_count`, `node_kinds`,
`edge_kinds`, `default_edge_kinds`, `run_roles`, `status_counts`,
`diff_status_counts`, and `cross_run_edge_count`.

Current behavior: `summary` always prints to stdout. The shared parser accepts
`--output`, but this subcommand does not write a file.

### `export-ir`

Write the stable machine-readable Graph IR JSON:

```bash
python -m replay graph export-ir replay/runs/agent4-demo.jsonl --output out/graph.json
```

`--output` is required for `export-ir`. Graph IR contains nodes, edges, edge
layers, groups, runs, evidence, timeline data, layout metadata, and optional
base/fork diff metadata.

### `mermaid`

Write a Mermaid Markdown file:

```bash
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by run --output out/graph.md
```

If `--output` is omitted, Mermaid text is printed to stdout:

```bash
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by path
```

`--group-by` is applied by the Mermaid exporter and accepts `none`, `path`,
`span`, or `run`. The default parser value is `path`.

### `html`

Write an offline HTML explorer:

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/graph.html
```

`--output` is required for `html`. The default renderer is `svg`, and the
default asset mode is `inline`, so the generated file can be opened offline
without adjacent asset files.

Use vendored assets when you want the HTML to reference files next to the export:

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --asset-mode vendored --output out/graph.html
```

With the default `svg` renderer, vendored mode writes:

```text
out/graph.html
out/visualize_assets/visualize.css
out/visualize_assets/visualize.js
```

The HTML exporter also supports the static XYFlow/React Flow renderer:

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --renderer xyflow --asset-mode vendored --output out/graph.html
```

With `--renderer xyflow --asset-mode vendored`, the exporter writes:

```text
out/graph.html
out/xyflow_assets/xyflow-viewer.css
out/xyflow_assets/xyflow-viewer.js
```

If XYFlow assets are missing, build them from the repository root:

```bash
npm run build:xyflow-viewer
```

## Shared options

These options are accepted by all graph subcommands:

| Option | Values | Effect |
| --- | --- | --- |
| `--fork PATH` | repeatable path | Adds one fork trace to the same graph and enables base/fork comparison metadata when fork metadata is present. |
| `--focus NODE_ID` | usually `<run_id>:<record_uid>` | Filters the exported graph around one node before writing or printing. |
| `--direction` | `upstream`, `downstream`, `both` | Direction used with `--focus`; default is `both`. |
| `--max-depth N` | integer | Limits graph traversal depth used with `--focus`. |
| `--title TEXT` | string | Sets the Graph IR title and the HTML/Mermaid title where applicable. |
| `--output PATH` | path | Required by `export-ir` and `html`, optional for `mermaid`, ignored by `summary`. |
| `--group-by` | `none`, `path`, `span`, `run` | Used by `mermaid`; accepted but not applied by `summary`, `export-ir`, or `html`. HTML grouping is controlled interactively in the browser. |

HTML-only options:

| Option | Values | Default | Effect |
| --- | --- | --- | --- |
| `--asset-mode` | `inline`, `vendored` | `inline` | Inline embeds CSS/JS into one HTML file. Vendored writes adjacent local asset files. |
| `--renderer` | `svg`, `xyflow` | `svg` | Chooses the built-in SVG explorer or the static XYFlow/React Flow explorer. |

## Compare a base run and fork run

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --fork replay/runs/agent4-demo-fork.jsonl --output out/agent4-compare.html
```

The generated comparison HTML highlights the fork boundary, marks changed,
unchanged, new, and missing nodes, links the graph with a timeline, and includes
a "Fork downstream" filter for reading the affected path.

Multiple `--fork` values are accepted:

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl --fork replay/runs/agent4-demo-fork.jsonl --fork replay/runs/agent4-demo-message-fork.jsonl
```

## Focus before export

Focus can be applied to any graph export:

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl --focus agent4-demo:rec_000001 --direction downstream --max-depth 1
```

A focus id is normally `<run_id>:<record_uid>`, for example
`agent4-demo:rec_000001`.

## Current limits

- Visualization reads existing JSONL traces; it is not a live recorder.
- The static HTML explorer is read-only.
- Browser action cards can copy IDs or CLI snippets, but they do not run replay.
- Tool-node breakpoints and tool output overrides are not available from the
  visualizer.
- Server-backed workbench actions are intentionally outside the current static
  export surface.

## 中文

Replay 可视化是一个离线、trace-first 的工作流。它读取已有 JSONL run 文件，并导出
summary、Graph IR JSON、Mermaid 图，或自包含的 HTML explorer。它本身不会记录 run，
HTML viewer 也不会执行 replay 或 fork 操作。

下面的示例假设你已经激活仓库本地 `uv` 虚拟环境，并且在仓库根目录完成了安装：

```bash
python -m replay graph --help
```

如果你不想手动激活虚拟环境，也可以给同样的命令加上 `uv run` 前缀。

## 输入

输入是一个或多个 JSONL trace 文件，通常位于 `replay/runs/`。仓库中已保存的 showcase
trace 是很好的起点：

- `replay/runs/agent4-demo.jsonl`
- `replay/runs/agent4-demo.jsonl`
- `replay/runs/agent4-demo-fork.jsonl`

当前 CLI 形态是：

```text
python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...] [options]
```

`paths` 是主要 trace 文件。需要比较 fork 时，用可重复的 `--fork` 传入 fork trace。

## 命令

### `summary`

把 JSON summary 打印到 stdout：

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl
```

summary 包含 `node_count`、`edge_count`、`default_edge_count`、`group_count`、
`run_count`、`timeline_count`、`evidence_count`、`node_kinds`、`edge_kinds`、
`default_edge_kinds`、`run_roles`、`status_counts`、`diff_status_counts` 和
`cross_run_edge_count`。

当前行为：`summary` 始终输出到 stdout。共享 parser 接受 `--output`，但这个子命令
不会写文件。

### `export-ir`

写出稳定的机器可读 Graph IR JSON：

```bash
python -m replay graph export-ir replay/runs/agent4-demo.jsonl --output out/graph.json
```

`export-ir` 必须传 `--output`。Graph IR 包含 nodes、edges、edge layers、groups、
runs、evidence、timeline 数据、layout 元数据，以及可选的 base/fork diff 元数据。

### `mermaid`

写出 Mermaid Markdown 文件：

```bash
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by run --output out/graph.md
```

如果省略 `--output`，Mermaid 文本会输出到 stdout：

```bash
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by path
```

`--group-by` 会被 Mermaid exporter 使用，可选值为 `none`、`path`、`span` 或 `run`。
parser 默认值是 `path`。

### `html`

写出离线 HTML explorer：

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/graph.html
```

`html` 必须传 `--output`。默认 renderer 是 `svg`，默认 asset mode 是 `inline`，
因此生成的 HTML 可以离线打开，不依赖相邻资源文件。

如果希望 HTML 引用导出目录旁边的本地资源文件，可以使用 vendored assets：

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --asset-mode vendored --output out/graph.html
```

默认 `svg` renderer 的 vendored 模式会写出：

```text
out/graph.html
out/visualize_assets/visualize.css
out/visualize_assets/visualize.js
```

HTML exporter 也支持静态 XYFlow/React Flow renderer：

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --renderer xyflow --asset-mode vendored --output out/graph.html
```

使用 `--renderer xyflow --asset-mode vendored` 时会写出：

```text
out/graph.html
out/xyflow_assets/xyflow-viewer.css
out/xyflow_assets/xyflow-viewer.js
```

如果缺少 XYFlow assets，在仓库根目录构建：

```bash
npm run build:xyflow-viewer
```

## 共享参数

所有 graph 子命令都接受这些参数：

| 参数 | 可选值 | 作用 |
| --- | --- | --- |
| `--fork PATH` | 可重复 path | 把一个 fork trace 加入同一张图；trace 中有 fork 元数据时会启用 base/fork 比较信息。 |
| `--focus NODE_ID` | 通常是 `<run_id>:<record_uid>` | 导出前围绕某个节点过滤图。 |
| `--direction` | `upstream`、`downstream`、`both` | 配合 `--focus` 使用；默认 `both`。 |
| `--max-depth N` | 整数 | 配合 `--focus` 限制图遍历深度。 |
| `--title TEXT` | 字符串 | 设置 Graph IR title，并在适用时设置 HTML/Mermaid 标题。 |
| `--output PATH` | path | `export-ir` 和 `html` 必填，`mermaid` 可选，`summary` 忽略。 |
| `--group-by` | `none`、`path`、`span`、`run` | 由 `mermaid` 使用；`summary`、`export-ir` 和 `html` 接受但不应用。HTML 分组在浏览器里交互控制。 |

HTML 专用参数：

| 参数 | 可选值 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `--asset-mode` | `inline`、`vendored` | `inline` | inline 把 CSS/JS 嵌入单个 HTML；vendored 写出相邻本地资源文件。 |
| `--renderer` | `svg`、`xyflow` | `svg` | 选择内置 SVG explorer 或静态 XYFlow/React Flow explorer。 |

## 比较 base run 和 fork run

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --fork replay/runs/agent4-demo-fork.jsonl --output out/agent4-compare.html
```

生成的 comparison HTML 会高亮 fork 边界，标记 changed、unchanged、new 和 missing 节点，
把图和 timeline 联动，并提供 "Fork downstream" filter 来阅读受影响路径。

可以传入多个 `--fork`：

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl --fork replay/runs/agent4-demo-fork.jsonl --fork replay/runs/agent4-demo-message-fork.jsonl
```

## 导出前 focus

focus 可以用于任何 graph export：

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl --focus agent4-demo:rec_000001 --direction downstream --max-depth 1
```

focus id 通常是 `<run_id>:<record_uid>`，例如
`agent4-demo:rec_000001`。

## 当前限制

- 可视化读取已有 JSONL trace；它不是 live recorder。
- 静态 HTML explorer 是只读的。
- 浏览器中的 action card 可以复制 ID 或 CLI snippet，但不会运行 replay。
- visualizer 目前不支持 tool-node breakpoint 或 tool output override。
- 需要服务端支持的 workbench 操作不属于当前静态导出范围。
