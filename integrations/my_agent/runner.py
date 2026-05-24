from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path
from typing import Any

import replay
from replay.integration import add_replay_arguments, config_from_args, replay_session

try:
    from .tool_adapter import build_adapters
except ImportError:
    from tool_adapter import build_adapters


DEFAULT_PROJECT_ROOT = Path.cwd()
DEFAULT_RUN_ID = "my_agent-run"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an agent under Replay instrumentation.")
    add_replay_arguments(parser, default_run_id=DEFAULT_RUN_ID)
    parser.add_argument(
        "--target-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="Repository or working directory of the wrapped agent.",
    )
    parser.add_argument(
        "--target-script",
        type=Path,
        required=True,
        help="Python script to execute as the wrapped agent entry point.",
    )
    parser.add_argument(
        "target_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to --target-script. Prefix with -- when needed.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    target_root = args.target_root.resolve()
    target_script = _resolve_target_script(target_root, args.target_script)
    target_args = _clean_remainder(args.target_args)
    config = config_from_args(args, default_run_id=DEFAULT_RUN_ID, default_project_root=target_root)

    if config.mode != "none":
        replay.install(
            semantic=config.semantic,
            project_root=config.project_root,
            include=config.include,
            exclude=config.exclude,
        )

    adapters = build_adapters(args)
    for adapter in adapters:
        adapter.install()

    old_argv = sys.argv[:]
    old_path = sys.path[:]
    try:
        sys.path.insert(0, str(target_root))
        sys.argv = [str(target_script), *target_args]
        with replay_session(config):
            runpy.run_path(str(target_script), run_name="__main__")
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path
        for adapter in reversed(adapters):
            adapter.uninstall()


def _resolve_target_script(target_root: Path, script: Path) -> Path:
    resolved = script if script.is_absolute() else target_root / script
    if not resolved.exists():
        raise SystemExit(f"target script does not exist: {resolved}")
    return resolved


def _clean_remainder(values: list[str]) -> list[str]:
    if values and values[0] == "--":
        return values[1:]
    return values


if __name__ == "__main__":
    main()
