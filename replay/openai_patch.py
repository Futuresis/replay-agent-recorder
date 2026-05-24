from __future__ import annotations

import importlib
from typing import Any

from .context import get_current_session


_installed = False
_original_async_create = None
_original_sync_create = None
_async_completions_class = None
_sync_completions_class = None


def _load_openai_completion_classes() -> tuple[type[Any], type[Any]] | None:
    try:
        importlib.import_module("openai")
    except ModuleNotFoundError as exc:
        if exc.name != "openai":
            raise
        return None
    module = importlib.import_module("openai.resources.chat.completions.completions")
    return module.AsyncCompletions, module.Completions


async def _patched_async_create(self, *args: Any, **kwargs: Any):
    session = get_current_session()
    if session is None:
        return await _original_async_create(self, *args, **kwargs)
    if _in_external_boundary():
        return await _original_async_create(self, *args, **kwargs)
    return await session.handle_async_llm_call(_original_async_create, self, args, kwargs)


def _patched_sync_create(self, *args: Any, **kwargs: Any):
    session = get_current_session()
    if session is None:
        return _original_sync_create(self, *args, **kwargs)
    if _in_external_boundary():
        return _original_sync_create(self, *args, **kwargs)
    return session.handle_sync_llm_call(_original_sync_create, self, args, kwargs)


def _in_external_boundary() -> bool:
    try:
        from .semantic_runtime import RUNTIME
    except Exception:
        return False
    snapshot = getattr(RUNTIME, "current_kinds_snapshot", None)
    if not callable(snapshot):
        return False
    kinds = snapshot()
    return bool(kinds and kinds[-1] in {"llm", "tool"})


def install_openai_patch() -> None:
    global _installed
    global _original_async_create
    global _original_sync_create
    global _async_completions_class
    global _sync_completions_class

    if _installed:
        return

    classes = _load_openai_completion_classes()
    if classes is None:
        return
    AsyncCompletions, Completions = classes

    original_async_create = AsyncCompletions.create
    original_sync_create = Completions.create

    async_patched = False
    try:
        AsyncCompletions.create = _patched_async_create
        async_patched = True
        Completions.create = _patched_sync_create
    except Exception:
        if async_patched:
            AsyncCompletions.create = original_async_create
        raise

    _original_async_create = original_async_create
    _original_sync_create = original_sync_create
    _async_completions_class = AsyncCompletions
    _sync_completions_class = Completions
    _installed = True


def uninstall_openai_patch() -> None:
    global _installed
    global _original_async_create
    global _original_sync_create
    global _async_completions_class
    global _sync_completions_class
    if not _installed:
        return

    if _async_completions_class is not None and _original_async_create is not None:
        _async_completions_class.create = _original_async_create
    if _sync_completions_class is not None and _original_sync_create is not None:
        _sync_completions_class.create = _original_sync_create

    _installed = False
    _original_async_create = None
    _original_sync_create = None
    _async_completions_class = None
    _sync_completions_class = None
