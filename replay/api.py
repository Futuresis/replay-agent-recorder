from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .asyncio_patch import install_asyncio_patch, uninstall_asyncio_patch
from .context import RecordSession, ReplaySession
from .filesystem_effects import FilesystemCapture
from .import_hook import install_import_hook, uninstall_import_hook
from .langchain_patch import install_langchain_patch
from .langgraph_patch import install_langgraph_patch
from .openai_patch import install_openai_patch, uninstall_openai_patch
from .sandbox_manager import ManagedSandbox, ManagedSandboxCapture
from .tool_adapters import (
    BaseToolAdapter,
    ClassMethodToolAdapter,
    MappingToolAdapter,
    MethodToolAdapter,
    ToolAdapter,
)
from .tools import invoke_tool, invoke_tool_sync


_semantic_token = None
_patch_restorers: list = []


def install(
    *,
    semantic: bool = True,
    project_root: str | Path | None = None,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    langchain: bool = False,
    langgraph: bool = False,
) -> None:
    """Patch supported LLM providers, asyncio branches, and optional AST provenance."""

    global _semantic_token
    global _patch_restorers
    if semantic and _semantic_token is None:
        _semantic_token = install_import_hook(project_root or Path.cwd(), include=include, exclude=exclude)
    install_openai_patch()
    install_asyncio_patch()
    if langchain:
        _patch_restorers.append(install_langchain_patch())
    if langgraph:
        _patch_restorers.append(install_langgraph_patch())


def wrap_runnable(obj: Any, *, framework: str = "auto") -> Any:
    """Best-effort wrapper for runnable-like objects imported by generated runners."""

    if framework in {"auto", "langgraph", "both"}:
        try:
            from .langgraph_patch import wrap_compiled_graph

            obj = wrap_compiled_graph(obj)
        except Exception:
            pass
    return obj


def uninstall() -> None:
    """Restore patched functions."""

    global _semantic_token
    global _patch_restorers
    for restore in reversed(_patch_restorers):
        restore()
    _patch_restorers = []
    uninstall_asyncio_patch()
    uninstall_openai_patch()
    if _semantic_token is not None:
        uninstall_import_hook(_semantic_token)
        _semantic_token = None


def record(
    run_id: str,
    *,
    log_dir: str | Path | None = None,
    overwrite: bool = True,
) -> RecordSession:
    return RecordSession(run_id=run_id, log_dir=log_dir, overwrite=overwrite)


def replay(
    *,
    base_run: str,
    breakpoint_record_uid: str | None = None,
    override_output: str | None = None,
    override_input: dict[str, Any] | None = None,
    override_message: dict[str, Any] | None = None,
    log_dir: str | Path | None = None,
    fork_run: str | None = None,
    semantic_fallback: bool = False,
) -> ReplaySession:
    return ReplaySession(
        base_run=base_run,
        breakpoint_record_uid=breakpoint_record_uid,
        override_output=override_output,
        override_input=override_input,
        override_message=override_message,
        log_dir=log_dir,
        fork_run=fork_run,
        semantic_fallback=semantic_fallback,
    )


def sandbox(
    *,
    base_root: str | Path,
    work_root: str | Path,
    reset: bool = True,
) -> ManagedSandbox:
    return ManagedSandbox(base_root=base_root, work_root=work_root, reset=reset)


def managed_sandbox(
    *,
    base_root: str | Path,
    work_root: str | Path,
    reset: bool = True,
    max_file_bytes: int = 1_000_000,
    encoding: str = "utf-8",
) -> ManagedSandboxCapture:
    return ManagedSandboxCapture(
        base_root=base_root,
        work_root=work_root,
        reset=reset,
        max_file_bytes=max_file_bytes,
        encoding=encoding,
    )
