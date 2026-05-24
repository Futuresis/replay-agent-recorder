# Replay

Replay is a small Python framework for recording, replaying, and forking
LLM-agent runs. It currently focuses on OpenAI-compatible
`chat.completions.create` calls, local tool-call boundaries, async branching,
AST-level provenance, sandboxed text-file effects, and breakpoint-based replay
forks.

## What This Repository Contains

- `replay/`: the replay framework package.
- `replay/openai_patch.py`: monkey-patches OpenAI SDK chat completions.
- `replay/asyncio_patch.py`: tracks async branches created by `asyncio.gather`,
  `asyncio.create_task`, and `asyncio.TaskGroup.create_task`.
- `replay/context.py`: record/replay sessions, path allocation, breakpoint logic,
  and JSONL record writing.
- `replay/semantic_runtime.py`, `replay/instrument.py`, and
  `replay/import_hook.py`: optional AST provenance tracking for call-to-call
  causality.
- `replay/edges.py`: sidecar edge records and orchestration graph helpers.
- `replay/graph_ir.py` and `replay/visualize.py`: Graph IR construction plus
  summary, JSON, Mermaid, and offline HTML visualization exports.
- `replay/tools.py`: the unified tool-call protocol: `name + arguments + invoke`.
- `replay/tool_adapters.py`: adapter helpers for custom tool systems.
- `replay/filesystem_effects.py` and `replay/sandbox_manager.py`: text-file
  effect capture and managed sandbox reset helpers.
- `replay/tests/`: smoke tests for LLM, tool, filesystem, and AST provenance
  behavior.
- `test_agent/agent4`: deterministic comprehensive integration-style replay
  agent with fake-LLM support.
- `replay/runs*`: runtime JSONL run output, ignored by default.

## Development Environment

Use Python 3.12 or newer and Node.js 20 or newer for the repository's standard
development flow.

