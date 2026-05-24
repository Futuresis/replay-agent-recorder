# Replay Agent Run Recorder

License: MIT

Security note: Replay traces can contain prompts, LLM outputs, tool arguments
and results, local file paths, file contents or diffs, and error details. Do not
publicly commit traces from real business or private workflows.

## English

Replay is a Python framework for recording, replaying, forking, and visualizing
LLM-agent runs. It patches OpenAI-compatible chat completion calls, records local
tool calls through a unified protocol, can capture sandboxed text-file effects,
and can fork a replay from a selected LLM breakpoint.

The project is useful when you want deterministic reruns of agent workflows,
debuggable traces, controlled "what if" experiments after an LLM response, or an
offline graph view of how LLM calls, tools, branches, and file effects relate to
each other.

### First-Time Setup

Use this order for a fresh clone:

1. Install Python 3.12 or newer.
2. Install Node.js 20 or newer.
3. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
4. Create and activate a local virtual environment with `uv`.
5. Install Python and Node dependencies.
6. Copy `.env.example` only if you want to run a real LLM endpoint.

Replay's default visualization workflow depends on the Node-based viewer build
defined in `package.json`, so Node is treated as a standard repository
dependency rather than an optional extra.

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

If you prefer not to activate the virtual environment manually, run later
Python commands with `uv run`, for example
`uv run python -m replay.tests.smoke_test`.

If you need to rebuild the vendored XYFlow viewer assets used by the HTML
visualizer, run:

```bash
npm run build:xyflow-viewer
```

After the environment is ready, run the deterministic Agent4 demo. It uses a
fake LLM by default, so no API key or network access is required:

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/agent4-demo.html
```

For a normal Python script that calls OpenAI-compatible chat completions after
`replay.install()`, the shorter CLI is:

```bash
replay record my-run path/to/agent.py -- --agent-arg value
replay replay my-run path/to/agent.py -- --agent-arg value
replay fork my-run --breakpoint-record-uid rec_000001 --override-output "new assistant text" path/to/agent.py
```

### What Is Implemented

- Recording and replaying OpenAI SDK `chat.completions.create` calls, including
  sync and async paths.
- Stable matching of replay records by normalized semantic input plus `path_id`
  disambiguation for concurrent branches.
- Async branch tracking for `asyncio.gather`, `asyncio.create_task`, and
  `asyncio.TaskGroup.create_task`.
- Local tool recording through `invoke_tool`, `invoke_tool_sync`,
  `MappingToolAdapter`, and `MethodToolAdapter`.
- Replaying tool outputs and recorded tool exceptions.
- Sandboxed text-file effect capture and replay for create, modify, and delete
  operations.
- Managed sandbox reset helpers so record and replay start from a clean base
  directory.
- Breakpoint forks from LLM records with `override_output`,
  `override_message`, or `override_input`.
- Optional AST-level provenance instrumentation that records data/control edges
  between LLM calls, tool calls, prompts, arguments, and branch conditions.
- Graph exports from JSONL traces: summary JSON, Graph IR JSON, Mermaid, and an
  offline interactive HTML explorer.
- Base/fork visualization metadata for changed, unchanged, new, missing, and
  downstream nodes.
- Deterministic Agent4 workflow covering LLM calls, local tools, sandboxed
  file effects, forks, and visualization metadata.

### Repository Layout

- `replay/`: framework package and CLI entry point.
- `replay/api.py`: public API for install, record, replay, tools, and sandboxes.
- `replay/context.py`: record/replay sessions, path allocation, breakpoints, and
  JSONL writing.
- `replay/openai_patch.py`: OpenAI SDK chat completion patching.
- `replay/asyncio_patch.py`: async branch path tracking.
- `replay/tools.py` and `replay/tool_adapters.py`: unified tool protocol and
  adapters.
- `replay/filesystem_effects.py` and `replay/sandbox_manager.py`: sandboxed
  filesystem capture and reset helpers.
- `replay/instrument.py`, `replay/import_hook.py`, and
  `replay/semantic_runtime.py`: optional AST provenance layer.
- `replay/graph_ir.py` and `replay/visualize.py`: trace-to-graph and
  visualization exports.
- `replay/runs/`: local JSONL trace output; ignored by default.
- `replay/tests/`: smoke, tool, provenance, and visualization tests.
- `test_agent/agent4/`: deterministic comprehensive replay test agent.
- `guidance/visualization/`: visualization quickstart and implementation notes.

### Public API Boundary

Prefer importing from the top-level `replay` package. The names exported by
`replay.__all__` are the recommended public API for the alpha release; internal
modules may change without compatibility guarantees.

### Packaging Notes

The source repository keeps `replay/tests/` for development, but release wheels
exclude `replay.tests*`. Vendored visualization assets under
`replay/xyflow_assets/` are included in the package.

### Built-In Integration Wrappers

Replay includes wrapper scripts for several open-source agent projects. These
wrappers are meant for users who already have the target project installed or
checked out locally: Replay provides the record/replay entry point, while the
user points it at the target checkout with `--target-root` and, when needed,
selects an entry with `--entry`.

The wrappers are not vendored copies of the target agents. They avoid requiring
users to write their own Replay glue code for common LangChain/LangGraph-style
agent projects, but compatibility is best-effort until each wrapper is
validated against pinned upstream versions.

| Integration | Status | Notes |
|---|---|---|
| `integrations/my_agent` | template | Generated wrapper template for custom agents; fill in target launch and tool adapter details before use. |
| `integrations/deepagents` | built-in wrapper | Replay wrapper for an existing DeepAgents checkout; includes detected candidate entries and accepts explicit `--entry` overrides. |
| `integrations/open_deep_research` | built-in wrapper | Replay wrapper for an existing Open Deep Research checkout; defaults to detected LangGraph entries when available. |
| `integrations/open_swe` | built-in wrapper | Replay wrapper for an existing Open SWE checkout; defaults to detected LangGraph entries when available. |
| `integrations/swe_agent` | built-in wrapper | Replay wrapper for an existing SWE-agent-style checkout; defaults to detected LangGraph entries when available. |

### Requirements

Replay is a local Python package in this repository. The framework and demo
agents expect Python 3.12+ and Node.js 20+ in the current development setup.

Recommended first-time install flow:

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

Use `npm install` to fetch the viewer dependencies declared in
`package.json`. They support the repository's default visualization workflow,
including the XYFlow-based HTML viewer build.

Install `uv` first by following the official
[`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/).

