# my_agent Replay Integration

Status: template

This is a template for building a Replay integration. It is not a directly
runnable integration until you fill in the target agent launch details and tool
adapter wiring.

This directory is a Replay wrapper skeleton. Keep agent-specific logic in
`tool_adapter.py`; keep standard record/replay CLI behavior in `runner.py`.

## Run

```bash
python runner.py --replay-mode record --run-id my_agent-demo --target-root /path/to/agent --target-script path/to/entry.py -- --agent-arg value
python runner.py --replay-mode replay --base-run my_agent-demo --target-root /path/to/agent --target-script path/to/entry.py -- --agent-arg value
```

## Configure This Template

- `tool_adapter.py`: import the target agent's tool registry or client and
  return Replay adapters.
- `runner.py`: add only target-specific launch arguments. Do not duplicate
  Replay's standard record/replay flags.

Selected tool style: `class-method`.