Install `uv` first by following the official
[`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/).

Recommended first-time setup from the repository root:

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

Use `npm install` to fetch the viewer dependencies from `package.json`. They
support the default visualization workflow, including rebuilding the vendored
XYFlow viewer assets when needed:

```bash
npm run build:xyflow-viewer
```

If you want to run a real LLM endpoint, copy `.env.example` to `.env` and fill
in:

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

The code still accepts legacy variable names such as `API_KEY` and `BASE_URL`,
but the documentation follows `.env.example` as the canonical format.

## Implemented Capabilities

- Record and replay OpenAI-compatible `chat.completions.create` calls, both sync
  and async.
- Match replay records by normalized semantic input, with `path_id`
  disambiguation for concurrent branches.
- Track branches created through `asyncio.gather`, `asyncio.create_task`, and
  `asyncio.TaskGroup.create_task`.
- Record and replay local tools routed through `invoke_tool`,
  `invoke_tool_sync`, `MappingToolAdapter`, or `MethodToolAdapter`.
- Replay recorded tool exceptions as `ReplayedToolError`.
- Capture and replay text-file create/modify/delete effects inside an explicit
  sandbox.
- During a fork, if a live sandboxed tool changes a captured filesystem root,
  later tools using that same root run live instead of applying stale recorded
  file effects.
- Fork replay from an LLM breakpoint by overriding the matched LLM output.
- Fork replay from an LLM breakpoint by patching the matched assistant message,
  including `content`, `tool_calls`, and `finish_reason`.
- Fork replay from an LLM breakpoint by overriding OpenAI call kwargs, such as
  `messages`, and then executing that call live.
- Track AST-level data/control provenance between LLM and tool calls and write
  `kind="edge"` sidecar records for orchestration graphs.
- Propagate provenance through assignments, calls, attributes/subscripts,
  arithmetic/string formatting, branches, loops, boolean short-circuit
  expressions, conditional expressions, and common comprehensions.

## Core Concepts

### LLM Calls

Call `replay.install()` once near process startup. It patches OpenAI SDK
`Completions.create` and `AsyncCompletions.create`, so existing code like this is
intercepted without changing each call site:

```python
response = await client.chat.completions.create(...)
```

Inside `replay.record(...)`, live responses are written to JSONL. Inside
`replay.replay(...)`, matching responses are read from previous JSONL records.

By default, `replay.install()` also installs an AST import hook for the current
working directory. It tracks when upstream LLM/tool outputs are used in downstream
inputs or branch conditions, then records `data` and `control` edges in the same
JSONL file:

```python
replay.install(project_root="path/to/your/agent")

with replay.record("run-A"):
    await main()
```

Use `replay.install(semantic=False)` to keep the original record/replay behavior
without AST instrumentation. `replay.replay(..., semantic_fallback=True)` can
fall back to a recorded callsite fingerprint when exact `input_id` matching
misses, but exact input matching still wins first.

Use `project_root`, `include`, and `exclude` to keep instrumentation focused on
agent code:

```python
replay.install(
    project_root=".",
    include=("test_agent/agent4/*.py",),
    exclude=("**/outputs/*",),
)
```

When a forked LLM or tool result flows into a later prompt, tool argument, or
branch condition, replay marks that downstream branch dirty and runs the affected
calls live into the fork run. The recorded `edge` entries can also be loaded with
`replay.edges.build_orchestration_graph(...)` to inspect call-to-call causality.

### Visualization

Replay can turn existing JSONL traces into graph outputs:

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl
python -m replay graph export-ir replay/runs/agent4-demo.jsonl --output out/graph.json
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by run --output out/graph.md
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/graph.html
```

The graph CLI shape is
`python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...]`.
Shared options include `--fork`, `--focus`, `--direction`, `--max-depth`,
`--title`, `--output`, and `--group-by`. The HTML exporter also accepts
`--asset-mode inline|vendored` and `--renderer svg|xyflow`.

The HTML output is an offline interactive explorer with search, result lists,
filters, focus, node/edge selection, evidence inspection, copyable CLI snippets,
timeline navigation, and collapse-by-run/path/span controls. When a base trace is
loaded with `--fork`, the same Graph IR also carries base/fork diff metadata:
fork boundary highlighting, node status (`changed`, `unchanged`, `new`,
`missing`, or `baseline`), downstream filtering, and Inspector previews for
input, output, and provenance differences. It does not execute replay actions
from the browser; server-backed workbench actions are a later phase. See
`guidance/visualization/quickstart.md` for the full command reference.

### Tool Calls

Tool replay is intentionally not tied to one agent framework. The framework has
one tool-call protocol:

```text
tool name + JSON-like arguments + live invoke function
```

Use it directly:

```python
result = await replay.invoke_tool(
    "search",
    {"query": "hello"},
    lambda: search({"query": "hello"}),
)
```

Or for synchronous tools:

```python
result = replay.invoke_tool_sync(
    "calculator",
    {"expression": "1 + 1"},
    lambda: calculator({"expression": "1 + 1"}),
)
```

Adapters in `replay.tool_adapters` turn common tool organization styles into this
protocol:

```python
adapter = replay.MappingToolAdapter(tool_registry, namespace="local")
adapter.install()

method_adapter = replay.MethodToolAdapter(client, "call_tool", namespace="mcp")
method_adapter.install()
```

`MappingToolAdapter` expects a mutable mapping like `{"search": search_fn}` where
each function accepts one JSON-like argument mapping. `MethodToolAdapter` expects
an object method shaped like `call_tool(name, arguments)`.
`ClassMethodToolAdapter` patches a class method before framework-owned tool
client instances are created, and can filter which tool names are recorded.

For new wrapper scripts, generate a standard integration skeleton:

```bash
python -m replay scaffold integration --name my-agent --tool-style method
```

The generated runner uses `replay.integration` helpers for standard replay CLI
flags, JSON override loading, instrumentation install, and record/replay session
selection. See `docs/integration-scaffold.md` at the repository root for the
full scaffold usage guide.

### Filesystem Effects

Tools that modify local files can opt in to sandboxed filesystem capture. Replay
will record the JSON-like tool output plus create/modify/delete effects under the
configured root:

```python
capture = replay.FilesystemCapture("sandbox")

result = replay.invoke_tool_sync(
    "rewrite_config",
    {"mode": "prod"},
    lambda: rewrite_config(),
    fs_capture=capture,
)
```

On record, replay snapshots the sandbox before and after the tool call and stores
text-file effects under `effects.filesystem`. On replay, the live tool is not
executed: replay first verifies each file's recorded `before_sha256`, applies the
captured file changes, then returns the recorded tool output. If current files do
not match the recorded pre-state, `FilesystemReplayConflictError` is raised.

Forks are more conservative. After a fork has run a live tool that changes a
captured filesystem root, replay treats that root as dirty for the rest of the
session. Later tools using the same `FilesystemCapture` run live and are written
to the fork instead of applying historical file effects on top of changed state.
If an equivalent historical tool record exists, replay consumes it only to keep
record matching aligned.

The first version supports ordinary text files inside the sandbox, with create,
modify, and delete effects. It rejects symlinks, binary files, files larger than
`max_file_bytes`, and paths that escape the sandbox. Rename is represented as a
delete plus a create.

For full-run record/replay, use one sandbox directory as the agent's complete
mutable file boundary. Keep a stable base directory next to it, then let replay
reset the sandbox before each run:

```python
with replay.managed_sandbox(base_root="agent/sandbox_base", work_root="agent/sandbox") as capture:
    adapter = replay.MethodToolAdapter(client, "call_tool", fs_capture=capture)
    with replay.record("run-A"):
        await main()

with replay.managed_sandbox(base_root="agent/sandbox_base", work_root="agent/sandbox") as capture:
    adapter = replay.MethodToolAdapter(client, "call_tool", fs_capture=capture)
    with replay.replay(base_run="run-A"):
        await main()
```

`replay.managed_sandbox(...)` resets `work_root` to a copy of `base_root` on
entry and returns `FilesystemCapture(work_root)`. This keeps filesystem hash
checks meaningful: every record or replay starts from the same base state instead
of reusing a directory already modified by a prior run. Lower-level
`replay.sandbox(...)` is also available when callers need the prepared work path
instead of a capture object.

## What Gets Recorded

Records are JSONL objects with:

- `kind`: `"llm"` or `"tool"` for primary records. Provenance sidecars use
  `kind: "edge"`.
- `input_id`: SHA256 hash of normalized semantic input.
- `path_id`: execution path, for example `root/0`, `root.0/0`, `root/tool/0`.
- `input`: normalized LLM request or tool input.
- `output`: replayable response/output.
- `error`: tool error record when a tool failed.
- `effects`: optional captured filesystem effects for tool records.
- `callsite`: first frame outside the replay package.
- `metadata`: timestamp, latency, matching mode, usage, semantic fingerprint,
  provenance, and fork/input-override markers where available.

For LLM calls, `input_id` is based on normalized semantic OpenAI request fields
such as provider, API, model, messages, tools, temperature, top_p, and n. Runtime
fields such as request ids, trace ids, timestamps, retry ids, timeout, extra
headers/query, and idempotency keys are excluded.

For tool calls, `input_id` is based on:

```json
{
  "tool_name": "...",
  "arguments": {...},
  "namespace": "optional",
  "version": "optional"
}
```

Inputs and outputs must be JSON-like. Dataclasses, Pydantic-like objects,
Decimals, Paths, tuples, lists, sets, dicts, strings, ints, floats, booleans, and
`None` are normalized where supported. Unsupported tool values raise
`ToolSerializationError`.

Fork files start with a `fork_metadata` line containing the base run,
breakpoint record uid, creation time, and mode. Edge records have
`edge_kind: "data"` or `"control"` plus `from` and `to` source objects that
identify the connected primary records.

## Record And Replay

Minimal LLM recording:

```python
import replay

replay.install()

with replay.record("run-A"):
    await main()
```

Minimal replay:

```python
import replay

replay.install()

with replay.replay(base_run="run-A"):
    await main()
```

Breakpoint replay with an overridden LLM output:

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_output="new assistant content",
):
    await main()