Live LLM demos need a project-root `.env` file. Copy `.env.example` and fill in
these three variables:

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

The code still accepts legacy variable names such as `API_KEY` and `BASE_URL`,
but the documentation follows `.env.example` as the canonical format.

### Quick Start: Deterministic Local Demo

Agent4 uses a fake LLM by default, so it can run without network access or API
credits.

Use synthetic runs for examples and bug reports. Do not publish JSONL traces
from real business workflows without reviewing and redacting them first.

Record a run:

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
```

Replay the run:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

Create a fork by replacing an LLM breakpoint output:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-output "manual seed override" --fork-run agent4-demo-fork --output test_agent/agent4/outputs/fork.md
```

The base trace is written to `replay/runs/agent4-demo.jsonl`. Fork traces are
written to `replay/runs/<fork-run>.jsonl` or auto-numbered as
`<base>_fork_NNN.jsonl`.

Run the focused test commands from the same activated `uv` environment:

```bash
python -m replay.tests.smoke_test
python -m replay.tests.tool_test
python -m replay.tests.ast_provenance_test
python -m replay.tests.test_graph_ir
python -m replay.tests.test_visualize_cli
python -m replay.tests.test_visualize_html
```

### Use Replay In Your Own Agent

For new integrations, generate a thin wrapper instead of copying a demo script:

```bash
python -m replay scaffold integration --name my-agent --tool-style method
```

The generated wrapper uses `replay.integration.add_replay_arguments(...)`,
`config_from_args(...)`, and `replay_session(...)`, so standard record/replay
flags stay consistent across agents. Fill in only the target launch details and
the Python tool adapter wiring. See
[`docs/integration-scaffold.md`](docs/integration-scaffold.md) for the full
scaffold usage guide, including when to edit `runner.py` and how to fill
`tool_adapter.py`.

