from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

from .entrypoints import parse_entry_ref
from .scaffold_detect import (
    EntryCandidate,
    ScaffoldDetectionResult,
    detect_integration_targets,
    write_replay_target_config,
)


SUPPORTED_TOOL_STYLES = ("none", "mapping", "method", "class-method")


def scaffold_integration(
    *,
    name: str,
    output_dir: Path | str,
    tool_style: str = "none",
    framework: str = "auto",
    target_root: Path | str | None = None,
    entry: str | None = None,
    entry_kind: str = "auto",
    method: str = "auto",
    detect: bool = False,
    graph: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Create a generic Replay integration wrapper skeleton."""

    if tool_style not in SUPPORTED_TOOL_STYLES:
        raise ValueError(f"tool_style must be one of {', '.join(SUPPORTED_TOOL_STYLES)}.")
    framework = _normalize_framework(framework)

    package_name = _normalize_package_name(name)
    target_dir = Path(output_dir) / package_name
    target_dir.mkdir(parents=True, exist_ok=True)
    detection = detect_integration_targets(target_root) if detect and target_root is not None else None
    replay_target_result = _replay_target_result(
        target_root=target_root,
        entry=entry,
        entry_kind=entry_kind,
        method=method,
        framework=framework,
        graph=graph,
        detection=detection,
    )

    files = {
        target_dir / "__init__.py": '"""Replay integration wrapper package."""\n',
        target_dir / "runner.py": _runner_template(
            package_name,
            framework=framework,
        ),
        target_dir / "tool_adapter.py": _tool_adapter_template(tool_style),
        target_dir / "README.md": _readme_template(
            package_name,
            tool_style,
            framework=framework,
            detection=replay_target_result,
        ),
    }

    written: list[Path] = []
    for path, content in files.items():
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}")
        path.write_text(content, encoding="utf-8")
        written.append(path)
    replay_target_path = target_dir / "replay_target.json"
    if replay_target_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {replay_target_path}")
    write_replay_target_config(replay_target_path, replay_target_result, target_root=target_root)
    written.append(replay_target_path)
    return written


def _normalize_package_name(name: str) -> str:
    normalized = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("Integration name must contain at least one alphanumeric character.")
    if normalized[0].isdigit():
        normalized = f"integration_{normalized}"
    return normalized


def _runner_template(
    package_name: str,
    *,
    framework: str,
) -> str:
    return dedent(
        f'''\
        from __future__ import annotations

        import argparse
        import sys
        from dataclasses import replace
        from pathlib import Path

        RUNNER_DIR = Path(__file__).resolve().parent


        def _bootstrap_replay_package_path() -> None:
            for search_root in (RUNNER_DIR, Path.cwd()):
                for candidate in (search_root, *search_root.parents):
                    if (candidate / "replay" / "__init__.py").exists():
                        candidate_str = str(candidate)
                        if candidate_str not in sys.path:
                            sys.path.insert(0, candidate_str)
                        return


        _bootstrap_replay_package_path()

        if str(RUNNER_DIR) not in sys.path:
            sys.path.insert(0, str(RUNNER_DIR))

        import replay
        from replay.integration import add_replay_arguments, config_from_args, replay_session
        from replay.entrypoints import (
            add_target_entry_arguments,
            framework_install_flags,
            load_replay_target_defaults,
            target_env_files_from_args,
            target_entry_from_args,
            target_invocation_from_args,
            run_target_entry_blocking,
            print_entry_result,
        )
        from replay.target_env import target_environment

        try:
            from .tool_adapter import build_adapters
        except ImportError:
            from tool_adapter import build_adapters


        DEFAULT_PROJECT_ROOT = RUNNER_DIR
        DEFAULT_RUN_ID = "{package_name}-run"
        DEFAULT_TARGET_CONFIG = Path(__file__).with_name("replay_target.json")


        def build_parser() -> argparse.ArgumentParser:
            defaults = load_replay_target_defaults(DEFAULT_TARGET_CONFIG)
            defaults.setdefault("framework", "{framework}")
            parser = argparse.ArgumentParser(description="Run an agent under Replay instrumentation.")
            add_replay_arguments(parser, default_run_id=DEFAULT_RUN_ID)
            add_target_entry_arguments(parser, defaults=defaults)
            return parser


        def main() -> None:
            args = build_parser().parse_args()
            entry = target_entry_from_args(args)
            invocation = target_invocation_from_args(args)
            config = config_from_args(args, default_run_id=DEFAULT_RUN_ID, default_project_root=entry.target_root)

            with target_environment(
                target_root=entry.target_root,
                target_cwd=entry.target_cwd,
                chdir=not args.no_chdir,
                pythonpath=getattr(args, "pythonpath", ()) or (),
                env_files=target_env_files_from_args(args, entry),
                env_override=getattr(args, "env_override", False),
                include_src=not getattr(args, "no_src_pythonpath", False),
            ):
                if config.mode != "none":
                    langchain, langgraph = framework_install_flags(args.framework)
                    replay.install(
                        semantic=config.semantic,
                        project_root=config.project_root,
                        include=config.include,
                        exclude=config.exclude,
                        langchain=langchain,
                        langgraph=langgraph,
                    )

                adapters = build_adapters(args)
                for adapter in adapters:
                    adapter.install()

                try:
                    invocation = replace(invocation, replay_config=config)
                    if entry.kind == "asgi" or invocation.serve or entry.method == "serve":
                        result = run_target_entry_blocking(entry, invocation)
                    else:
                        with replay_session(config):
                            result = run_target_entry_blocking(entry, invocation)
                        print_entry_result(
                            result,
                            output_file=getattr(args, "result_output_file", None),
                            print_result=not getattr(args, "no_print_result", False),
                        )
                finally:
                    for adapter in reversed(adapters):
                        adapter.uninstall()


        if __name__ == "__main__":
            main()
        '''
    )


def _tool_adapter_template(tool_style: str) -> str:
    if tool_style == "none":
        body = '''\
        def build_adapters(args: Any) -> list[replay.ToolAdapter]:
            """Return Replay tool adapters to install before the target starts."""

            return []
        '''
    elif tool_style == "mapping":
        body = '''\
        def build_adapters(args: Any) -> list[replay.ToolAdapter]:
            """Wrap a mutable mapping of tool name to callable.

            Replace the import and registry expression with the wrapped agent's
            actual tool registry. Tool functions should accept one dict argument.
            """

            # from target_agent.tools import TOOL_REGISTRY
            # return [replay.MappingToolAdapter(TOOL_REGISTRY, namespace="local", version="v1")]
            return []
        '''
    elif tool_style == "method":
        body = '''\
        def build_adapters(args: Any) -> list[replay.ToolAdapter]:
            """Wrap an object method shaped like call_tool(name, arguments)."""

            # from target_agent.tools import tool_client
            # return [replay.MethodToolAdapter(tool_client, "call_tool", namespace="local", version="v1")]
            return []
        '''
    else:
        body = '''\
        def build_adapters(args: Any) -> list[replay.ToolAdapter]:
            """Patch a framework-owned class method shaped like call_tool(name, arguments)."""

            # from target_agent.tools import ToolClient
            # return [
            #     replay.ClassMethodToolAdapter(
            #         ToolClient,
            #         "call_tool",
            #         namespace="local",
            #         version="v1",
            #         tool_filter=None,
            #     )
            # ]
            return []
        '''

    header = dedent(
        '''\
        from __future__ import annotations

        from typing import Any

        import replay
        '''
    )
    return f"{header}\n\n{dedent(body)}"


def _readme_template(
    package_name: str,
    tool_style: str,
    *,
    framework: str,
    detection: ScaffoldDetectionResult,
) -> str:
    detected_section = _detected_readme_section(detection)
    body = f"""# {package_name} Replay Integration

This directory is a Replay wrapper skeleton. Keep agent-specific logic in
`tool_adapter.py` only for custom non-LangChain tool boundaries; keep
standard record/replay CLI behavior in `runner.py`.

## Run

Script:
```bash
python runner.py --replay-mode record --run-id {package_name}-demo --target-root /path/to/agent --entry script:src/main.py -- --task hello
python runner.py --replay-mode replay --base-run {package_name}-demo --target-root /path/to/agent --entry script:src/main.py -- --task hello
```

Module:
```bash
python runner.py --target-root /path/to/agent --entry module:my_agent.cli -- --task hello
```

Runnable:
```bash
python runner.py --target-root /path/to/agent --entry my_agent.graph:agent --input-json '{{"messages":[{{"role":"user","content":"hello"}}]}}'
```

Factory:
```bash
python runner.py --target-root /path/to/agent --entry factory:my_agent.graph:build_agent --input-json '{{"messages":[{{"role":"user","content":"hello"}}]}}'
```

LangGraph JSON:
```bash
python runner.py --target-root /path/to/agent --entry 'langgraph.json#GraphName' --input-json '{{"messages":[{{"role":"user","content":"hello"}}]}}'
```

Advanced server mode:
```bash
python runner.py --target-root /path/to/agent --entry asgi:agent.webapp:app --serve --host 127.0.0.1 --port 8000 --run-id-template '{{method}}-{{path}}-{{request_id}}'
python runner.py --target-root /path/to/agent --entry 'langgraph.json#http' --serve
```

## Fill In

- `tool_adapter.py`: LangChain/LangGraph projects usually do not need
  edits here. Edit it only for custom non-LangChain tool boundaries.
- `runner.py`: do not edit this file for ordinary script, module,
  runnable, factory, or ASGI server targets. Do not duplicate Replay's standard
  record/replay flags.
- Server mode wraps each HTTP request in its own Replay session. If a
  target starts background work after returning the response, enable the
  auto-session/bootstrap path described in P4 docs.

Selected tool style: `{tool_style}`.
Framework patch mode: `{framework}`.

{detected_section}

For the complete scaffold rules, see `docs/integration-scaffold.md` in
the Replay repository root.
"""
    return body.strip() + "\n"


def _normalize_framework(
    framework: str,
) -> str:
    if framework not in {"auto", "none", "langchain", "langgraph", "both"}:
        raise ValueError("framework must be one of auto, none, langchain, langgraph, both.")
    return framework


def _replay_target_result(
    *,
    target_root: Path | str | None,
    entry: str | None,
    entry_kind: str,
    method: str,
    framework: str,
    graph: str | None,
    detection: ScaffoldDetectionResult | None,
) -> ScaffoldDetectionResult:
    root = Path(target_root).resolve() if target_root is not None else Path.cwd()
    candidates = tuple(detection.candidates) if detection is not None else ()
    selected = detection.selected if detection is not None else None
    if entry is not None:
        resolved = parse_entry_ref(entry)
        selected = EntryCandidate(
            entry=entry,
            kind=resolved.kind if entry_kind == "auto" else entry_kind,  # type: ignore[arg-type]
            method=method,  # type: ignore[arg-type]
            confidence=1.0,
            reason="Explicit scaffold entry",
            graph_name=graph or resolved.graph,
            framework=framework,
        )
        candidates = (selected, *candidates)
    elif selected is not None and framework != "auto":
        selected = EntryCandidate(
            entry=selected.entry,
            kind=selected.kind,
            method=selected.method,
            confidence=selected.confidence,
            reason=selected.reason,
            source_path=selected.source_path,
            symbol=selected.symbol,
            graph_name=selected.graph_name,
            framework=framework,
            requires_input=selected.requires_input,
            requires_factory_config=selected.requires_factory_config,
            metadata=selected.metadata,
        )
    return ScaffoldDetectionResult(
        target_root=root,
        candidates=candidates,
        selected=selected,
        warnings=tuple(detection.warnings) if detection is not None else (),
    )

def _detected_readme_section(result: ScaffoldDetectionResult) -> str:
    if not result.candidates:
        return (
            "## Detected target entry\n\n"
            "No target entry was detected automatically. Pass `--entry` manually, for example:\n\n"
            "```bash\n"
            "python runner.py --entry my_agent.graph:agent --input-json '{\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}'\n"
            "```"
        )

    default_line = "`None`"
    if result.selected is not None:
        default_line = f"`{result.selected.entry}`"
    rows = [
        "| Confidence | Entry | Kind | Reason |",
        "|---:|---|---|---|",
    ]
    for candidate in result.candidates:
        rows.append(
            f"| {candidate.confidence:.2f} | `{candidate.entry}` | `{candidate.kind}` | {candidate.reason} |"
        )
    lines = [
        "## Detected target entry",
        "",
        f"Default entry: {default_line}",
        "",
        "Detected candidates:",
        "",
        *rows,
    ]
    return "\n".join(lines)