```

When the breakpoint is reached, replay returns a synthetic response whose first
assistant message has the supplied content. The breakpoint branch is marked
dirty, not permanently live. Later calls are decided one by one: calls that
depend on fork-produced sources, or that no longer match a base record on the
dirty branch, execute live and are recorded to the fork run; unaffected calls can
still replay matching base records.

`override_output` is a shorthand for text-only experiments. It replaces the first
assistant message `content` and clears any original `tool_calls`.

Breakpoint replay with an overridden assistant message:

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_message={
        "content": "I will call the lookup tool with a narrower query.",
        "tool_calls": [
            {
                "id": "call_manual_001",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": "{\"query\":\"new query\",\"limit\":5}",
                },
            }
        ],
    },
):
    await main()
```

`override_message` patches the first assistant message in the matched raw
response. Fields not provided are preserved. If non-empty `tool_calls` are
provided and `finish_reason` is omitted, replay sets the first choice
`finish_reason` to `"tool_calls"`. You can also pass `finish_reason` explicitly
inside `override_message`.

Breakpoint replay with an overridden LLM input:

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_input={
        "messages": [{"role": "user", "content": "new prompt"}],
    },
):
    await main()
```

The input override is a shallow merge into the OpenAI chat completion kwargs for
the matched breakpoint call. The modified call is executed live, recorded into a
fork run, and the current branch is marked dirty after the breakpoint.

`override_output`, `override_message`, and `override_input` are mutually
exclusive. If none is provided, the matched breakpoint LLM call executes live and
marks the branch dirty.

Only LLM records can be used as breakpoints. Tool records are replayed, but they
are not valid breakpoint targets.

Replay forks are written next to the base run. If `fork_run` is omitted, replay
allocates names like:

```text
run-A_fork_001.jsonl
run-A_fork_002.jsonl
```

You can choose the fork name explicitly:

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_input={"messages": [{"role": "user", "content": "new prompt"}]},
    fork_run="run-A_prompt_experiment",
):
    await main()
```

## Async Branches And Paths