Install patches once near process startup:

```python
import replay

replay.install()
```

Record a run:

```python
with replay.record("run-A"):
    await main()
```

Replay the same run:

```python
with replay.replay(base_run="run-A"):
    await main()
```

Fork from an LLM breakpoint with a replacement assistant output:

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_output="new assistant content",
):
    await main()
```

Patch the assistant message, including tool calls:

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

Patch the OpenAI call kwargs and execute that breakpoint call live:

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

`override_output`, `override_message`, and `override_input` are mutually
exclusive.

### Record Tools

Replay does not depend on a specific agent framework. Route tools through the
unified protocol:

For a complete adapter protocol, including input/output serialization rules,
record/replay behavior, filesystem effects, and custom adapter templates, see
[`docs/tool-adapter-protocol.md`](docs/tool-adapter-protocol.md).

```python
result = await replay.invoke_tool(
    "search",
    {"query": "hello"},
    lambda: search({"query": "hello"}),
)
```

For synchronous tools:

```python
result = replay.invoke_tool_sync(
    "calculator",
    {"expression": "1 + 1"},
    lambda: calculator({"expression": "1 + 1"}),
)
```

For registries or method-shaped clients, install an adapter:

```python
adapter = replay.MappingToolAdapter(tool_registry, namespace="local")
adapter.install()

method_adapter = replay.MethodToolAdapter(client, "call_tool", namespace="mcp")
method_adapter.install()
```

For framework-owned tool clients where the wrapper must patch a class before the
agent creates instances, use `replay.ClassMethodToolAdapter`. It supports custom
argument extraction and optional tool-name filters.

### Capture Filesystem Effects

For tools that modify local text files, use an explicit sandbox:

```python
with replay.managed_sandbox(
    base_root="agent/sandbox_base",
    work_root="agent/sandbox",
) as capture:
    adapter = replay.MethodToolAdapter(
        client,
        "call_tool",
        namespace="workspace",
        fs_capture=capture,
    )
    adapter.install()
    with replay.record("run-A"):
        await main()
```

During replay, the live tool is not called. Replay verifies the recorded
pre-state hashes, applies the captured text-file changes, and returns the
recorded output. If a fork makes the sandbox dirty, later tools using that same
capture run live and are recorded into the fork.

### CLI Usage

Run any Python script under Replay instrumentation:

```bash
python -m replay python --run-id run-A path/to/agent.py
python -m replay python --base-run run-A path/to/agent.py
python -m replay python --base-run run-A --breakpoint-record-uid rec_000003 --override-output "new output" path/to/agent.py
```

Useful CLI options include:

- `--log-dir`: choose where JSONL traces are stored.
- `--fork-run`: choose the fork trace name.
- `--override-message-json`: patch the first assistant message at a breakpoint.
- `--override-input-json`: patch OpenAI kwargs at a breakpoint.
- `--semantic-fallback`: allow callsite fallback if exact semantic matching
  misses.
- `--no-semantic`: disable AST provenance instrumentation.
- `--project-root`, `--include`, `--exclude`: scope AST instrumentation.

### Visualize Traces

Visualization reads existing JSONL traces. The CLI entry point is:

```text
python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...] [options]
```

Print a summary JSON:

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl
```

Export Graph IR:

```bash
python -m replay graph export-ir replay/runs/agent4-demo.jsonl --output out/graph.json
```

Export Mermaid:

```bash
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by run --output out/graph.md
```

Export an offline HTML explorer:

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/graph.html
```

HTML defaults to `--asset-mode inline --renderer svg`. Use
`--asset-mode vendored` to write adjacent local CSS/JS assets, or
`--renderer xyflow` for the static XYFlow/React Flow viewer.

Compare a base trace with a fork:

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --fork replay/runs/agent4-demo-fork.jsonl --output out/agent4-compare.html
```

Common options are `--fork PATH` (repeatable), `--focus NODE_ID`,
`--direction upstream|downstream|both`, `--max-depth N`, `--title TEXT`,
`--output PATH`, and `--group-by none|path|span|run`. `export-ir` and `html`
require `--output`; `mermaid` prints to stdout when `--output` is omitted.

