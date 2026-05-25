# Security and privacy

Replay Agent Recorder is local-first, but local traces can still contain sensitive data. Treat trace files as confidential unless you intentionally created them from synthetic data.

## What traces may contain

A JSONL trace may include:

- prompts
- system messages
- model responses
- OpenAI-compatible request kwargs
- tool names
- tool arguments
- tool return values
- recorded tool exceptions
- local file paths
- text file contents or diffs from sandbox capture
- branch metadata
- semantic provenance metadata
- error messages and debugging context

## Do not publish raw business traces

Do not commit real traces from private, customer, internal, or business workflows to a public repository.

Recommended practice:

```gitignore
.replay/
replay/runs/*.jsonl
out/*.html
.env
```

Use synthetic traces for examples, tests, issue reports, and screenshots.

## Environment variables

Real LLM demos may use a project-root `.env` file. Never commit it.

```env
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=your_model_name
```

## Sandboxed file capture

Filesystem capture is designed for explicit sandbox directories. It should not be pointed at a whole project, home directory, secrets directory, or production workspace.

Safer pattern:

```python
with replay.managed_sandbox(
    base_root="agent/sandbox_base",
    work_root="agent/sandbox",
) as capture:
    ...
```

Risky pattern:

```python
# Do not do this.
with replay.managed_sandbox(base_root="/", work_root="/"):
    ...
```

## Sharing traces safely

Before sharing a trace:

1. Confirm it was generated from synthetic or public data.
2. Inspect prompt and response records.
3. Inspect tool arguments and return values.
4. Inspect captured file paths and file diffs.
5. Remove secrets, tokens, private URLs, personal data, and proprietary text.
6. Prefer sharing a minimal focused trace rather than a full run.

## Security reporting

For vulnerabilities, follow the process in [SECURITY.md](../SECURITY.md).

Do not open a public issue with exploit details, secrets, private traces, or customer data.
