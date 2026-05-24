# Replay Integration Scaffold Guide

This guide is for users who have a third-party Python agent and want to connect
it to Replay without copying the demo agents.

The scaffold standardizes the wrapper code around an agent. It does not try to
describe an agent with YAML. You still write a small Python adapter for the
agent-specific tool boundary.

## Mental Model

Generated integrations have two important files:

- `runner.py`: the standard wrapper entry point. In normal integrations, do not
  edit this file.
- `tool_adapter.py`: the agent-specific tool wiring. Only edit this when the
  target has a custom non-LangChain/non-LangGraph tool boundary.

`runner.py` owns common Replay behavior:

- standard record/replay CLI flags;
- `replay.install(...)`;
- `record`, `replay`, and `none` session selection;
- `sys.path` and `sys.argv` setup for the target agent;
- generic target-entry loading for `script`, `module`, `import`, `runnable`,
  `factory`, `langgraph.json`, and `asgi` entries;
- adapter install and uninstall order;
- JSON override option loading and validation.

`tool_adapter.py` owns only the target agent's tool boundary:

- where the tool registry or client lives;
- which method actually executes tools;
- how to extract stable tool names;
- how to convert tool arguments into a JSON-like `dict`;
- whether only some tool names should be recorded;
- namespace and version naming.

## Generate A Wrapper

Run this from the Replay repository root:

```bash
python -m replay scaffold integration --name my-agent --tool-style method
```

For agents built on LangChain or LangGraph, enable those adapters at generation
time so the generated `runner.py` installs them automatically:

```bash
python -m replay scaffold integration \
  --name open-deep-research \
  --tool-style none \
  --framework both
```

This creates:

```text
integrations/my_agent/
  __init__.py
  runner.py
  tool_adapter.py
  README.md
```

Pick `--tool-style` based on the third-party agent's tool execution shape:

| Tool shape in the target agent | Scaffold style |
| --- | --- |
| No local tools, only LLM calls | `none` |
| Mutable registry like `{"search": search_fn}` | `mapping` |
| Existing client instance like `client.call_tool(name, arguments)` | `method` |
| Framework creates client instances internally, so you must patch a class method | `class-method` |

Use `--framework langchain` when the target agent calls LangChain chat models or
`BaseTool` entry points. Use `--framework langgraph` when the target agent is a
LangGraph `StateGraph`/compiled graph and you want LangGraph node spans in the
trace. Use `--framework both` for projects that use both layers. This only
affects the generated wrapper; it does not create entries in `tool_adapter.py`.

If you are unsure, search the target agent for the real tool execution point:

```bash
rg "call_tool|tool_call|execute_tool|run_tool|tools\[|tool_calls"
```

Do not wrap the place where the LLM returns `tool_calls`. Wrap the place where
the agent executes the local tool, SDK call, HTTP request, or workspace action.

## Fill In `tool_adapter.py`

The generated file exposes one function:

```python
def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    ...
```

Return the adapters that should be installed before the target agent starts.

### No Tools

If the agent only makes OpenAI-compatible LLM calls, use `--tool-style none`.
The generated `tool_adapter.py` can stay as:

```python
def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    return []
```

### Mapping Registry

Use this when the target agent has a mutable dict of tool functions:

```python
def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    from target_agent.tools import TOOL_REGISTRY

    return [
        replay.MappingToolAdapter(
            TOOL_REGISTRY,
            namespace="my-agent",
            version="v1",
        )
    ]
```

Each registered tool should accept one JSON-like `dict` argument.

### Method Client

Use this when you can import or construct the concrete client instance before
the target runs:

```python
def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    from target_agent.tools import tool_client

    return [
        replay.MethodToolAdapter(
            tool_client,
            "call_tool",
            namespace="my-agent",
            version="v1",
        )
    ]
```

The default method shape is `call_tool(name, arguments)`.

### Class Method Client

Use this when the target framework creates tool client instances internally:

```python
def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    from target_agent.tools import ToolClient

    return [
        replay.ClassMethodToolAdapter(
            ToolClient,
            "call_tool",
            namespace="my-agent",
            version="v1",
        )
    ]
```

If the method uses keyword arguments instead of an `arguments` dict:

```python
def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    from target_agent.tools import ToolClient

    def arguments_factory(call_args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(kwargs)

    return [
        replay.ClassMethodToolAdapter(
            ToolClient,
            "call",
            namespace="my-agent",
            version="v1",
            arguments_factory=arguments_factory,
            tool_filter={"google_search"},
        )
    ]
```

`tool_filter` is optional. Use it when the patched method handles many actions
but only some are Replay tools.

## Entry Types

Use `--entry` to choose how `runner.py` launches the target. In normal use you
do not edit `runner.py`; you either pass `--entry` explicitly or let generated
`replay_target.json` provide the default.

| Entry type | Example | Notes |
| --- | --- | --- |
| Script | `script:src/main.py` | Equivalent to `runpy.run_path(...)` |
| Module | `module:my_agent.cli` | Equivalent to `python -m my_agent.cli` |
| Import / Runnable | `my_agent.graph:agent` | Imports a symbol and auto-selects `invoke` / `ainvoke` / `stream` / `astream` |
| Factory | `factory:my_agent.graph:build_agent` | Calls a factory, then runs the returned runnable |
| LangGraph JSON | `langgraph.json#agent` | Loads `graphs[...]` from `langgraph.json` |
| ASGI | `asgi:agent.webapp:app` | Advanced / experimental server mode |