`replay.install()` also patches common asyncio branch entry points. This lets the
framework assign stable paths in concurrent runs:

```text
root.0/0
root.1/0
root.2/0
```

Tool calls use a separate local counter:

```text
root/tool/0
root.0/tool/0
```

Replay matching prefers `input_id`. If multiple unconsumed records have the same
`input_id`, `path_id` is used to disambiguate.

This means a strict replay intentionally fails with `ReplayMissError` if current
code reaches a call whose semantic input differs from the historical run. For
experiments, use an LLM breakpoint plus `override_output`, `override_message`,
or `override_input`; the branch is marked dirty and fork-affected or unmatched
downstream calls execute live and are recorded to the fork, instead of trying to
reuse now-stale downstream records.

With semantic instrumentation enabled, replay also checks data/control
provenance. If a downstream call depends on a fork-produced source, that call
runs live even when an old record with the same callsite or path exists. This is
what keeps dependent prompts, tool arguments, and branch-controlled calls from
silently mixing old and forked state.

## Demo Agents

### Agent4: comprehensive deterministic replay workflow

Agent4 uses a local fake LLM by default, so it can exercise the framework without
network access or API credits. It combines concurrent LLM calls, duplicate-input
disambiguation, direct sync/async tools, `MappingToolAdapter`,
`MethodToolAdapter`, expected tool errors, sandbox file effects, and breakpoint
forks.

Record:

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
```

Replay:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

Fork with an output override:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-output "manual seed override" --fork-run agent4-demo-fork --output test_agent/agent4/outputs/fork.md
```

Use `--real-llm` when you want Agent4 to call the endpoint configured in `.env`
instead of its fake LLM.

## Inspecting Runs

Runs are JSONL files under the selected `log_dir`, defaulting to `replay/runs`.
Each line is one record. To choose a breakpoint, inspect LLM records and use
their `record_uid`:

```bash
python - <<'PY'
import json
from pathlib import Path

for line in Path("replay/runs/run-A.jsonl").read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    if record.get("kind") == "llm":
        print(record["record_uid"], record["path_id"], record["output"].get("content", "")[:80])
PY
```

Manual JSONL edits are possible but fragile. Replaying an edited output works
only while downstream semantic inputs still match the stored `input_id`s. If an
edited LLM output changes a later prompt or tool argument, strict replay will
usually fail with `ReplayMissError`. Prefer breakpoint forks for controlled
experiments.

## Tests

Run the LLM smoke test:

```bash
python -m replay.tests.smoke_test
```

Run the tool replay test:

```bash
python -m replay.tests.tool_test
```

Run the AST provenance test:

```bash
python -m replay.tests.ast_provenance_test
```

The smoke and tool tests write temporary logs under `replay/tmp-runs-*` and
remove them at exit. The AST provenance test builds temporary modules to verify
data/control edges, semantic fallback, expression control flow, and fork
propagation.

## Current Limitations

- Only OpenAI SDK `chat.completions.create` is patched.
- `stream=True` is not supported.
- Tool calls are recorded only when routed through `invoke_tool`,
  `invoke_tool_sync`, or an adapter.
- Tool arguments and outputs must be JSON-like and serializable by replay.
- Filesystem effect capture supports text files in an explicit sandbox only.
- Breakpoints currently target LLM records only.
- `override_input` performs a shallow kwargs merge; it does not deep-merge
  nested fields such as individual messages or tool schemas.
- AST instrumentation applies only to imported Python files under the selected
  project root and currently avoids constructs that cannot be safely thunked,
  such as `await` inside boolean or conditional expressions.
- Direct HTTP calls or non-OpenAI SDKs need their own adapter/patch layer.

## Good Next Steps

- Add a LangChain-style adapter if needed.
- Consider a small CLI for inspecting JSONL records and choosing breakpoints.
- Broaden filesystem capture beyond ordinary text files if future agents need
  binary artifacts.

## 中文

Replay 是一个小型 Python 框架，用于记录、重放和分叉 LLM Agent 的运行过程。
当前重点覆盖 OpenAI 兼容的 `chat.completions.create` 调用、本地工具调用边界、
异步分支、AST 级 provenance、沙箱文本文件效果，以及基于断点的 replay fork。

## 本仓库包含什么

- `replay/`: Replay 框架包。
- `replay/openai_patch.py`: monkey patch OpenAI SDK 的 chat completions。
- `replay/asyncio_patch.py`: 跟踪 `asyncio.gather`、`asyncio.create_task` 和
  `asyncio.TaskGroup.create_task` 创建的异步分支。
- `replay/context.py`: record/replay session、路径分配、断点逻辑和 JSONL 记录写入。
- `replay/semantic_runtime.py`、`replay/instrument.py` 和
  `replay/import_hook.py`: 可选的 AST provenance 跟踪，用于记录调用之间的因果关系。
