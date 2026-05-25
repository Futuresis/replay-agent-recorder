# 可视化指南

Replay 的可视化是一个离线、trace-first 的工作流。它读取已有 JSONL trace 文件，并导出 summary、Graph IR JSON、Mermaid 图，或者可离线打开的 HTML explorer。它本身不会记录 run，HTML viewer 也不会执行 replay 或 fork 操作。

维护者视角的实现状态和 Graph IR 契约见：[Visualization implementation status](architecture/visualization-implementation-status.md)。

## 推荐输入目录

建议把 trace 放在项目本地目录，避免运行产物写进 Python package 目录：

```text
.replay/runs/
  agent4-demo.jsonl
  agent4-demo-fork.jsonl
out/
  agent4-demo.html
  agent4-demo-compare.html
```

当前实现如果不传 log directory，可能仍会默认写到 `replay/runs` 这类 package 内部路径。公开 demo 和真实项目中，建议显式传入 `--log-dir .replay/runs` 或 `log_dir=".replay/runs"`。

当前 CLI 形态是：

```text
python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...] [options]
```

`paths` 是主 trace 文件。需要比较 fork 时，用可重复的 `--fork` 参数传入 fork trace。

## 快速示例

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl

python -m replay graph export-ir .replay/runs/agent4-demo.jsonl \
  --output out/graph.json

python -m replay graph mermaid .replay/runs/agent4-demo.jsonl \
  --group-by run \
  --output out/graph.md

python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --output out/graph.html
```

## `summary`

把 JSON summary 打印到 stdout：

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl
```

summary 包括节点数、边数、group 数、run 数、timeline 数、evidence 数、节点类型、边类型、run role、状态分布、diff 状态分布和 cross-run edge 数等信息。

当前行为：`summary` 主要用于终端检查和 CI 检查，默认打印到 stdout。

## `export-ir`

导出稳定的机器可读 Graph IR JSON：

```bash
python -m replay graph export-ir .replay/runs/agent4-demo.jsonl \
  --output out/graph.json
```

Graph IR 包含 nodes、edges、edge layers、groups、runs、evidence、timeline data、layout metadata，以及可选的 base/fork diff metadata。你可以用它构建自己的 viewer，或者在测试里检查图结构。

## `mermaid`

写出 Mermaid Markdown 文件：

```bash
python -m replay graph mermaid .replay/runs/agent4-demo.jsonl \
  --group-by run \
  --output out/graph.md
```

省略 `--output` 时，Mermaid 文本会打印到 stdout：

```bash
python -m replay graph mermaid .replay/runs/agent4-demo.jsonl \
  --group-by path
```

`--group-by` 支持 `none`、`path`、`span` 和 `run`。Mermaid 适合短 trace、PR 评论、架构说明和文档片段。

## `html`

导出离线 HTML explorer：

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --output out/graph.html
```

默认 renderer 是 `svg`，默认 asset mode 是 `inline`，所以生成的 HTML 文件可以离线直接打开，不依赖相邻资源文件。

内置 HTML explorer 支持：

- 检查 node 和 edge
- 按 id、kind、status、title、summary、preview 搜索
- 围绕指定节点 focus
- timeline 导航
- 按 node kind、edge kind、run role、diff status 过滤
- provenance 和 sidecar edge 的 evidence view
- 复制常用 id 或 CLI snippet 的 action card
- 传入 fork trace 时进行 base/fork diff 高亮

### Vendored asset mode

如果希望 HTML 引用导出目录旁边的本地 CSS 和 JS 文件，可以使用 vendored 模式：

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --asset-mode vendored \
  --output out/graph.html
```

默认 SVG renderer 下，vendored 模式会写出：

```text
out/graph.html
out/visualize_assets/visualize.css
out/visualize_assets/visualize.js
```

### React/XYFlow renderer

可选的 XYFlow renderer 使用 `viewer/` 构建出的 bundled assets：

