from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path
from typing import Any

from .api import install, record, replay
from .graph_ir import build_graph_ir, filter_graph_ir, load_trace_records, summarize_graph_ir
from .scaffold import SUPPORTED_TOOL_STYLES, scaffold_integration
from .visualize import graph_ir_to_mermaid, write_html_graph, write_mermaid_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="replay")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_cmd = subparsers.add_parser("record", help="Record a Python script run.")
    record_cmd.add_argument("run_id", help="Run id to write.")
    _add_common_script_arguments(record_cmd)

    replay_cmd = subparsers.add_parser("replay", help="Replay a Python script from a recorded run.")
    replay_cmd.add_argument("base_run", help="Run id to replay from.")
    replay_cmd.add_argument("--semantic-fallback", action="store_true")
    _add_common_script_arguments(replay_cmd)

    fork_cmd = subparsers.add_parser("fork", help="Replay a script from a breakpoint with an override.")
    fork_cmd.add_argument("base_run", help="Run id to replay from.")
    fork_cmd.add_argument("--breakpoint-record-uid", required=True)
    fork_override_group = fork_cmd.add_mutually_exclusive_group(required=True)
    fork_override_group.add_argument("--override-output", default=None)
    fork_override_group.add_argument("--override-message-json", default=None)
    fork_override_group.add_argument("--override-input-json", default=None)
    fork_cmd.add_argument("--fork-run", default=None)
    fork_cmd.add_argument("--semantic-fallback", action="store_true")
    _add_common_script_arguments(fork_cmd)

    python_cmd = subparsers.add_parser("python", help="Run a Python script under replay instrumentation.")
    mode_group = python_cmd.add_mutually_exclusive_group()
    mode_group.add_argument("--run-id", default=None, help="Record the script into this run id.")
    mode_group.add_argument("--base-run", default=None, help="Replay the script from this base run id.")
    python_cmd.add_argument("--log-dir", type=Path, default=None)
    python_cmd.add_argument("--breakpoint-record-uid", default=None)
    python_cmd.add_argument("--override-output", default=None)
    python_cmd.add_argument("--override-message-json", default=None)
    python_cmd.add_argument("--override-input-json", default=None)
    python_cmd.add_argument("--fork-run", default=None)
    python_cmd.add_argument("--semantic-fallback", action="store_true")
    python_cmd.add_argument("--no-semantic", action="store_true", help="Disable AST provenance instrumentation.")
    python_cmd.add_argument("--project-root", type=Path, default=None)
    python_cmd.add_argument("--include", action="append", default=None)
    python_cmd.add_argument("--exclude", action="append", default=None)
    python_cmd.add_argument("script", type=Path)
    python_cmd.add_argument("script_args", nargs=argparse.REMAINDER)

    graph = subparsers.add_parser("graph", help="Build and export graph views from replay traces.")
    graph_sub = graph.add_subparsers(dest="graph_command", required=True)

    for name in ("summary", "export-ir", "mermaid", "html"):
        graph_cmd = graph_sub.add_parser(name)
        graph_cmd.add_argument("paths", nargs="+", type=Path)
        graph_cmd.add_argument("--fork", action="append", default=[], type=Path)
        graph_cmd.add_argument("--focus", default=None)
        graph_cmd.add_argument("--direction", choices=("upstream", "downstream", "both"), default="both")
        graph_cmd.add_argument("--max-depth", type=int, default=None)
        graph_cmd.add_argument("--title", default=None)
        graph_cmd.add_argument("--output", type=Path, default=None)
        graph_cmd.add_argument("--group-by", choices=("none", "path", "span", "run"), default="path")
        if name == "html":
            graph_cmd.add_argument("--asset-mode", choices=("inline", "vendored"), default="inline")
            graph_cmd.add_argument("--renderer", choices=("svg", "xyflow"), default="svg")

    scaffold = subparsers.add_parser("scaffold", help="Generate Replay integration wrapper skeletons.")
    scaffold_sub = scaffold.add_subparsers(dest="scaffold_command", required=True)
    integration = scaffold_sub.add_parser("integration", help="Generate a generic agent integration wrapper.")
    integration.add_argument("--name", required=True, help="Integration package name.")
    integration.add_argument(
        "--output-dir",
        type=Path,
        default=Path("integrations"),
        help="Directory where the integration package should be created.",
    )
    integration.add_argument(
        "--tool-style",
        choices=SUPPORTED_TOOL_STYLES,
        default="none",
        help="Initial adapter template style.",
    )
    integration.add_argument(
        "--framework",
        choices=("auto", "none", "langchain", "langgraph", "both"),
        default="auto",
        help="Framework patches installed by generated runner.py.",
    )
    integration.add_argument("--target-root", type=Path, default=None, help="Target project root used for detection/defaults.")
    integration.add_argument("--detect", action="store_true", help="Detect likely target entries and write replay_target.json.")
    integration.add_argument("--entry", default=None, help="Default target entry to write into replay_target.json.")
    integration.add_argument(
        "--entry-kind",
        choices=("auto", "script", "module", "import", "factory", "runnable", "langgraph-json", "asgi"),
        default="auto",
    )
    integration.add_argument(
        "--method",
        choices=("auto", "call", "invoke", "ainvoke", "stream", "astream", "serve"),
        default="auto",
    )
    integration.add_argument("--graph", default=None, help="Default LangGraph graph name.")
    integration.add_argument("--force", action="store_true", help="Overwrite existing generated files.")

    return parser


