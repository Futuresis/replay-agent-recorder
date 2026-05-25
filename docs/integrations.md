# Integrations

Replay has two integration modes:

1. Use the public API or CLI directly in your own agent.
2. Generate or use a wrapper for an existing agent project checked out locally.

The wrappers in `integrations/` are not vendored copies of third-party agents. They are small runners that install Replay, prepare target entrypoints, and call into a target project you provide with `--target-root`.

## Recommended path for a new project

Start with direct API integration when possible:

```python
import replay

replay.install(project_root=".")

with replay.record("run-A", log_dir=".replay/runs"):
    await main()
```

Then add tool adapters where your agent actually executes tools:

```python
result = await replay.invoke_tool(
    "search",
    {"query": query},
    lambda: search({"query": query}),
    namespace="local",
    version="v1",
)
```

Use a generated wrapper when the target project is a separate checkout or when you want a standard CLI around a script, module, runnable, factory, LangGraph JSON, or ASGI app.

## Generate a wrapper

```bash
python -m replay scaffold integration \
  --name my-agent \
  --output-dir integrations \
  --tool-style method \
  --framework auto
```

Common `--tool-style` values:

| Style | Use when |
|---|---|
| `none` | The target only needs LLM patching or framework patching. |
| `mapping` | Tools live in a dictionary or registry. |
| `method` | Tools are executed through a client method such as `client.call_tool(name, args)`. |
| `class-method` | Tools are executed by patching a class method. |

Common `--framework` values:

| Mode | Meaning |
|---|---|
| `auto` | Generated runner chooses best-effort framework behavior. |
| `none` | Do not install framework-specific patches. |
| `langchain` | Install LangChain-oriented patches. |
| `langgraph` | Install LangGraph-oriented patches. |
| `both` | Install both LangChain and LangGraph patches. |

## Target entry types

Generated wrappers can invoke several entry styles.

| Entry | Example |
|---|---|
| Script | `--entry script:src/main.py -- --task hello` |
| Module | `--entry module:my_agent.cli -- --task hello` |
| Import | `--entry my_agent.app:main` |
| Runnable | `--entry my_agent.graph:agent --input-json '{"messages":[...]}'` |
| Factory | `--entry factory:my_agent.graph:build_agent --input-json '{"messages":[...]}'` |
| LangGraph JSON | `--entry 'langgraph.json#agent' --input-json '{"messages":[...]}'` |
| ASGI | `--entry asgi:my_agent.web:app --serve --port 8000` |

## Run a wrapped agent

Record mode:

```bash
python integrations/my_agent/runner.py \
  --replay-mode record \
  --run-id my-agent-demo \
  --replay-log-dir .replay/runs \
  --target-root /path/to/target-agent \
  --entry script:src/main.py \
  -- --task hello
```

Replay mode:

```bash
python integrations/my_agent/runner.py \
  --replay-mode replay \
  --base-run my-agent-demo \
  --replay-log-dir .replay/runs \
  --target-root /path/to/target-agent \
  --entry script:src/main.py \
  -- --task hello
```

Fork mode:

```bash
python integrations/my_agent/runner.py \
  --replay-mode replay \
  --base-run my-agent-demo \
  --replay-log-dir .replay/runs \
  --breakpoint-record-uid rec_000003 \
  --override-output "new assistant text" \
  --fork-run my-agent-demo-fork \
  --target-root /path/to/target-agent \
  --entry script:src/main.py \
  -- --task hello
```

## Built-in wrappers

| Directory | Status | Notes |
|---|---|---|
| `integrations/my_agent` | template | Use this as the clean starting point for a custom wrapper. |
| `integrations/deepagents` | experimental | Wrapper for an existing DeepAgents checkout. Explicit `--entry` is recommended. |
| `integrations/open_deep_research` | experimental | Wrapper for an existing Open Deep Research checkout. |
| `integrations/open_swe` | experimental | Wrapper for an existing Open SWE checkout. |
| `integrations/swe_agent` | experimental | Wrapper for SWE-agent-style checkouts. |
| `integrations/local_deep_researcher` | local experimental | Keep only if this wrapper is intentionally supported; remove it if it was a private experiment. |

Treat built-in wrappers as best-effort until each is validated against pinned upstream versions. Prefer explicit `--entry` values over auto-detected candidates.

## What to edit

Generated integrations usually contain:

```text
integrations/my_agent/
  README.md
  runner.py
  tool_adapter.py
  replay_target.json
```

Edit `tool_adapter.py` when the target has custom non-LangChain tool boundaries.

Avoid editing `runner.py` for normal script/module/runnable/factory/ASGI targets. The runner should keep standard Replay flags consistent across integrations.

## Common mistakes

| Mistake | Fix |
|---|---|
| Only patching LLM calls but not tools | Install a tool adapter at the actual tool execution boundary. |
| Recording unstable arguments | Remove timestamps, random ids, clients, sessions, file handles, and SDK objects from tool arguments. |
| Trusting auto-detected entries blindly | Pass `--entry` explicitly and keep a small curated list in the integration README. |
| Using invalid Python module paths | If a path contains `-` or another non-importable segment, use `script:` instead of dotted module import. |
| Letting real traces leak | Write traces under `.replay/runs` and keep that directory ignored. |

## References

- [Integration Scaffold Guide](integration-scaffold.md)
- [Tool Adapter Protocol](tool-adapter-protocol.md)
- [Quickstart](quickstart.md)


## README policy for integrations

Each integration directory should keep its README short and decision-oriented:

- current support status;
- one recommended record/replay command shape;
- where to put target-specific tool adapter code;
- validation checklist;
- link back to this guide.

Do not paste long raw auto-detection tables into integration READMEs. Keep full detection output in `replay_target.json` and document only validated, useful entrypoints.