- `replay/edges.py`: sidecar edge 记录和 orchestration graph helper。
- `replay/graph_ir.py` 和 `replay/visualize.py`: Graph IR 构建，以及 summary、
  JSON、Mermaid 和离线 HTML 可视化导出。
- `replay/tools.py`: 统一工具调用协议：`name + arguments + invoke`。
- `replay/tool_adapters.py`: 为自定义工具系统提供适配器。
- `replay/filesystem_effects.py` 和 `replay/sandbox_manager.py`: 文本文件效果捕获
  和 managed sandbox 重置工具。
- `replay/tests/`: 覆盖 LLM、工具、文件系统和 AST provenance 行为的 smoke tests。
- `test_agent/agent4`: 带 fake LLM 的确定性综合 replay 集成测试 Agent。
- `replay/runs*`: 本地生成的 JSONL run 输出，默认不提交。

## 开发环境

仓库标准开发流程要求 Python 3.12+ 和 Node.js 20+。

安装 `uv` 时，请先参考官方安装文档：
[`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/)。

推荐在仓库根目录按以下顺序完成首次安装：

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

其中 `npm install` 用于安装 `package.json` 中声明的 viewer 依赖，服务于默认可视化链路。
如果需要重新构建 vendored XYFlow viewer 资源，可执行：

```bash
npm run build:xyflow-viewer
```

如果要连接真实 LLM endpoint，请把 `.env.example` 复制为 `.env` 并填写：

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

代码仍兼容历史变量名，例如 `API_KEY` 和 `BASE_URL`，但文档以 `.env.example` 为准。

## 已实现能力

- 记录和重放 OpenAI 兼容的 `chat.completions.create` 调用，支持同步和异步。
- 根据归一化后的语义输入匹配 replay 记录，并用 `path_id` 区分并发分支。
- 跟踪 `asyncio.gather`、`asyncio.create_task` 和
  `asyncio.TaskGroup.create_task` 创建的分支。
- 记录和重放通过 `invoke_tool`、`invoke_tool_sync`、`MappingToolAdapter` 或
  `MethodToolAdapter` 接入的本地工具。
- 将已记录的工具异常重放为 `ReplayedToolError`。
- 在显式沙箱中捕获和重放文本文件的 create、modify、delete 效果。
- 在 fork 中，如果 live sandbox 工具修改了被捕获的文件系统根目录，后续使用同一
  根目录的工具会 live 执行，而不是套用过期的文件效果记录。
- 可以通过覆盖匹配到的 LLM 输出，从 LLM 断点创建 replay fork。
- 可以通过 patch 匹配到的 assistant message 创建 replay fork，包括 `content`、
  `tool_calls` 和 `finish_reason`。
- 可以通过覆盖 OpenAI 调用 kwargs 创建 replay fork，例如覆盖 `messages`，并让
  该调用 live 执行。
- 跟踪 LLM 和工具调用之间的 AST 级数据/控制 provenance，并写入 `kind="edge"`
  的 sidecar 记录，用于 orchestration graph。
- provenance 能穿过赋值、函数调用、属性/下标、算术和字符串格式化、分支、循环、
  布尔短路表达式、条件表达式和常见 comprehension。

## 核心概念

### LLM 调用

在进程启动附近调用一次 `replay.install()`。它会 patch OpenAI SDK 的
`Completions.create` 和 `AsyncCompletions.create`，所以现有代码中的调用可以在
不修改每个 call site 的情况下被拦截：

```python
response = await client.chat.completions.create(...)
```

在 `replay.record(...)` 中，live response 会写入 JSONL。在
`replay.replay(...)` 中，匹配到的 response 会从已有 JSONL 记录中读取。

默认情况下，`replay.install()` 也会为当前工作目录安装 AST import hook。它会跟踪
上游 LLM/工具输出何时被下游输入或分支条件使用，并在同一个 JSONL 文件中记录
`data` 和 `control` 边：

```python
replay.install(project_root="path/to/your/agent")

with replay.record("run-A"):
    await main()
```

如果只想保留原始 record/replay 行为，可以用 `replay.install(semantic=False)` 关闭
AST instrumentation。`replay.replay(..., semantic_fallback=True)` 可以在精确
`input_id` 匹配失败时回退到已记录的 callsite fingerprint，但仍会优先使用精确输入匹配。

可以通过 `project_root`、`include` 和 `exclude` 控制 instrumentation 范围：

```python
replay.install(
    project_root=".",
    include=("test_agent/agent4/*.py",),
    exclude=("**/outputs/*",),
)
```

当 fork 后的 LLM 或工具结果流入后续 prompt、工具参数或分支条件时，Replay 会把该
下游分支标记为 dirty，并让受影响的调用 live 执行并写入 fork run。记录下来的
`edge` entry 也可以通过 `replay.edges.build_orchestration_graph(...)` 加载，用来查看
调用之间的因果关系。

### 可视化

Replay 可以把已有 JSONL trace 转为图输出：

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl
python -m replay graph export-ir replay/runs/agent4-demo.jsonl --output out/graph.json
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by run --output out/graph.md
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/graph.html
```

graph CLI 形态是
`python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...]`。
共享参数包括 `--fork`、`--focus`、`--direction`、`--max-depth`、`--title`、
`--output` 和 `--group-by`。HTML exporter 还支持
`--asset-mode inline|vendored` 和 `--renderer svg|xyflow`。

HTML 输出是一个离线交互式 explorer，支持搜索、结果列表、过滤、focus、节点/边选择、
evidence 查看、可复制 CLI 片段、时间线导航，以及按 run/path/span 折叠。加载 base
trace 时如果同时传入 `--fork`，同一个 Graph IR 也会携带 base/fork diff 元数据：
fork 边界高亮、节点状态（`changed`、`unchanged`、`new`、`missing` 或 `baseline`）、
下游过滤，以及 Inspector 中的输入、输出和 provenance 差异预览。浏览器内不会执行
replay 动作；需要服务端支持的 workbench 动作属于后续阶段。完整命令参考见
`guidance/visualization/quickstart.md`。

### 工具调用

工具 replay 不绑定某一个 Agent 框架。框架只要求一个统一工具调用协议：

```text
tool name + JSON-like arguments + live invoke function
```

可以直接使用：

```python
result = await replay.invoke_tool(
    "search",
    {"query": "hello"},
    lambda: search({"query": "hello"}),
)
```

同步工具可以这样使用：

```python
result = replay.invoke_tool_sync(
    "calculator",
    {"expression": "1 + 1"},
    lambda: calculator({"expression": "1 + 1"}),
)
```

`replay.tool_adapters` 中的适配器可以把常见工具组织方式接入该协议：

```python
adapter = replay.MappingToolAdapter(tool_registry, namespace="local")
adapter.install()

method_adapter = replay.MethodToolAdapter(client, "call_tool", namespace="mcp")
method_adapter.install()
```

`MappingToolAdapter` 需要一个可变 mapping，例如 `{"search": search_fn}`，其中每个
函数接收一个 JSON-like argument mapping。`MethodToolAdapter` 需要对象方法形如
`call_tool(name, arguments)`。

### 文件系统效果

会修改本地文件的工具可以选择开启沙箱文件系统捕获。Replay 会记录 JSON-like 工具输出，
以及配置根目录下的 create/modify/delete 效果：

```python
capture = replay.FilesystemCapture("sandbox")

result = replay.invoke_tool_sync(
    "rewrite_config",
    {"mode": "prod"},
    lambda: rewrite_config(),
    fs_capture=capture,
)
```

record 时，Replay 会在工具调用前后 snapshot sandbox，并把文本文件变化保存到
`effects.filesystem`。replay 时，live 工具不会执行：Replay 会先验证每个文件的
`before_sha256`，再应用捕获到的文件变化，最后返回记录中的工具输出。如果当前文件与
记录中的 pre-state 不匹配，会抛出 `FilesystemReplayConflictError`。

fork 会更保守。fork 中一旦某个 live 工具修改了被捕获的文件系统根目录，Replay 会在
剩余 session 中把该根目录视为 dirty。后续使用同一个 `FilesystemCapture` 的工具会
live 执行并写入 fork，而不是在已改变的状态上应用历史文件效果。如果存在等价的历史工具
记录，Replay 只会消费它来保持记录匹配对齐。

当前版本支持 sandbox 内普通文本文件的 create、modify、delete 效果。它会拒绝 symlink、
二进制文件、超过 `max_file_bytes` 的文件，以及逃出 sandbox 的路径。rename 会表示为
一次 delete 加一次 create。

对于完整的 record/replay 运行，建议使用一个 sandbox 目录作为 Agent 的全部可变文件边界。
把稳定的 base 目录放在旁边，并让 Replay 在每次运行前重置 sandbox：

```python
with replay.managed_sandbox(base_root="agent/sandbox_base", work_root="agent/sandbox") as capture:
    adapter = replay.MethodToolAdapter(client, "call_tool", fs_capture=capture)
    with replay.record("run-A"):
        await main()

with replay.managed_sandbox(base_root="agent/sandbox_base", work_root="agent/sandbox") as capture:
    adapter = replay.MethodToolAdapter(client, "call_tool", fs_capture=capture)
    with replay.replay(base_run="run-A"):
        await main()
```

`replay.managed_sandbox(...)` 会在进入 context 时把 `work_root` 重置为 `base_root` 的副本，
并返回 `FilesystemCapture(work_root)`。这样文件 hash 校验才有意义：每次 record 或
replay 都从同一个 base state 开始，而不是复用已经被上一次运行修改过的目录。较低层的
`replay.sandbox(...)` 也可使用，适合调用方需要拿到准备好的 work path 而不是 capture
对象的情况。

## 会记录什么

记录是 JSONL object，常见字段包括：

- `kind`: 主记录为 `"llm"` 或 `"tool"`。provenance sidecar 使用 `kind: "edge"`。
- `input_id`: 归一化语义输入的 SHA256 hash。
- `path_id`: 执行路径，例如 `root/0`、`root.0/0`、`root/tool/0`。
- `input`: 归一化后的 LLM request 或工具输入。
- `output`: 可重放的 response/output。
- `error`: 工具失败时的工具错误记录。
- `effects`: 工具记录中可选的文件系统效果。
- `callsite`: replay 包之外的第一个 frame。
- `metadata`: timestamp、latency、matching mode、usage、semantic fingerprint、
  provenance，以及 fork/input-override 标记等。

对于 LLM 调用，`input_id` 基于归一化后的 OpenAI 语义请求字段，例如 provider、API、
model、messages、tools、temperature、top_p 和 n。request id、trace id、timestamp、
retry id、timeout、extra headers/query 和 idempotency key 等运行时字段会被排除。

对于工具调用，`input_id` 基于：

```json
{
  "tool_name": "...",
  "arguments": {...},
  "namespace": "optional",
  "version": "optional"
}
```

输入和输出必须是 JSON-like。Replay 会尽量归一化 dataclass、类似 Pydantic 的对象、
Decimal、Path、tuple、list、set、dict、string、int、float、bool 和 `None`。不支持的
工具值会抛出 `ToolSerializationError`。

fork 文件会以一行 `fork_metadata` 开头，其中包含 base run、breakpoint record uid、
创建时间和模式。Edge 记录包含 `edge_kind: "data"` 或 `"control"`，以及标识相连主记录的
`from` 和 `to` source object。

## Record 与 Replay

最小 LLM record 示例：

```python
import replay

replay.install()

with replay.record("run-A"):
    await main()
```

最小 replay 示例：

```python
import replay

replay.install()

with replay.replay(base_run="run-A"):
    await main()
```

用覆盖后的 LLM 输出进行断点 replay：

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_output="new assistant content",
):
    await main()