The HTML explorer is read-only and works offline. It supports search, filters,
focus, timeline navigation, node/edge inspection, evidence views, and base/fork
diff highlighting. See `guidance/visualization/quickstart.md` for the full
command and parameter reference.

### Demo Agent

Agent4 is the maintained deterministic comprehensive fake-LLM workflow.

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo
```

### Tests

Run focused tests:

```bash
python -m replay.tests.smoke_test
python -m replay.tests.tool_test
python -m replay.tests.ast_provenance_test
python -m replay.tests.test_graph_ir
python -m replay.tests.test_visualize_cli
python -m replay.tests.test_visualize_html
```

### Current Limitations

- Only OpenAI SDK `chat.completions.create` is patched.
- Streaming responses are not supported.
- Tool calls are recorded only when routed through Replay's tool protocol or an
  adapter.
- Tool inputs and outputs must be JSON-like and serializable.
- Filesystem capture supports ordinary text files inside an explicit sandbox.
- Breakpoints currently target LLM records only.
- `override_input` performs a shallow kwargs merge.
- The static HTML explorer does not execute replay or fork actions.
- Direct HTTP calls and non-OpenAI SDK clients need their own adapter or patch
  layer.

For deeper implementation details, see `replay/README.md` and
`guidance/visualization/quickstart.md`.

### License

Replay is released under the MIT License. See `LICENSE` for details.

## 中文

Replay 是一个用于记录、重放、分叉和可视化 LLM Agent 运行过程的 Python
框架。它会 patch OpenAI 兼容的 chat completion 调用，用统一协议记录本地
工具调用，可以捕获沙箱内文本文件的变化，并且可以从某个 LLM 断点开始创建
新的 replay fork。

当你需要确定性复现 Agent 工作流、调试运行 trace、在某次 LLM 输出之后做
可控的 "what if" 实验，或者离线查看 LLM 调用、工具调用、分支和文件变化之
间的关系图时，这个项目会很有用。

### 已实现内容

- 记录和重放 OpenAI SDK `chat.completions.create` 调用，支持同步和异步路径。
- 通过归一化后的语义输入匹配 replay 记录，并使用 `path_id` 区分并发分支。
- 跟踪 `asyncio.gather`、`asyncio.create_task` 和
  `asyncio.TaskGroup.create_task` 创建的异步分支。
- 通过 `invoke_tool`、`invoke_tool_sync`、`MappingToolAdapter` 和
  `MethodToolAdapter` 记录本地工具调用。
- 重放工具输出和已记录的工具异常。
- 捕获和重放沙箱内文本文件的 create、modify、delete 效果。
- 提供 managed sandbox 重置工具，保证 record 和 replay 都从干净基线目录开始。
- 可以从 LLM 记录断点创建 fork，并支持 `override_output`、
  `override_message` 或 `override_input`。
- 可选的 AST 级 provenance 插桩，用来记录 LLM 调用、工具调用、prompt、
  参数和分支条件之间的数据/控制依赖边。
- 从 JSONL trace 导出图数据：summary JSON、Graph IR JSON、Mermaid，以及
  离线交互式 HTML explorer。
- 支持 base/fork 可视化差异元数据，包括 changed、unchanged、new、missing
  和 downstream 节点。
- 提供多个示例 Agent，覆盖纯 LLM、本地工具、类似 MCP 的沙箱工具，以及
  使用 fake LLM 的确定性集成工作流。

### 仓库结构

- `replay/`: 框架包和 CLI 入口。
- `replay/api.py`: install、record、replay、tool、sandbox 等公开 API。
- `replay/context.py`: record/replay session、路径分配、断点逻辑和 JSONL 写入。
- `replay/openai_patch.py`: OpenAI SDK chat completion patch。
- `replay/asyncio_patch.py`: 异步分支路径跟踪。
- `replay/tools.py` 和 `replay/tool_adapters.py`: 统一工具协议和适配器。
- `replay/filesystem_effects.py` 和 `replay/sandbox_manager.py`: 沙箱文件效果捕获
  和重置工具。
- `replay/instrument.py`、`replay/import_hook.py` 和
  `replay/semantic_runtime.py`: 可选 AST provenance 层。
- `replay/graph_ir.py` 和 `replay/visualize.py`: trace 转 graph 和可视化导出。
- `replay/runs/`: 本地生成的 JSONL trace 输出，默认不提交。
- `replay/tests/`: smoke、tool、provenance 和 visualization 测试。
- `test_agent/agent4/`: 确定性的综合 replay 测试 Agent。
- `guidance/visualization/`: 可视化 quickstart 和实现说明。

### 环境要求

Replay 是本仓库中的本地 Python 包。当前开发环境下，框架和示例 Agent 面向
Python 3.12+ 和 Node.js 20+。

推荐新用户按下面顺序完成首次安装：

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

其中 `npm install` 用于安装 `package.json` 中声明的 viewer 依赖，它服务于仓库默认
可视化链路，而不是可选附加项。

安装 `uv` 时，请先参考官方安装文档：
[`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/)。

