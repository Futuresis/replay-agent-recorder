from __future__ import annotations

import os
from pathlib import Path

from .api import install as replay_install
from .autosession import AutoSessionConfig, enable_auto_session
from .entrypoints import framework_install_flags


_AUTO_SESSION_TOKEN = None


def install_from_env() -> None:
    """Install Replay instrumentation from REPLAY_* environment variables."""

    global _AUTO_SESSION_TOKEN
    if os.environ.get("REPLAY_AUTOINSTALL") != "1":
        return

    framework = os.environ.get("REPLAY_FRAMEWORK", "auto")
    langchain, langgraph = framework_install_flags(framework)  # type: ignore[arg-type]
    replay_install(
        semantic=os.environ.get("REPLAY_NO_SEMANTIC") != "1",
        project_root=_optional_path(os.environ.get("REPLAY_PROJECT_ROOT")),
        include=_split_path_list(os.environ.get("REPLAY_INCLUDE")),
        exclude=_split_path_list(os.environ.get("REPLAY_EXCLUDE")),
        langchain=langchain,
        langgraph=langgraph,
    )

    if os.environ.get("REPLAY_AUTO_SESSION") == "1":
        run_id_template = (
            os.environ.get("REPLAY_RUN_ID_TEMPLATE")
            or os.environ.get("REPLAY_RUN_ID")
            or "{graph}-{thread_id}-{input_hash}"
        )
        _AUTO_SESSION_TOKEN = enable_auto_session(
            AutoSessionConfig(
                mode=os.environ.get("REPLAY_MODE", "record"),  # type: ignore[arg-type]
                run_id_template=run_id_template,
                base_run=os.environ.get("REPLAY_BASE_RUN"),
                log_dir=_optional_path(os.environ.get("REPLAY_LOG_DIR")),
                overwrite=os.environ.get("REPLAY_NO_OVERWRITE") != "1",
                semantic_fallback=os.environ.get("REPLAY_SEMANTIC_FALLBACK") == "1",
            )
        )


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _split_path_list(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    return tuple(item for item in value.split(os.pathsep) if item)