```

到达断点时，Replay 会返回一个 synthetic response，其中第一个 assistant message 使用给定
content。断点分支会被标记为 dirty，而不是永久 live。后续调用会逐条判断：依赖 fork 新
source 的调用，或在 dirty 分支上无法匹配 base 记录的调用，会 live 执行并记录到 fork run；
未受影响且仍能匹配 base 记录的调用仍可 replay。

`override_output` 是文本实验的简写。它会替换第一个 assistant message 的 `content`，并清空
原有 `tool_calls`。

用覆盖后的 assistant message 进行断点 replay：

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_message={
        "content": "I will call the lookup tool with a narrower query.",
        "tool_calls": [
            {
                "id": "call_manual_001",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": "{\"query\":\"new query\",\"limit\":5}",
                },
            }
        ],
    },
):
    await main()
```

`override_message` 会 patch 匹配到的 raw response 中第一个 assistant message。未提供的字段
会保留。如果提供了非空 `tool_calls` 且省略 `finish_reason`，Replay 会把第一个 choice 的
`finish_reason` 设置为 `"tool_calls"`。也可以在 `override_message` 中显式传入
`finish_reason`。

用覆盖后的 LLM 输入进行断点 replay：

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_input={
        "messages": [{"role": "user", "content": "new prompt"}],
    },
):
    await main()
