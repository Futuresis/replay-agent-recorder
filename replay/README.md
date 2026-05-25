# `replay/` package internals

This directory contains the Python package implementation for Replay Agent Recorder.

User-facing docs start here:

- [`../README.md`](../README.md)
- [`../docs/quickstart.md`](../docs/quickstart.md)
- [`../docs/concepts.md`](../docs/concepts.md)
- [`../docs/visualization.md`](../docs/visualization.md)
- [`../docs/integrations.md`](../docs/integrations.md)

Maintainer-facing visualization notes live in [`../docs/architecture/visualization-implementation-status.md`](../docs/architecture/visualization-implementation-status.md).

## High-level module map

| Area | Typical modules | Purpose |
|---|---|---|
| Public API | `api.py`, `__init__.py` | User-facing install, record, replay, fork, and helper APIs. |
| Runtime context | `context.py`, `storage.py`, `records.py` | Session state, JSONL trace storage, record schemas, and matching. |
| LLM patching | `openai_patch.py` and related patch helpers | Capture and replay OpenAI-compatible chat completion calls. |
| Tool recording | `tools.py`, adapter modules | Record local tool calls through `invoke_tool`, mapping adapters, and method adapters. |
| Filesystem capture | sandbox / filesystem helpers | Capture text-file effects inside explicit sandbox directories. |
| Semantic provenance | `semantic_runtime.py`, AST instrumentation modules | Optional data/control provenance between prompts, calls, branches, and outputs. |
| CLI | `cli.py`, `entrypoints.py` | `replay record`, `replay replay`, `replay fork`, graph exports, and scaffold commands. |
| Visualization | `graph_ir.py`, `visualize.py`, `xyflow_assets/` | Build Graph IR and export summary, Mermaid, SVG HTML, or XYFlow HTML. |
| Tests | `tests/` | Package-level regression and smoke tests. |

## Development notes

- Keep public imports stable through `replay/__init__.py` where possible.
- Prefer writing traces to `.replay/runs` in examples and docs, even if some legacy paths use `replay/runs`.
- Do not add new user-facing guides in this package directory. Use `../docs/` instead.
- If you change `viewer/`, rebuild bundled assets with `npm run build:xyflow-viewer` and verify notices in `../THIRD_PARTY_NOTICES.md`.
