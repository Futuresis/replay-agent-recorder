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
DEFAULT_RUN_ID = "swe_agent-run"
DEFAULT_TARGET_CONFIG = Path(__file__).with_name("replay_target.json")


def build_parser() -> argparse.ArgumentParser:
    defaults = load_replay_target_defaults(DEFAULT_TARGET_CONFIG)
    defaults.setdefault("framework", "both")
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
