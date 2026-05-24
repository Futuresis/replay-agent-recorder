from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_SANDBOX_DIR = AGENT_DIR / "sandbox"
SANDBOX_BASE_DIR = AGENT_DIR / "sandbox_base"


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent4 under replay record/replay.")
    parser.add_argument("command", nargs="?", default=None)
    parser.add_argument("--mode", choices=["record", "replay"], default="record")
    parser.add_argument("--run-id", default="agent4-comprehensive")
    parser.add_argument("--log-dir", type=Path, default=ROOT_DIR / "replay" / "runs")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--real-llm", action="store_true", help="Use the configured real OpenAI-compatible endpoint.")
    parser.add_argument("--breakpoint-record-uid", default=None)
    parser.add_argument("--override-output", default=None)
    parser.add_argument("--override-input-json", default=None)
    parser.add_argument("--override-message-json", default=None)
    parser.add_argument("--fork-run", default=None)
    parser.add_argument("--semantic-fallback", action="store_true")
    args = parser.parse_args()

    if not args.real_llm:
        from .fake_llm import install_fake_llm

        install_fake_llm()

    import replay

    replay.install(
        project_root=ROOT_DIR,
        include=("test_agent/agent4/*.py",),
    )

    from .main import DEFAULT_COMMAND, run
    from .tools import MAPPING_TOOLS, WorkspaceToolClient

    command = args.command or DEFAULT_COMMAND
    output_path = resolve_project_path(args.output)
    log_dir = resolve_project_path(args.log_dir)
    override_input = parse_json_object(args.override_input_json, "--override-input-json")
    override_message = parse_json_object(args.override_message_json, "--override-message-json")

    mapping_adapter = replay.MappingToolAdapter(MAPPING_TOOLS, namespace="agent4-map", version="v1")
    workspace_client = WorkspaceToolClient(DEFAULT_SANDBOX_DIR)

    with replay.managed_sandbox(base_root=SANDBOX_BASE_DIR, work_root=DEFAULT_SANDBOX_DIR) as capture:
        method_adapter = replay.MethodToolAdapter(
            workspace_client,
            "call_tool",
            namespace="agent4-workspace",
            version="v1",
            fs_capture=capture,
        )
        mapping_adapter.install()
        method_adapter.install()
        try:
            if args.mode == "record":
                with replay.record(args.run_id, log_dir=log_dir):
                    result = await run(
                        command,
                        output_path=output_path,
                        tool_client=workspace_client,
                        allow_fake_llm=not args.real_llm,
                    )
            else:
                with replay.replay(
                    base_run=args.run_id,
                    breakpoint_record_uid=args.breakpoint_record_uid,
                    override_output=args.override_output,
                    override_input=override_input,
                    override_message=override_message,
                    log_dir=log_dir,
                    fork_run=args.fork_run,
                    semantic_fallback=args.semantic_fallback,
                ):
                    result = await run(
                        command,
                        output_path=output_path,
                        tool_client=workspace_client,
                        allow_fake_llm=not args.real_llm,
                    )
        finally:
            method_adapter.uninstall()
            mapping_adapter.uninstall()

    print(f"agent4 {args.mode} ok")
    print(f"report: {result['output_path']}")
    print(f"synthesis: {result['llm']['synthesis']}")


def resolve_project_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.is_absolute() else ROOT_DIR / path


def parse_json_object(raw: str | None, flag_name: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{flag_name} must decode to a JSON object.")
    return parsed


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

