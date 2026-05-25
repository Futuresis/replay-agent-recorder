# Limitations and roadmap

Replay Agent Recorder is useful today for local debugging and deterministic demos, but it is still alpha. This document separates current limits from good future work.

## Current limitations

| Area | Current state |
|---|---|
| LLM provider patching | Direct patching focuses on OpenAI SDK `chat.completions.create`. |
| Streaming | Streaming responses are not supported yet. |
| Tools | Tools are recorded only when routed through Replay's tool protocol or an adapter. |
| Serialization | Tool inputs and outputs must be JSON-like and serializable. |
| Filesystem | File capture supports ordinary text files inside an explicit sandbox. |
| Breakpoints | Breakpoints currently target LLM records. |
| Override input | `override_input` performs a shallow kwargs merge. |
| Visualization | Static HTML is read-only and does not execute replay/fork actions. |
| Direct HTTP clients | Non-OpenAI SDK clients need custom adapters or patch layers. |
| Distributed systems | This is not yet a distributed production tracing system. |

## When not to use Replay yet

Replay may not be the right tool if you need:

- a hosted observability SaaS dashboard
- production-grade distributed tracing across many services
- streaming-first model capture
- automatic capture of every possible SDK, browser, database, or HTTP client
- guaranteed redaction of sensitive data without manual review
- binary filesystem snapshots or arbitrary filesystem rollback

## Recommended near-term improvements

These are high-impact improvements for public open-source polish:

1. Move the default trace directory from the package path to `.replay/runs` or another project-local cache path.
2. Validate `run_id` to prevent path traversal and invalid filenames.
3. Add subprocess timeouts to tests that spawn CLI commands.
4. Add `pytest-timeout`, `ruff`, and format checks to CI.
5. Keep `THIRD_PARTY_NOTICES.md` updated for bundled viewer assets.
6. Keep integration README files curated so they show recommended entries instead of long raw auto-detection dumps.
7. Make viewer UI default to English or add i18n.
8. Add a trace redaction CLI.
9. Add screenshots or GIFs under `docs/assets/`.
10. Add release automation for PyPI and GitHub Releases.

## Longer-term roadmap ideas

- Additional model provider adapters.
- Streaming capture and replay.
- Richer fork workflows and fork diff UX.
- Redaction and policy-based trace sanitization.
- Larger example gallery.
- GitHub Pages documentation site.
- More framework-native integrations.
- OpenTelemetry bridge or export format.
- Stable public trace schema versioning.