```bash
npm install
npm run build:xyflow-viewer

python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --renderer xyflow \
  --asset-mode vendored \
  --output out/graph-xyflow.html
```

使用 `--renderer xyflow --asset-mode vendored` 时，导出内容为：

```text
out/graph-xyflow.html
out/xyflow_assets/xyflow-viewer.css
out/xyflow_assets/xyflow-viewer.js
```

如果想要单文件 HTML，用 `--asset-mode inline`；如果想要更小的 HTML 和相邻本地资源，用 `--asset-mode vendored`。

## 比较 base run 和 fork run

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --fork .replay/runs/agent4-demo-fork.jsonl \
  --output out/agent4-demo-compare.html
```

diff metadata 会把节点标记为：

| 状态 | 含义 |
|---|---|
| `unchanged` | base 和 fork 中出现了同一个语义节点。 |
| `changed` | 匹配节点存在，但内容发生变化。 |
| `new` | 只在 fork 中出现。 |
| `missing` | 只在 base 中出现。 |
| `downstream` | 位于 fork breakpoint 的下游。 |

支持多个 `--fork`：

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl \
  --fork .replay/runs/agent4-demo-fork.jsonl \
  --fork .replay/runs/agent4-demo-message-fork.jsonl
```

## 导出前 focus

Focus 可以围绕某个节点导出更小的局部图：

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --focus agent4-demo:rec_000001 \
  --direction downstream \
  --max-depth 2 \
  --output out/focused.html
```

focus id 通常是 `<run_id>:<record_uid>`，例如 `agent4-demo:rec_000001`。

## 通用选项

| 选项 | 取值 | 作用 |
|---|---|---|
| `--fork PATH` | 可重复 path | 添加一个 fork trace，并在存在 fork metadata 时启用 base/fork comparison metadata。 |
| `--focus NODE_ID` | 通常是 `<run_id>:<record_uid>` | 在写出或打印前，把图过滤到某个节点附近。 |
| `--direction` | `upstream`、`downstream`、`both` | 与 `--focus` 配合使用，默认 `both`。 |
| `--max-depth N` | 整数 | 限制 focus graph traversal depth。 |
| `--title TEXT` | 字符串 | 设置 Graph IR title 和 HTML/Mermaid title。 |
| `--output PATH` | path | `export-ir` 和 `html` 必填，`mermaid` 可选，`summary` 忽略。 |
| `--group-by` | `none`、`path`、`span`、`run` | Mermaid 使用；HTML 中的 grouping 在浏览器里交互控制。 |

HTML-only options：

| 选项 | 取值 | 默认 | 作用 |
|---|---|---:|---|
| `--asset-mode` | `inline`、`vendored` | `inline` | inline 把 CSS/JS 嵌入单个 HTML；vendored 写出相邻本地资源。 |
| `--renderer` | `svg`、`xyflow` | `svg` | 选择内置 SVG explorer 或静态 XYFlow/React Flow explorer。 |

## 推荐的发布截图

为了让 GitHub 首页更直观，可以从 `out/agent4-demo-compare.html` 截图并保存为：

```text
docs/assets/replay-graph.png
```

然后在 `README.md` 顶部附近加入：

```markdown
![Replay graph explorer](docs/assets/replay-graph.png)
```

当前 README 先使用 Mermaid 图，避免截图文件不存在时出现 broken image。

## 当前限制

- 可视化读取已有 JSONL trace，不是 live recorder。
- 静态 HTML explorer 是只读的。
- 浏览器里的 action card 可以复制 id 或 CLI snippet，但不会执行 replay。
- visualizer 目前不支持 tool-node breakpoint 和 tool output override。
- server-backed workbench actions 不属于当前静态导出能力面。
- 特别大的 trace 可能需要先过滤或 focus export。
- 可视化质量取决于运行时捕获到了多少 semantic 和 tool provenance。
