# Security Policy

Replay is currently an alpha project. Please treat recorded traces as sensitive
debug artifacts, not as public logs.

## Sensitive Trace Data

Replay traces may contain:

- Prompts and system instructions.
- LLM outputs.
- Tool parameters and return values.
- Local file contents or diffs captured during filesystem replay.
- Local file paths.
- Error messages, stack traces, and other diagnostic information.

Do not publish, attach, or commit traces from real business, customer, private,
or proprietary workflows unless you have reviewed and redacted them.

## Reporting a Vulnerability

If you find a vulnerability, please report it privately using GitHub security
advisories when available. If private reporting is not available for your fork
or mirror, open a minimal public issue that does not include exploit details,
secrets, private traces, or customer data, and ask the maintainers to establish
a private channel.

Please include:

- The affected Replay version or commit.
- A short description of the impact.
- Reproduction steps using synthetic data.
- Whether the issue exposes trace contents, filesystem data, credentials, or
  remote execution behavior.

## Supported Versions

Security fixes are expected to target the latest commit on the main development
branch until the project starts publishing stable releases.

## Security Notes for Integrations

Replay can patch LLM calls, wrap tool calls, and capture sandboxed filesystem
effects. Integration authors should keep capture scopes narrow, avoid recording
secrets in tool arguments, and prefer synthetic traces for documentation,
tests, and bug reports.
