from __future__ import annotations

import argparse
import inspect
import json
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Iterator, Literal, TypeVar

from .api import install, record, replay


ReplayMode = Literal["record", "replay", "none"]
T = TypeVar("T")


@dataclass(frozen=True)
class ReplayRunConfig:
    """Normalized replay runtime options for integration wrapper scripts."""

    mode: ReplayMode = "record"
    run_id: str | None = None
    base_run: str | None = None
    base_run_explicit: bool = False
    fork_run: str | None = None
    log_dir: Path | None = None
    overwrite: bool = True
    breakpoint_record_uid: str | None = None
    override_output: str | None = None
    override_input: dict[str, Any] | None = None
    override_message: dict[str, Any] | None = None
    semantic_fallback: bool = False
    semantic: bool = True
    project_root: Path | None = None
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None


def add_replay_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_mode: ReplayMode = "record",
    default_run_id: str | None = None,
    default_log_dir: Path | str | None = None,
    include_install_options: bool = True,
) -> None:
    """Add the standard replay integration flags to an existing parser."""

    parser.add_argument(
        "--replay-mode",
        choices=("record", "replay", "none"),
        default=default_mode,
        help="Replay mode for the wrapped agent.",
    )
    parser.add_argument(
        "--run-id",
        default=default_run_id,
        help="Run id used in record mode. Also used as the replay base if --base-run is omitted.",
    )
    parser.add_argument(
        "--base-run",
        default=None,
        help="Base run id used in replay mode. Defaults to --run-id.",
    )
    parser.add_argument("--fork-run", default=None, help="Optional fork run id for replay mode.")
    parser.add_argument(
        "--breakpoint-record-uid",
        default=None,
        help="LLM record uid where replay should branch or apply an override.",
    )
    parser.add_argument("--override-output", default=None, help="Replacement assistant content at the breakpoint.")
    parser.add_argument("--override-input-json", default=None, help="JSON object merged into the breakpoint request.")
    parser.add_argument("--override-input-file", type=Path, default=None, help="File containing a JSON object for --override-input-json.")
    parser.add_argument("--override-message-json", default=None, help="JSON assistant message object at the breakpoint.")
    parser.add_argument(
        "--override-message-file",
        type=Path,
        default=None,
        help="File containing a JSON object for --override-message-json.",
    )
    parser.add_argument(
        "--replay-log-dir",
        type=Path,
        default=Path(default_log_dir) if default_log_dir is not None else None,
        help="Directory for Replay JSONL runs.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite an existing record-mode run file.",
    )
    parser.add_argument(
        "--semantic-fallback",
        action="store_true",
        help="Enable semantic fallback matching in replay mode.",
    )

    if include_install_options:
        parser.add_argument("--no-semantic", action="store_true", help="Disable AST provenance instrumentation.")
        parser.add_argument("--project-root", type=Path, default=None, help="Project root for AST provenance instrumentation.")
        parser.add_argument("--include", action="append", default=None, help="Include glob for AST instrumentation; repeatable.")
        parser.add_argument("--exclude", action="append", default=None, help="Exclude glob for AST instrumentation; repeatable.")


def config_from_args(
    args: argparse.Namespace,
    *,
    default_run_id: str | None = None,
    default_log_dir: Path | str | None = None,
    default_project_root: Path | str | None = None,
) -> ReplayRunConfig:
    """Build and validate a ReplayRunConfig from argparse output."""

    mode = getattr(args, "replay_mode", "record")
    if mode not in {"record", "replay", "none"}:
        raise ValueError(f"Unsupported replay mode: {mode!r}")

    run_id = getattr(args, "run_id", None) or default_run_id
    base_run_arg = getattr(args, "base_run", None)
    base_run = base_run_arg or run_id
    base_run_explicit = base_run_arg is not None
    log_dir = getattr(args, "replay_log_dir", None)
    if log_dir is None and default_log_dir is not None:
        log_dir = Path(default_log_dir)

    override_input = load_json_object_option(
        getattr(args, "override_input_json", None),
        getattr(args, "override_input_file", None),
        label="override-input",
    )
    override_message = load_json_object_option(
        getattr(args, "override_message_json", None),
        getattr(args, "override_message_file", None),
        label="override-message",
    )
    override_output = getattr(args, "override_output", None)
    _validate_run_options(
        mode=mode,
        run_id=run_id,
        base_run=base_run,
        breakpoint_record_uid=getattr(args, "breakpoint_record_uid", None),
        override_output=override_output,
        override_input=override_input,
        override_message=override_message,
    )

    project_root = getattr(args, "project_root", None)
    if project_root is None and default_project_root is not None:
        project_root = Path(default_project_root)

    return ReplayRunConfig(
        mode=mode,
        run_id=run_id,
        base_run=base_run,
        base_run_explicit=base_run_explicit,
        fork_run=getattr(args, "fork_run", None),
        log_dir=Path(log_dir) if log_dir is not None else None,
        overwrite=not bool(getattr(args, "no_overwrite", False)),
        breakpoint_record_uid=getattr(args, "breakpoint_record_uid", None),
        override_output=override_output,
        override_input=override_input,
        override_message=override_message,
        semantic_fallback=bool(getattr(args, "semantic_fallback", False)),
        semantic=not bool(getattr(args, "no_semantic", False)),
        project_root=Path(project_root) if project_root is not None else None,
        include=_tuple_or_none(getattr(args, "include", None)),
        exclude=_tuple_or_none(getattr(args, "exclude", None)),
    )


