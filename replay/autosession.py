from __future__ import annotations

import contextvars
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from .asgi import format_run_id, sanitize_run_id_part
from .context import get_current_session
from .integration import ReplayRunConfig, replay_session


@dataclass(frozen=True)
class AutoSessionConfig:
    mode: Literal["record", "replay", "none"] = "record"
    run_id_template: str = "{graph}-{thread_id}-{run_id}"
    base_run: str | None = None
    log_dir: Path | None = None
    overwrite: bool = True
    semantic_fallback: bool = False


_auto_session_config: contextvars.ContextVar[AutoSessionConfig | None] = contextvars.ContextVar(
    "replay_auto_session_config",
    default=None,
)


@contextmanager
def auto_session(config: AutoSessionConfig) -> Iterator[None]:
    token = enable_auto_session(config)
    try:
        yield
    finally:
        disable_auto_session(token)


def enable_auto_session(config: AutoSessionConfig) -> contextvars.Token:
    """Enable automatic replay sessions for runnable invocations."""

    return _auto_session_config.set(config)


def disable_auto_session(token: contextvars.Token) -> None:
    """Restore the previous automatic session configuration."""

    _auto_session_config.reset(token)


@contextmanager
def maybe_replay_session_for_config(
    *,
    runnable: Any,
    input_value: Any,
    config: dict[str, Any] | None,
    graph_name: str | None = None,
) -> Iterator[None]:
    """Open replay_session if no current session and auto_session is enabled."""

    auto_config = _auto_session_config.get()
    if get_current_session() is not None or auto_config is None or auto_config.mode == "none":
        yield
        return

    run_id = derive_run_id(
        auto_config.run_id_template,
        runnable=runnable,
        input_value=input_value,
        config=config,
        graph_name=graph_name,
    )
    session_config = ReplayRunConfig(
        mode=auto_config.mode,
        run_id=run_id if auto_config.mode == "record" else None,
        base_run=auto_config.base_run or (run_id if auto_config.mode == "replay" else None),
        log_dir=auto_config.log_dir,
        overwrite=auto_config.overwrite,
        semantic_fallback=auto_config.semantic_fallback,
    )
    with replay_session(session_config):
        yield


def derive_run_id(
    template: str,
    *,
    runnable: Any,
    input_value: Any,
    config: dict[str, Any] | None,
    graph_name: str | None,
) -> str:
    """Derive a stable run id from runnable, input, and LangGraph config."""

    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict):
        configurable = {}
    values = {
        "graph": sanitize_run_id_part(graph_name or _runnable_name(runnable)),
        "thread_id": sanitize_run_id_part(configurable.get("thread_id")),
        "run_id": sanitize_run_id_part(
            config.get("run_id") if isinstance(config, dict) else None
            or configurable.get("run_id")
        ),
        "checkpoint_id": sanitize_run_id_part(configurable.get("checkpoint_id")),
        "input_hash": _input_hash(input_value),
    }
    return format_run_id(template, values)


def _runnable_name(runnable: Any) -> str:
    get_name = getattr(runnable, "get_name", None)
    if callable(get_name):
        try:
            return str(get_name())
        except Exception:
            pass
    cls = runnable.__class__
    return getattr(cls, "__name__", None) or getattr(cls, "__qualname__", "runnable")


def _input_hash(value: Any) -> str:
    try:
        from .normalization import normalize_for_json

        normalized = normalize_for_json(value)
    except Exception:
        normalized = repr(value)
    try:
        payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(normalized)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
