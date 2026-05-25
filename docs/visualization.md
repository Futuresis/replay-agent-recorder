# Visualization guide

Replay visualizes existing JSONL traces. It does not execute the agent; it reads trace files and exports graph formats.

## Input

A trace is a JSONL file created by `replay.record(...)`, `replay.replay(...)` in fork mode, or the `replay` CLI.

Recommended local project layout:

```text
.replay/runs/
  agent4-demo.jsonl
  agent4-demo-fork.jsonl
out/
  agent4-demo.html
  agent4-demo-compare.html
```

The current implementation also has an internal package default under `replay/runs`. For project repositories, prefer passing `--log-dir .replay/runs` or `log_dir=".replay/runs"` so traces stay outside the package directory.

## Commands

### Summary JSON

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl
```

Use this for quick inspection, CI checks, and debugging graph counts.

### Graph IR JSON

```bash
python -m replay graph export-ir .replay/runs/agent4-demo.jsonl \
  --output out/graph.json
```

Graph IR is the normalized intermediate representation used by the Mermaid and HTML exporters.

### Mermaid

```bash
python -m replay graph mermaid .replay/runs/agent4-demo.jsonl \
  --group-by run \
  --output out/graph.md
```

When `--output` is omitted, Mermaid is printed to stdout.

```bash
python -m replay graph mermaid .replay/runs/agent4-demo.jsonl
```

### HTML explorer

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --output out/graph.html
```

The default HTML exporter is standalone and works offline. It supports:

- search
- filters
- focus views
- timeline navigation
- node and edge inspection
- evidence views
- base/fork diff highlighting

### React/XYFlow renderer

The optional XYFlow renderer uses bundled assets built from `viewer/`.

```bash
npm install
npm run build:xyflow-viewer

python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --renderer xyflow \
  --output out/graph-xyflow.html
```

Use `--asset-mode inline` for a single standalone HTML file, or `--asset-mode vendored` to write adjacent local CSS/JS assets.

## Compare base and fork

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

## Focus before export

Focus lets you export a smaller graph around one node.

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --focus rec_000003 \
  --direction both \
  --max-depth 2 \
  --output out/focused.html
```

Options:

| Option | Values | Meaning |
|---|---|---|
| `--focus` | node id | Keep nodes around this node. |
| `--direction` | `upstream`, `downstream`, `both` | Which direction to include. |
| `--max-depth` | integer | Maximum graph distance from focus. |
| `--group-by` | `none`, `path`, `span`, `run` | Group nodes in Mermaid / graph views. |
| `--title` | text | Override page title. |
| `--fork` | path | Add a fork trace; repeatable. |

## Suggested release screenshot

For a polished GitHub landing page, generate a screenshot from `out/agent4-demo-compare.html` and save it as:

```text
docs/assets/replay-graph.png
```

Then add this to `README.md` near the top:

```markdown
![Replay graph explorer](docs/assets/replay-graph.png)
```

The current README uses Mermaid instead of an image so it will not show a broken asset before the screenshot exists.

## Current limits

- The HTML explorer is read-only; it does not execute replay or fork actions.
- Very large traces may need filtering or focus export before visualization.
- The static renderer and XYFlow renderer have different interaction styles.
- Visualization quality depends on how much semantic and tool provenance the run captured.