如果需要重新构建 XYFlow 可视化资源，可继续执行：

```bash
npm run build:xyflow-viewer
```

需要真实 LLM 的示例要在项目根目录创建 `.env`。可以复制 `.env.example` 并填写这三项：

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

代码仍兼容历史变量名，例如 `API_KEY` 和 `BASE_URL`，但文档以 `.env.example` 为准。

### 快速开始：确定性本地示例

Agent4 默认使用 fake LLM，所以不需要网络访问，也不需要真实 API 额度。

记录一次运行：

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
```

重放这次运行：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

通过替换某个 LLM 断点输出创建 fork：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-output "manual seed override" --fork-run agent4-demo-fork --output test_agent/agent4/outputs/fork.md
```

base trace 会写到 `replay/runs/agent4-demo.jsonl`。fork trace 会写到
`replay/runs/<fork-run>.jsonl`，如果不指定名称，则自动命名为
`<base>_fork_NNN.jsonl`。

同一个已激活的 `uv` 虚拟环境里也可以直接运行重点测试：

```bash
python -m replay.tests.smoke_test
python -m replay.tests.tool_test
python -m replay.tests.ast_provenance_test
python -m replay.tests.test_graph_ir
python -m replay.tests.test_visualize_cli
python -m replay.tests.test_visualize_html
```

### 在自己的 Agent 中使用 Replay

在进程启动附近安装 patch：

```python
import replay

replay.install()
```

记录一次运行：

```python
with replay.record("run-A"):
    await main()
```

重放同一次运行：

```python
with replay.replay(base_run="run-A"):
    await main()
```

从某个 LLM 断点 fork，并替换 assistant 输出：

```python
with replay.replay(
    base_run="run-A",
    breakpoint_record_uid="rec_000003",
    override_output="new assistant content",
):
    await main()
```

patch assistant message，包括 tool calls：

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

patch OpenAI 调用 kwargs，并让该断点调用 live 执行：

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

`override_output`、`override_message` 和 `override_input` 互斥。

### 记录工具调用

Replay 不绑定特定 Agent 框架。把工具调用接入统一协议即可：

```python
result = await replay.invoke_tool(
    "search",
    {"query": "hello"},
    lambda: search({"query": "hello"}),
)
```

同步工具可以这样写：

```python
result = replay.invoke_tool_sync(
    "calculator",
    {"expression": "1 + 1"},
    lambda: calculator({"expression": "1 + 1"}),
)
```

如果工具以 registry 或 method-shaped client 的形式组织，可以安装适配器：

```python
adapter = replay.MappingToolAdapter(tool_registry, namespace="local")
adapter.install()

method_adapter = replay.MethodToolAdapter(client, "call_tool", namespace="mcp")
method_adapter.install()
```

### 捕获文件系统变化

如果工具会修改本地文本文件，请使用显式沙箱：

```python
with replay.managed_sandbox(
    base_root="agent/sandbox_base",
    work_root="agent/sandbox",
) as capture:
    adapter = replay.MethodToolAdapter(
        client,
        "call_tool",
        namespace="workspace",
        fs_capture=capture,
    )
    adapter.install()
    with replay.record("run-A"):
        await main()
```