def load_json_object_option(
    raw: str | None,
    file_path: Path | str | None,
    *,
    label: str,
) -> dict[str, Any] | None:
    """Load one JSON object from either a direct string option or a file option."""

    if raw and file_path:
        raise ValueError(f"--{label}-json and --{label}-file are mutually exclusive.")
    if file_path:
        try:
            raw = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Failed to read --{label}-file {file_path!r}: {exc}") from exc
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--{label} must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"--{label} must decode to a JSON object.")
    return value


def install_from_config(config: ReplayRunConfig) -> None:
    """Install replay instrumentation using normalized integration options."""

    if config.mode == "none":
        return
    install(
        semantic=config.semantic,
        project_root=config.project_root,
        include=config.include,
        exclude=config.exclude,
    )


@contextmanager
def replay_session(config: ReplayRunConfig) -> Iterator[None]:
    """Open a record/replay/no-op session for a wrapper script."""

    if config.mode == "none":
        with nullcontext():
            yield
        return

    if config.mode == "record":
        if not config.run_id:
            raise ValueError("run_id is required in record mode.")
        with record(config.run_id, log_dir=config.log_dir, overwrite=config.overwrite):
            yield
        return

    if config.mode == "replay":
        if not config.base_run:
            raise ValueError("base_run is required in replay mode.")
        with replay(
            base_run=config.base_run,
            breakpoint_record_uid=config.breakpoint_record_uid,
            override_output=config.override_output,
            override_input=config.override_input,
            override_message=config.override_message,
            log_dir=config.log_dir,
            fork_run=config.fork_run,
            semantic_fallback=config.semantic_fallback,
        ):
            yield
        return

    raise ValueError(f"Unsupported replay mode: {config.mode!r}")


def run_with_replay(
    target: Callable[[], T],
    config: ReplayRunConfig,
    *,
    install_instrumentation: bool = True,
) -> T:
    """Run a synchronous target inside the configured replay session."""

    if install_instrumentation:
        install_from_config(config)
    with replay_session(config):
        return target()


async def run_async_with_replay(
    target: Callable[[], Awaitable[T] | T],
    config: ReplayRunConfig,
    *,
    install_instrumentation: bool = True,
) -> T:
    """Run a synchronous or asynchronous target inside the configured replay session."""

    if install_instrumentation:
        install_from_config(config)
    with replay_session(config):
        result = target()
        if inspect.isawaitable(result):
            return await result
        return result


def _validate_run_options(
    *,
    mode: str,
    run_id: str | None,
    base_run: str | None,
    breakpoint_record_uid: str | None,
    override_output: str | None,
    override_input: dict[str, Any] | None,
    override_message: dict[str, Any] | None,
) -> None:
    if mode == "record" and not run_id:
        raise ValueError("--run-id is required in record mode unless a wrapper default is provided.")
    if mode == "replay" and not base_run:
        raise ValueError("--base-run or --run-id is required in replay mode.")

    override_count = sum(
        value is not None
        for value in (override_output, override_input, override_message)
    )
    if override_count > 1:
        raise ValueError(
            "--override-output, --override-input-json/--override-input-file, and "
            "--override-message-json/--override-message-file are mutually exclusive."
        )
    if override_count and not breakpoint_record_uid:
        raise ValueError("--breakpoint-record-uid is required when using an override.")


def _tuple_or_none(values: Iterable[str] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(values)