Generated scaffold wrappers may also include `replay_target.json`. That file
stores the detected default entry, framework mode, and related hints. You can
override it at runtime with `--entry`, `--entry-kind`, `--graph`, `--method`,
`--target-root`, or `--target-cwd`.

## Run The Wrapped Agent

Record a run:

```bash
python integrations/my_agent/runner.py \
  --replay-mode record \
  --run-id my-agent-demo \
  --target-root /path/to/third-party-agent \
  --entry script:path/to/main.py \
  -- --task "analyze this project"
```

Replay it:

```bash
python integrations/my_agent/runner.py \
  --replay-mode replay \
  --base-run my-agent-demo \
  --target-root /path/to/third-party-agent \
  --entry script:path/to/main.py \
  -- --task "analyze this project"
```

Run a module entry:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry module:my_agent.cli \
  -- --task "analyze this project"
```

Run a runnable import:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry my_agent.graph:agent \
  --input-json '{"messages":[{"role":"user","content":"hello"}]}'
```

Run a factory entry:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry factory:my_agent.graph:build_agent \
  --factory-config-json '{"configurable":{"thread_id":"t1"}}' \
  --input-json '{"messages":[{"role":"user","content":"hello"}]}'
```

Run a LangGraph graph from `langgraph.json`:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry 'langgraph.json#Deep Researcher' \
  --input-json '{"messages":[{"role":"user","content":"hello"}]}'
```

Everything after the final `--` is passed to the target agent. Everything before
that belongs to the Replay wrapper.

Fork from an LLM breakpoint:

```bash
python integrations/my_agent/runner.py \
  --replay-mode replay \
  --base-run my-agent-demo \
  --breakpoint-record-uid rec_000003 \
  --override-output "replacement assistant content" \
  --fork-run my-agent-demo-fork \
  --target-root /path/to/third-party-agent \
  --entry script:path/to/main.py \
  -- --task "analyze this project"
```

Run the target without record/replay while keeping the same wrapper:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry script:path/to/main.py \
  -- --task "analyze this project"
```

Use generated defaults from `replay_target.json` and override them temporarily:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --input-json '{"messages":[{"role":"user","content":"hello"}]}'

python integrations/my_agent/runner.py \
  --replay-mode none \
  --entry my_agent.graph:agent \
  --input-json '{"messages":[{"role":"user","content":"hello"}]}'
```

## ASGI / Server Mode

ASGI mode is advanced / experimental. It is useful when the target agent is
already exposed as an ASGI app or when `langgraph.json#http` points at one.

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry asgi:agent.webapp:app \
  --serve \
  --host 127.0.0.1 \
  --port 8000

python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry 'langgraph.json#http' \
  --serve
```

Each HTTP request runs inside its own Replay session. If your deployment has a
stable request ID header, pass it so request traces are easier to correlate:

```bash
python integrations/my_agent/runner.py \
  --replay-mode none \
  --target-root /path/to/third-party-agent \
  --entry 'langgraph.json#http' \
  --serve \
  --request-header-run-id x-request-id
```

For commands that start the app outside `runner.py` (for example `langgraph dev`
or another framework-owned server command), use the bootstrap hook instead of
editing the target application:

```bash
PYTHONPATH=/path/to/replay_repo:$PYTHONPATH \
REPLAY_AUTOINSTALL=1 \
REPLAY_FRAMEWORK=both \
REPLAY_AUTO_SESSION=1 \
REPLAY_MODE=record \
python -c "from replay.bootstrap import install_from_env; install_from_env(); ..."
```

Supported bootstrap environment variables include:

- `REPLAY_AUTOINSTALL=1`
- `REPLAY_FRAMEWORK=auto|none|langchain|langgraph|both`
- `REPLAY_AUTO_SESSION=1`
- `REPLAY_MODE=record|replay|none`
- `REPLAY_RUN_ID_TEMPLATE=...`
- `REPLAY_RUN_ID=...`
  Used as a fixed fallback template when `REPLAY_RUN_ID_TEMPLATE` is unset.
- `REPLAY_BASE_RUN=...`
- `REPLAY_PROJECT_ROOT=...`
- `REPLAY_LOG_DIR=...`

Replay does not currently scaffold a `sitecustomize.py` file automatically. If
you want `sitecustomize`-style bootstrap behavior, create that file yourself and
call `from replay.bootstrap import install_from_env; install_from_env()`.

## When To Edit `runner.py`

Do not edit `runner.py` for normal script, module, import, runnable, factory,
LangGraph JSON, or ASGI targets.

Edit `runner.py` only when the target cannot be launched by `runpy.run_path`,
for example:

- startup must set environment variables or prepare files;
- the integration should hard-code a specific target root or script;
- the target needs extra validation before launch.

Even then, keep Replay's standard flags from `add_replay_arguments(...)` and
keep session selection through `replay_session(...)`.

## Common Mistakes

- Do not reimplement the standard Replay CLI flags in each integration.
- Do not wrap OpenAI `tool_calls` output; wrap the actual tool execution point.
- Do not execute the live tool before passing the callback to Replay.
- Tool arguments and tool results must be JSON-like values.
- Use stable tool names, namespaces, and versions. Changing them breaks trace
  matching.
- Always uninstall custom adapters in reverse install order if you customize the
  runner.

## Quick Checklist

Before considering an integration done:

- `python -m replay scaffold integration ...` created the wrapper directory.
- `tool_adapter.py` returns the right adapters from `build_adapters(...)`.
- `runner.py --replay-mode record ...` writes a trace.
- `runner.py --replay-mode replay ...` replays the same run without live tool
  execution for recorded tools.
- A breakpoint fork with `--override-output` works for at least one LLM record.