```

input override 会对匹配断点调用的 OpenAI chat completion kwargs 做浅层 merge。修改后的调用
会 live 执行、记录到 fork run，并且当前分支在断点之后被标记为 dirty。

`override_output`、`override_message` 和 `override_input` 互斥。如果三者都未提供，断点只会
让匹配的 LLM 调用 live 执行，并把该分支标记为 dirty。

只有 LLM 记录可以作为断点。工具记录会被 replay，但不能作为断点目标。

如果省略 `fork_run`，Replay 会把 fork 写在 base run 旁边，并分配类似下面的名称：

```text
run-A_fork_001.jsonl
run-A_fork_002.jsonl
```

也可以显式选择 fork 名称：

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_input={"messages": [{"role": "user", "content": "new prompt"}]},
    fork_run="run-A_prompt_experiment",
):
    await main()
```

## 异步分支和路径

`replay.install()` 也会 patch 常见 asyncio 分支入口。这样框架可以在并发运行中分配稳定路径：

```text
root.0/0
root.1/0
root.2/0
```

工具调用使用单独的本地计数器：

```text
root/tool/0
root.0/tool/0
```

Replay 匹配优先使用 `input_id`。如果有多个未消费记录拥有相同 `input_id`，则用 `path_id`
消歧。

这意味着严格 replay 在当前代码到达一个语义输入与历史运行不同的调用时，会故意以
`ReplayMissError` 失败。做实验时，请使用 LLM 断点配合 `override_output`、
`override_message` 或 `override_input`；该分支随后会被标记为 dirty，受 fork 影响或无法匹配
旧记录的下游调用会 live 执行并记录到 fork，而不是试图复用已经过期的下游记录。

