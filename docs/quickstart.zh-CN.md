# 快速开始

这份指南会带你从 fresh clone 跑到：记录 trace、确定性重放、创建 fork、导出离线 HTML 图。

## 环境要求

- Python 3.12+
- Git
- 只有在需要重新构建 React/XYFlow viewer assets 时，才需要 Node.js 20+

默认的 SVG HTML 图导出不需要重新构建 viewer。

## 1. Clone 并安装

```bash
git clone https://github.com/Futuresis/replay-agent-recorder.git
cd replay-agent-recorder

python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

如果使用 `uv`：

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## 2. 运行确定性 Agent4 示例

Agent4 默认使用 fake LLM，不需要 API key。

```bash
python -m test_agent.agent4.replay_runner \
  --mode record \
  --run-id agent4-demo \
  --log-dir .replay/runs \
  --output test_agent/agent4/outputs/record.md
```

这会写出：

```text
.replay/runs/agent4-demo.jsonl
test_agent/agent4/outputs/record.md
```

重放同一次运行：

```bash
python -m test_agent.agent4.replay_runner \
  --mode replay \
  --run-id agent4-demo \
  --log-dir .replay/runs \
  --output test_agent/agent4/outputs/replay.md
```

record 和 replay 的 deterministic synthesis 应该一致。

## 3. 导出执行图

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl

python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --output out/agent4-demo.html
```

用浏览器打开 `out/agent4-demo.html`。它是静态文件，可以离线查看。

## 4. 从 LLM 断点创建 fork

fork 可以让你重放 base run 到某个 LLM 调用点，替换这个调用，然后继续执行下游路径。

```bash
python -m test_agent.agent4.replay_runner \
  --mode replay \
  --run-id agent4-demo \
  --log-dir .replay/runs \
  --breakpoint-record-uid rec_000001 \
  --override-output "manual seed override" \
  --fork-run agent4-demo-fork \
  --output test_agent/agent4/outputs/fork.md
```

比较 base 和 fork：

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --fork .replay/runs/agent4-demo-fork.jsonl \
  --output out/agent4-demo-compare.html
```

## 5. 接入你自己的脚本

如果你的脚本调用 OpenAI-compatible chat completions，可以直接用 CLI 插桩运行。

```bash
replay record run-A --log-dir .replay/runs path/to/agent.py -- --agent-arg value
replay replay run-A --log-dir .replay/runs path/to/agent.py -- --agent-arg value
replay fork run-A \
  --log-dir .replay/runs \
  --breakpoint-record-uid rec_000003 \
  --override-output "new assistant text" \
  path/to/agent.py -- --agent-arg value
```

等价 Python API：

```python
import replay

replay.install(project_root=".")

with replay.record("run-A", log_dir=".replay/runs"):
    await main()

with replay.replay(base_run="run-A", log_dir=".replay/runs"):
    await main()
```

## 6. 真实 LLM 运行

如果要调用真实 OpenAI-compatible endpoint，请从 `.env.example` 创建项目根目录下的 `.env`。

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

然后给 Agent4 runner 加 `--real-llm`：

```bash
python -m test_agent.agent4.replay_runner \
  --mode record \
  --real-llm \
  --run-id agent4-real \
  --log-dir .replay/runs
```

## 7. 下一步

| 目标 | 继续阅读 |
|---|---|
| 理解 trace 模型 | [核心概念](concepts.zh-CN.md) |
| 记录本地工具 | [工具适配器协议](tool-adapter-protocol.md) |
| 捕获文件变化 | [核心概念：文件系统效果](concepts.zh-CN.md#文件系统效果) |
| 导出图 | [Visualization](visualization.md) |
| 包装另一个 Agent 项目 | [Integrations](integrations.md) |
| 理解隐私风险 | [Security and privacy](security-and-privacy.md) |