def _add_common_script_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--no-semantic", action="store_true", help="Disable AST provenance instrumentation.")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--include", action="append", default=None)
    parser.add_argument("--exclude", action="append", default=None)
    parser.add_argument("script", type=Path)
    parser.add_argument("script_args", nargs=argparse.REMAINDER)


def main() -> None:
    args = build_parser().parse_args()
    if args.command in {"record", "replay", "fork"}:
        _run_script_command(args)
        return
    if args.command == "python":
        _run_python_command(args)
        return
    if args.command == "graph":
        _run_graph_command(args)
        return
    if args.command == "scaffold":
        _run_scaffold_command(args)
        return
    raise ValueError(f"Unsupported command: {args.command}")


def _run_script_command(args: argparse.Namespace) -> None:
    python_args = argparse.Namespace(
        run_id=args.run_id if args.command == "record" else None,
        base_run=args.base_run if args.command in {"replay", "fork"} else None,
        log_dir=args.log_dir,
        breakpoint_record_uid=getattr(args, "breakpoint_record_uid", None),
        override_output=getattr(args, "override_output", None),
        override_message_json=getattr(args, "override_message_json", None),
        override_input_json=getattr(args, "override_input_json", None),
        fork_run=getattr(args, "fork_run", None),
        semantic_fallback=getattr(args, "semantic_fallback", False),
        no_semantic=args.no_semantic,
        project_root=args.project_root,
        include=args.include,
        exclude=args.exclude,
        script=args.script,
        script_args=_strip_remainder_separator(args.script_args),
    )
    _run_python_command(python_args)


def _strip_remainder_separator(args: list[str]) -> list[str]:
    if args and args[0] == "--":
        return args[1:]
    return args


def _run_python_command(args: argparse.Namespace) -> None:
    override_message = json.loads(args.override_message_json) if args.override_message_json else None
    override_input = json.loads(args.override_input_json) if args.override_input_json else None
    install(
        semantic=not args.no_semantic,
        project_root=args.project_root,
        include=args.include,
        exclude=args.exclude,
    )

    script_path = args.script
    script_argv = [str(script_path), *args.script_args]
    old_argv = sys.argv
    try:
        sys.argv = script_argv
        if args.base_run:
            with replay(
                base_run=args.base_run,
                breakpoint_record_uid=args.breakpoint_record_uid,
                override_output=args.override_output,
                override_input=override_input,
                override_message=override_message,
                log_dir=args.log_dir,
                fork_run=args.fork_run,
                semantic_fallback=args.semantic_fallback,
            ):
                runpy.run_path(str(script_path), run_name="__main__")
            return
        run_id = args.run_id or script_path.stem
        with record(run_id, log_dir=args.log_dir):
            runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv


def _run_graph_command(args: argparse.Namespace) -> None:
    ir = _build_cli_graph_ir(args)

    if args.graph_command == "summary":
        print(json.dumps(summarize_graph_ir(ir), indent=2, sort_keys=True))
        return
    if args.graph_command == "export-ir":
        if args.output is None:
            raise ValueError("--output is required for export-ir")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(ir, indent=2, sort_keys=True), encoding="utf-8")
        return
    if args.graph_command == "mermaid":
        if args.output is None:
            print(graph_ir_to_mermaid(ir, group_by=args.group_by), end="")
            return
        write_mermaid_markdown(ir, args.output, title=args.title, group_by=args.group_by)
        return
    if args.graph_command == "html":
        if args.output is None:
            raise ValueError("--output is required for html")
        write_html_graph(ir, args.output, title=args.title, asset_mode=args.asset_mode, renderer=args.renderer)
        return
    raise ValueError(f"Unsupported graph command: {args.graph_command}")


def _build_cli_graph_ir(args: argparse.Namespace) -> dict[str, Any]:
    all_paths = [*args.paths, *args.fork]
    records = load_trace_records(all_paths)
    ir = build_graph_ir(records, title=args.title)
    if args.focus:
        ir = filter_graph_ir(
            ir,
            focus=args.focus,
            direction=args.direction,
            max_depth=args.max_depth,
        )
    return ir


def _run_scaffold_command(args: argparse.Namespace) -> None:
    if args.scaffold_command != "integration":
        raise ValueError(f"Unsupported scaffold command: {args.scaffold_command}")
    written = scaffold_integration(
        name=args.name,
        output_dir=args.output_dir,
        tool_style=args.tool_style,
        framework=args.framework,
        target_root=args.target_root,
        entry=args.entry,
        entry_kind=args.entry_kind,
        method=args.method,
        detect=args.detect,
        graph=args.graph,
        force=args.force,
    )
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
