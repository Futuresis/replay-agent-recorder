# Quickstart

This guide gets you from a fresh clone to a recorded trace, a deterministic replay, a fork, and an offline HTML graph.

## Requirements

- Python 3.12+
- Git
- Node.js 20+ only if you want to rebuild the React/XYFlow viewer assets

The default SVG HTML graph exporter works without rebuilding the viewer.

## 1. Clone and install

```bash
git clone https://github.com/Futuresis/replay-agent-recorder.git
cd replay-agent-recorder

python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

`uv` users can replace the install step with:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## 2. Run the deterministic Agent4 demo

Agent4 uses a fake LLM by default. No API key is needed.

```bash
python -m test_agent.agent4.replay_runner \
  --mode record \
  --run-id agent4-demo \
  --log-dir .replay/runs \
  --output test_agent/agent4/outputs/record.md
```

This writes:

```text
.replay/runs/agent4-demo.jsonl
test_agent/agent4/outputs/record.md
```

Replay the same run:

```bash
python -m test_agent.agent4.replay_runner \
  --mode replay \
  --run-id agent4-demo \
  --log-dir .replay/runs \
  --output test_agent/agent4/outputs/replay.md
```

The replay should produce the same deterministic synthesis as the record run.

## 3. Export a graph

```bash
python -m replay graph summary .replay/runs/agent4-demo.jsonl

python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --output out/agent4-demo.html
```

Open `out/agent4-demo.html` in a browser. The file is static and works offline.

## 4. Fork from an LLM breakpoint

A fork lets you replay a base run up to a selected LLM call, replace that call, and continue the downstream path.

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

Compare the base and fork traces:

```bash
python -m replay graph html .replay/runs/agent4-demo.jsonl \
  --fork .replay/runs/agent4-demo-fork.jsonl \
  --output out/agent4-demo-compare.html
```

## 5. Try Replay on your own script

If your script calls OpenAI-compatible chat completions, the CLI can run it under Replay instrumentation.

```bash
replay record run-A --log-dir .replay/runs path/to/agent.py -- --agent-arg value
replay replay run-A --log-dir .replay/runs path/to/agent.py -- --agent-arg value
replay fork run-A \
  --log-dir .replay/runs \
  --breakpoint-record-uid rec_000003 \
  --override-output "new assistant text" \
  path/to/agent.py -- --agent-arg value
```

Equivalent Python API:

```python
import replay

replay.install(project_root=".")

with replay.record("run-A", log_dir=".replay/runs"):
    await main()

with replay.replay(base_run="run-A", log_dir=".replay/runs"):
    await main()
```

## 6. Real LLM runs

For demos that call a real OpenAI-compatible endpoint, create a project-root `.env` file from `.env.example`.

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

Then pass `--real-llm` to the Agent4 runner:

```bash
python -m test_agent.agent4.replay_runner \
  --mode record \
  --real-llm \
  --run-id agent4-real \
  --log-dir .replay/runs
```

## 7. Common next steps

| Goal | Read next |
|---|---|
| Understand the trace model | [Concepts](concepts.md) |
| Record local tools | [Tool Adapter Protocol](tool-adapter-protocol.md) |
| Capture file changes | [Concepts: Filesystem effects](concepts.md#filesystem-effects) |
| Export graphs | [Visualization](visualization.md) |
| Wrap another agent project | [Integrations](integrations.md) |
| Understand privacy risks | [Security and privacy](security-and-privacy.md) |
