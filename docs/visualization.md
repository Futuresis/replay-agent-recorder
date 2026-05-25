# Visualization guide

Replay visualization is an offline, trace-first workflow. It reads existing JSONL trace files and exports summaries, Graph IR JSON, Mermaid diagrams, or a standalone HTML explorer. It does not record runs by itself, and the HTML viewer does not execute replay or fork actions.

For maintainer-facing implementation notes and the Graph IR contract, see [Visualization implementation status](architecture/visualization-implementation-status.md).

## Recommended input layout

Use project-local trace files so run artifacts stay outside the Python package directory:

```text
.replay/runs/
  agent4-demo.jsonl
  agent4-demo-fork.jsonl
out/
  agent4-demo.html
  agent4-demo-compare.html
```

The current implementation may still default to an internal package path such as `replay/runs` when no log directory is supplied. For public demos and user projects, prefer passing `--log-dir .replay/runs` or `log_dir=".replay/runs"`.

The CLI shape is:

```text
python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...] [options]
```

`paths` are primary trace files. Add fork traces with repeated `--fork` options when you want comparison metadata.

## Quick examples

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

Print a JSON summary to stdout:

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl
```

The summary includes graph counts and distribution fields such as node count, edge count, group count, run count, timeline count, evidence count, node kinds, edge kinds, run roles, status counts, diff status counts, and cross-run edge counts.

Current behavior: `summary` prints to stdout. The shared parser may accept `--output`, but this subcommand is primarily intended for terminal inspection and CI checks.

## `export-ir`

Write stable machine-readable Graph IR JSON:

```bash
python -m replay graph export-ir .replay/runs/agent4-demo.jsonl \
  --output out/graph.json
```

Graph IR contains nodes, edges, edge layers, groups, runs, evidence, timeline data, layout metadata, and optional base/fork diff metadata. Use it when you want to build your own viewer or test graph structure programmatically.

## `mermaid`

Write a Mermaid Markdown file:

```bash
python -m replay graph mermaid .replay/runs/agent4-demo.jsonl \
  --group-by run \
  --output out/graph.md
```

When `--output` is omitted, Mermaid text is printed to stdout:

```bash
python -m replay graph mermaid .replay/runs/agent4-demo.jsonl \
  --group-by path
```

`--group-by` accepts `none`, `path`, `span`, or `run`. Mermaid is useful for short traces, PR comments, architecture notes, and documentation snippets.

## `html`

Write an offline HTML explorer:

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --output out/graph.html
```

The default renderer is `svg`, and the default asset mode is `inline`, so the generated file can be opened offline without adjacent asset files.

The built-in HTML explorer supports:

- node and edge inspection
- search across ids, kinds, status, titles, summaries, and previews
- focus views around selected nodes
- timeline navigation
- filters by node kind, edge kind, run role, and diff status
- evidence views for provenance and sidecar edges
- action cards that copy useful ids or CLI snippets
- base/fork diff highlighting when fork traces are supplied

### Vendored asset mode

Use vendored assets when you want the HTML to reference local CSS and JS files next to the export:

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --asset-mode vendored \
  --output out/graph.html
```

With the default SVG renderer, vendored mode writes:

```text
out/graph.html
out/visualize_assets/visualize.css
out/visualize_assets/visualize.js
```

### React/XYFlow renderer

The optional XYFlow renderer uses bundled assets built from `viewer/`:

```bash
npm install
npm run build:xyflow-viewer

python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --renderer xyflow \
  --asset-mode vendored \
  --output out/graph-xyflow.html
```

With `--renderer xyflow --asset-mode vendored`, the exporter writes:

```text
out/graph-xyflow.html
out/xyflow_assets/xyflow-viewer.css
out/xyflow_assets/xyflow-viewer.js
```

Use `--asset-mode inline` for a single standalone HTML file, or `--asset-mode vendored` when you want a smaller HTML file plus adjacent local assets.

## Compare a base run and a fork

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --fork .replay/runs/agent4-demo-fork.jsonl \
  --output out/agent4-demo-compare.html
```

Diff metadata can mark nodes as:

| Status | Meaning |
|---|---|
| `unchanged` | Same semantic node appears in base and fork. |
| `changed` | Matching node exists but content differs. |
| `new` | Node appears only in the fork. |
| `missing` | Node appears only in the base. |
| `downstream` | Node is downstream of the fork breakpoint. |

Multiple `--fork` values are accepted:

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl \
  --fork .replay/runs/agent4-demo-fork.jsonl \
  --fork .replay/runs/agent4-demo-message-fork.jsonl
```

## Focus before export

Focus lets you export a smaller graph around one node:

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --focus agent4-demo:rec_000001 \
  --direction downstream \
  --max-depth 2 \
  --output out/focused.html
```

A focus id is normally `<run_id>:<record_uid>`, for example `agent4-demo:rec_000001`.

## Shared options

| Option | Values | Effect |
|---|---|---|
| `--fork PATH` | repeatable path | Adds one fork trace to the same graph and enables base/fork comparison metadata when fork metadata is present. |
| `--focus NODE_ID` | usually `<run_id>:<record_uid>` | Filters the exported graph around one node before writing or printing. |
| `--direction` | `upstream`, `downstream`, `both` | Direction used with `--focus`; default is `both`. |
| `--max-depth N` | integer | Limits graph traversal depth used with `--focus`. |
| `--title TEXT` | string | Sets the Graph IR title and the HTML/Mermaid title where applicable. |
| `--output PATH` | path | Required by `export-ir` and `html`, optional for `mermaid`, ignored by `summary`. |
| `--group-by` | `none`, `path`, `span`, `run` | Used by Mermaid; accepted by other exporters for compatibility. HTML grouping is controlled interactively in the browser. |

HTML-only options:

| Option | Values | Default | Effect |
|---|---|---:|---|
| `--asset-mode` | `inline`, `vendored` | `inline` | Inline embeds CSS/JS into one HTML file. Vendored writes adjacent local asset files. |
| `--renderer` | `svg`, `xyflow` | `svg` | Chooses the built-in SVG explorer or the static XYFlow/React Flow explorer. |

## Suggested release screenshot

For a polished GitHub landing page, generate a screenshot from `out/agent4-demo-compare.html` and save it as:

```text
docs/assets/replay-graph.png
```

Then add this near the top of `README.md`:

```markdown
![Replay graph explorer](docs/assets/replay-graph.png)
```

The current README uses a Mermaid diagram so it will not show a broken image before a screenshot exists.

## Current limits

- Visualization reads existing JSONL traces; it is not a live recorder.
- The static HTML explorer is read-only.
- Browser action cards can copy ids or CLI snippets, but they do not run replay.
- Tool-node breakpoints and tool output overrides are not available from the visualizer.
- Server-backed workbench actions are intentionally outside the current static export surface.
- Very large traces may need filtering or focus export before visualization.
- Visualization quality depends on how much semantic and tool provenance the run captured.
