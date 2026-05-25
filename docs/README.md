# Documentation

Start here if you are browsing the project after the landing page.

| Document | Purpose |
|---|---|
| [Quickstart](quickstart.md) | Run the deterministic demo, replay it, fork it, and export a graph. |
| [快速开始](quickstart.zh-CN.md) | 中文快速开始。 |
| [Concepts and architecture](concepts.md) | Learn the core terms and mental model. |
| [核心概念和架构](concepts.zh-CN.md) | 中文概念说明。 |
| [Visualization](visualization.md) | Export summary, Graph IR, Mermaid, and HTML graphs. |
| [可视化](visualization.zh-CN.md) | 中文可视化说明。 |
| [Integrations](integrations.md) | Connect Replay to existing agent projects. |
| [Integration scaffold](integration-scaffold.md) | Generate and maintain wrapper integrations. |
| [Tool adapter protocol](tool-adapter-protocol.md) | Record local tool calls correctly. |
| [Security and privacy](security-and-privacy.md) | Understand what traces may contain and how to keep them safe. |
| [Limitations and roadmap](limitations.md) | Current limits and high-impact future improvements. |
| [Original README details](original-readme-details.md) | Preserved lower-level notes from the earlier README. |
| [Architecture notes](architecture/) | Maintainer-facing implementation status and internal contracts. |

## Directory policy

Public user documentation should live in `docs/`. The previous `guidance/` directory has been folded into:

- `docs/visualization.md` and `docs/visualization.zh-CN.md` for user-facing visualization usage.
- `docs/architecture/visualization-implementation-status.md` for maintainer-facing implementation notes.

Do not keep a separate `guidance/` directory in the final public documentation tree unless you intentionally want a private planning area.