重放时不会调用真实工具。Replay 会先校验记录中的 pre-state hash，再应用捕获到的
文本文件变化，并返回记录中的工具输出。如果 fork 让这个 sandbox 变脏，后续使用
同一个 capture 的工具会 live 执行，并写入 fork trace。

### CLI 用法

用 Replay 插桩运行任意 Python 脚本：

```bash
python -m replay python --run-id run-A path/to/agent.py
python -m replay python --base-run run-A path/to/agent.py
python -m replay python --base-run run-A --breakpoint-record-uid rec_000003 --override-output "new output" path/to/agent.py
```

常用 CLI 选项包括：

- `--log-dir`: 指定 JSONL trace 存储目录。
- `--fork-run`: 指定 fork trace 名称。
- `--override-message-json`: 在断点处 patch 第一个 assistant message。
- `--override-input-json`: 在断点处 patch OpenAI kwargs。
- `--semantic-fallback`: 精确语义匹配失败时允许 callsite fallback。
- `--no-semantic`: 关闭 AST provenance 插桩。
- `--project-root`、`--include`、`--exclude`: 限定 AST 插桩范围。

### 可视化 Trace

可视化模块读取已有 JSONL trace。CLI 入口是：

```text
python -m replay graph {summary,export-ir,mermaid,html} paths [paths ...] [options]
```

打印 summary JSON：

```bash
python -m replay graph summary replay/runs/agent4-demo.jsonl
```

导出 Graph IR：

```bash
python -m replay graph export-ir replay/runs/agent4-demo.jsonl --output out/graph.json
```

导出 Mermaid：

```bash
python -m replay graph mermaid replay/runs/agent4-demo.jsonl --group-by run --output out/graph.md
```

导出离线 HTML explorer：

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --output out/graph.html
```

HTML 默认使用 `--asset-mode inline --renderer svg`。如果希望写出相邻的本地
CSS/JS 资源文件，用 `--asset-mode vendored`；如果希望使用静态 XYFlow/React Flow
viewer，用 `--renderer xyflow`。

比较 base trace 和 fork：

```bash
python -m replay graph html replay/runs/agent4-demo.jsonl --fork replay/runs/agent4-demo-fork.jsonl --output out/agent4-compare.html
```

常用参数包括 `--fork PATH`（可重复）、`--focus NODE_ID`、
`--direction upstream|downstream|both`、`--max-depth N`、`--title TEXT`、
`--output PATH` 和 `--group-by none|path|span|run`。`export-ir` 和 `html`
必须传 `--output`；`mermaid` 省略 `--output` 时会输出到 stdout。

HTML explorer 是只读的，并且可以离线打开。它支持搜索、过滤、focus、时间线导航、
节点/边检查、evidence 查看，以及 base/fork 差异高亮。完整命令和参数说明见
`guidance/visualization/quickstart.md`。

### 示例 Agent

Agent1：纯 LLM 故事工作流。

```bash
```

Agent2：LLM 加本地项目评审工具。

```bash
```

Agent3：LLM 加类似 MCP 的沙箱文件工具。

```bash
```

Agent4：确定性的综合 fake-LLM 工作流。

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo
```

### 测试

运行重点测试：

```bash
python -m replay.tests.smoke_test
python -m replay.tests.tool_test
python -m replay.tests.ast_provenance_test
python -m replay.tests.test_graph_ir
python -m replay.tests.test_visualize_cli
python -m replay.tests.test_visualize_html
```

### 当前限制

- 目前只 patch OpenAI SDK `chat.completions.create`。
- 不支持 streaming response。
- 工具调用只有通过 Replay 工具协议或适配器接入时才会被记录。
- 工具输入和输出必须是 JSON-like 且可序列化。
- 文件系统捕获仅支持显式沙箱内的普通文本文件。
- 断点目前只支持 LLM 记录。
- `override_input` 只做浅层 kwargs merge。
- 静态 HTML explorer 不执行 replay 或 fork 操作。
- 直接 HTTP 调用和非 OpenAI SDK client 需要额外的适配器或 patch 层。

更深入的实现细节见 `replay/README.md` 和
`guidance/visualization/quickstart.md`。
