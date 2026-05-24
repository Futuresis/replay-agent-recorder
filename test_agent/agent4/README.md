# Agent4 Comprehensive Replay Test Agent

`agent4` is a deterministic integration-style test agent for the replay system.
It is meant to exercise many replay surfaces in one run instead of demonstrating
one narrow workflow.

Covered scenarios:

- LLM recording/replay for root, dependent, and duplicate concurrent inputs.
- Async branch tracking through `asyncio.gather`, `asyncio.create_task`, and
  `asyncio.TaskGroup`.
- Direct sync and async tools through `replay.invoke_tool_sync` and
  `replay.invoke_tool`.
- `MappingToolAdapter` for a mutable local tool registry.
- `MethodToolAdapter` for a method-shaped workspace tool client.
- Sandboxed text-file effects: inventory, modify, create, append, and delete.
- Expected tool exceptions, including replay as `ReplayedToolError`.
- AST data/control provenance across prompts, tool arguments, and branches.
- Breakpoint/fork runs after LLM output, input, or message overrides.

## How It Is Wired

`replay_runner.py` is the normal entry point. By default it installs a local fake
LLM, installs Replay with AST instrumentation limited to `test_agent/agent4`,
wraps `MAPPING_TOOLS` with `MappingToolAdapter`, wraps
`WorkspaceToolClient.call_tool(...)` with `MethodToolAdapter`, and resets
`test_agent/agent4/sandbox` from `test_agent/agent4/sandbox_base` through
`replay.managed_sandbox(...)`.

The runner writes JSONL runs under `replay/runs` unless `--log-dir` is supplied.
The agent writes a Markdown coverage report under `test_agent/agent4/outputs`
unless `--output` is supplied.

## Environment Setup

Use Python 3.12 or newer and Node.js 20 or newer.

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

Use `npm install` to fetch the viewer dependencies declared in `package.json`.
They support the repository's default visualization workflow instead of being an
optional extra. If you need to rebuild the vendored XYFlow assets, run:

```bash
npm run build:xyflow-viewer
```

## Deterministic Local Run

The default replay runner uses the local fake LLM, so it does not need network
access or real API credits.

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

The replay should regenerate the same report content and sandbox state without
calling the live workspace tools for recorded tool effects.

## Breakpoint Forks

Inspect the LLM record ids, then replay from one of them:

```bash
python - <<'PY'
import json
from pathlib import Path

for line in Path("replay/runs/agent4-demo.jsonl").read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    if item.get("kind") == "llm":
        print(item["record_uid"], item["path_id"], item["output"].get("content"))
PY
```

Replace an assistant response with plain text:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-output "manual seed override" --fork-run agent4-demo-fork --output test_agent/agent4/outputs/fork.md
```

Patch the OpenAI input kwargs and run that breakpoint call live:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-input-json "{\"messages\":[{\"role\":\"user\",\"content\":\"patched seed prompt\"}]}" --fork-run agent4-demo-input-fork
```

Patch the first assistant message:

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-message-json "{\"content\":\"manual assistant content\"}" --fork-run agent4-demo-message-fork
```

The override modes are mutually exclusive. Fork records are written to the
chosen `--fork-run` name or, if omitted, to the next
`<base>_fork_NNN.jsonl` file.

## Real LLM Run

Use `--real-llm` to call the configured OpenAI-compatible endpoint from `.env`:

```bash
python -m test_agent.agent4.replay_runner --real-llm --mode record --run-id agent4-real
```

Copy the repository-root `.env.example` to `.env` and fill in:

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

The code still accepts legacy variable names such as `API_KEY` and `BASE_URL`,
but the documentation follows `.env.example` as the canonical format.

## Direct Agent Run

The agent itself can also be run directly. This does not record or replay unless
you wrap it with Replay yourself.

```bash
python -m test_agent.agent4.main --fake-llm
```

Useful direct-run options:

- `--output`: write the Markdown coverage report to a specific path.
- `--sandbox-root`: use a different workspace tool root.

Useful replay-runner options:

- `--mode record|replay`: choose whether to write a base run or replay one.
- `--run-id`: base JSONL run id under `--log-dir`.
- `--log-dir`: directory for JSONL run files.
- `--output`: report output path.
- `--breakpoint-record-uid`: LLM record uid to fork from.
- `--override-output`, `--override-input-json`, `--override-message-json`:
  mutually exclusive breakpoint overrides.
- `--fork-run`: explicit fork JSONL run id.
- `--semantic-fallback`: allow callsite-fingerprint fallback when exact input
  matching misses.
- `--real-llm`: use `.env` instead of the fake LLM.

## 中文

`agent4` 是 Replay 系统的确定性集成式测试 Agent。它的目的不是展示某一个窄场景，而是在一次
运行中覆盖多个 replay surface。

覆盖场景：

- root、dependent 和 duplicate concurrent input 的 LLM record/replay。
- 通过 `asyncio.gather`、`asyncio.create_task` 和 `asyncio.TaskGroup` 进行异步分支跟踪。
- 通过 `replay.invoke_tool_sync` 和 `replay.invoke_tool` 直接调用同步/异步工具。
- 使用 `MappingToolAdapter` 适配可变本地工具 registry。
- 使用 `MethodToolAdapter` 适配 method-shaped workspace tool client。
- 沙箱文本文件效果：inventory、modify、create、append 和 delete。
- 预期工具异常，以及重放为 `ReplayedToolError`。
- prompt、工具参数和分支之间的 AST data/control provenance。
- LLM output、input 或 message override 之后的 breakpoint/fork run。

## 如何接线

`replay_runner.py` 是常规入口。默认情况下，它会安装本地 fake LLM，安装 Replay 并把 AST
instrumentation 限制到 `test_agent/agent4`，用 `MappingToolAdapter` 包装 `MAPPING_TOOLS`，
用 `MethodToolAdapter` 包装 `WorkspaceToolClient.call_tool(...)`，并通过
`replay.managed_sandbox(...)` 从 `test_agent/agent4/sandbox_base` 重置
`test_agent/agent4/sandbox`。

除非传入 `--log-dir`，runner 会把 JSONL run 写入 `replay/runs`。除非传入 `--output`，
Agent 会把 Markdown coverage report 写入 `test_agent/agent4/outputs`。

## 环境准备

请使用 Python 3.12+ 和 Node.js 20+。

安装 `uv` 时，请先参考官方安装文档：
[`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/)。

