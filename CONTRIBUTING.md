# Contributing

Thanks for helping improve Replay. The project is still in alpha, so focused
bug reports, reproducible traces made with synthetic data, and small pull
requests are especially useful.

## Development Setup

Use Python 3.12 or newer and Node.js 20 or newer, then install the repository
with the same `uv`-first flow used in the main docs:

```bash
uv venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
npm install
```

Use `npm install` to fetch the viewer dependencies required by the default
visualization workflow.
Install `uv` first by following the official
[`uv` installation guide](https://docs.astral.sh/uv/getting-started/installation/).

Run focused tests while developing:

```bash
python -m pytest replay/tests
```

For deterministic smoke coverage without a live LLM, use the Agent4 demo:

```bash
python -m test_agent.agent4.replay_runner --mode record --run-id agent4-demo --output test_agent/agent4/outputs/record.md
python -m test_agent.agent4.replay_runner --mode replay --run-id agent4-demo --output test_agent/agent4/outputs/replay.md
```

## Pull Request Guidelines

- Keep changes scoped to one behavior or integration.
- Prefer importing the public API from `replay` instead of internal modules.
- Add or update tests for behavior changes.
- Do not commit real business traces, customer data, credentials, `.env` files,
  or local sandbox contents.
- Use synthetic prompts, synthetic tool outputs, and small fixtures in tests and
  documentation.
- Mention any known limitations or unverified integration behavior in the PR
  description.

## Integration Contributions

Integration wrappers should clearly state whether they are templates,
experimental, or validated. A validated integration should include the target
entry point, setup notes, and at least one repeatable record/replay verification
path.

## Documentation

When documenting examples, assume trace files may include prompts, model
outputs, tool arguments, tool results, local file paths, diffs, and error
messages. Keep examples synthetic and safe to publish.