启用 semantic instrumentation 后，Replay 也会检查数据/控制 provenance。如果下游调用依赖
fork 产生的 source，即使旧记录有相同 callsite 或 path，该调用也会 live 执行。这样可以避免
依赖 fork 状态的 prompt、工具参数和分支控制调用静默混用旧状态。

## 示例 Agent

### Agent1：纯 LLM 故事 Agent

Record：

```bash
```

也可以通过通用 CLI wrapper 运行脚本：

```bash
python -m replay python --run-id run-A path/to/agent.py
python -m replay python --base-run run-A path/to/agent.py
python -m replay python --base-run run-A --breakpoint-record-uid rec_000003 --override-output "new output" path/to/agent.py
```

Replay：

```bash
```

在断点处用 input override replay：

```bash
```

在断点处用 message override replay：

```bash
```

### Agent4：综合确定性 replay 工作流

Agent4 默认使用本地 fake LLM，所以无需网络访问或 API 额度即可测试框架。它结合了并发
LLM 调用、重复输入消歧、直接同步/异步工具、`MappingToolAdapter`、`MethodToolAdapter`、
预期工具错误、沙箱文件效果和断点 fork。

Record：

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
```

Replay：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

使用 output override 创建 fork：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-output "manual seed override" --fork-run agent4-demo-fork --output test_agent/agent4/outputs/fork.md
```

如果希望 Agent4 调用 `.env` 中配置的真实 endpoint，可以使用 `--real-llm`。

## 查看 Run

run 是位于所选 `log_dir` 下的 JSONL 文件，默认目录是 `replay/runs`。每一行是一条记录。
要选择断点，可以查看 LLM 记录并使用其 `record_uid`：

```bash
python - <<'PY'
import json
from pathlib import Path

for line in Path("replay/runs/run-A.jsonl").read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    if record.get("kind") == "llm":
        print(record["record_uid"], record["path_id"], record["output"].get("content", "")[:80])
PY
```

手动编辑 JSONL 是可能的，但很脆弱。只有当下游语义输入仍匹配已存储的 `input_id` 时，编辑后的
输出才可能 replay 成功。如果编辑后的 LLM 输出改变了后续 prompt 或工具参数，严格 replay 通常会
以 `ReplayMissError` 失败。更推荐使用 breakpoint fork 做可控实验。

## 测试

运行 LLM smoke test：

```bash
python -m replay.tests.smoke_test
```

运行工具 replay test：

```bash
python -m replay.tests.tool_test
```

运行 AST provenance test：

```bash
python -m replay.tests.ast_provenance_test
```

smoke 和 tool 测试会把临时日志写到 `replay/tmp-runs-*` 下，并在退出时删除。AST provenance
测试会构建临时模块，用来验证 data/control edge、semantic fallback、表达式控制流和 fork
传播。

## 当前限制

- 目前只 patch OpenAI SDK `chat.completions.create`。
- 不支持 `stream=True`。
- 工具调用只有通过 `invoke_tool`、`invoke_tool_sync` 或 adapter 接入时才会被记录。
- 工具参数和输出必须是 JSON-like，并且可由 Replay 序列化。
- 文件系统效果捕获只支持显式沙箱中的文本文件。
- 断点目前只能指向 LLM 记录。
- `override_input` 做的是浅层 kwargs merge，不会 deep-merge 单条 message 或 tool schema 等嵌套字段。
- AST instrumentation 只作用于所选 project root 下导入的 Python 文件，并且目前会避开无法安全 thunk
  的结构，例如布尔表达式或条件表达式中的 `await`。
- 直接 HTTP 调用或非 OpenAI SDK 需要自己的 adapter/patch 层。

## 后续可做

- 如有需要，添加 LangChain 风格 adapter。
- 考虑增加一个小型 CLI，用于检查 JSONL 记录并选择断点。
- 如果未来 Agent 需要二进制 artifact，可以把文件系统捕获扩展到普通文本文件之外。