推荐在仓库根目录按下面顺序完成首次安装：

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

其中 `npm install` 用于安装 `package.json` 中声明的 viewer 依赖，服务于仓库默认可视化链路。
如果需要重新构建 vendored XYFlow 资源，可执行：

```bash
npm run build:xyflow-viewer
```

## 确定性本地运行

默认 replay runner 使用本地 fake LLM，所以不需要网络访问或真实 API 额度。

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

replay 应该在不调用 live workspace tools 处理已记录工具效果的情况下，重新生成相同报告内容和
sandbox 状态。

## 断点 Fork

先查看 LLM record id，然后从其中一个断点 replay：

```bash
python - <<'PY'
import json
from pathlib import Path

for line in Path("replay/runs/agent4-demo.jsonl").read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    if item.get("kind") == "llm":
        print(item["record_uid"], item["path_id"], item["output"].get("content"))
PY
```

把 assistant response 替换为普通文本：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-output "manual seed override" --fork-run agent4-demo-fork --output test_agent/agent4/outputs/fork.md
```

patch OpenAI input kwargs，并让该断点调用 live 执行：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-input-json "{\"messages\":[{\"role\":\"user\",\"content\":\"patched seed prompt\"}]}" --fork-run agent4-demo-input-fork
```

patch 第一个 assistant message：

```bash
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --breakpoint-record-uid rec_000001 --override-message-json "{\"content\":\"manual assistant content\"}" --fork-run agent4-demo-message-fork
```

这些 override 模式互斥。Fork 记录会写入指定的 `--fork-run` 名称；如果省略，则写入下一个
`<base>_fork_NNN.jsonl` 文件。

## 真实 LLM 运行

使用 `--real-llm` 可以调用 `.env` 中配置的 OpenAI-compatible endpoint：

```bash
python -m test_agent.agent4.replay_runner --real-llm --mode record --run-id agent4-real
```

把仓库根目录的 `.env.example` 复制为 `.env`，并填写：

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

代码仍兼容历史变量名，例如 `API_KEY` 和 `BASE_URL`，但文档以 `.env.example` 为准。

## 直接运行 Agent

也可以直接运行 Agent 本体。除非你自己用 Replay 包装它，否则这种方式不会 record 或 replay。

```bash
python -m test_agent.agent4.main --fake-llm
```

常用 direct-run 选项：

- `--output`: 把 Markdown coverage report 写到指定路径。
- `--sandbox-root`: 使用不同的 workspace tool root。

常用 replay-runner 选项：

- `--mode record|replay`: 选择写入 base run 还是 replay 已有 run。
- `--run-id`: `--log-dir` 下的 base JSONL run id。
- `--log-dir`: JSONL run 文件目录。
- `--output`: report 输出路径。
- `--breakpoint-record-uid`: 要从哪个 LLM record uid 创建 fork。
- `--override-output`、`--override-input-json`、`--override-message-json`:
  互斥的断点 override 方式。
- `--fork-run`: 显式指定 fork JSONL run id。
- `--semantic-fallback`: 精确输入匹配失败时，允许 callsite-fingerprint fallback。
- `--real-llm`: 使用 `.env`，而不是 fake LLM。
