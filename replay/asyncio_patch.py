from __future__ import annotations

import asyncio
import inspect
from typing import Any

from .context import branch_allocation_suppressed, current_branch_id, get_current_session


_installed = False
_original_create_task = None
_original_gather = None
_original_taskgroup_create_task = None


async def _run_in_branch(coro: Any, branch_id: str) -> Any:
    token = current_branch_id.set(branch_id)
    try:
        return await coro
    finally:
        session = get_current_session()
        if session is not None:
            session.finish_branch(branch_id)
        current_branch_id.reset(token)


def _wrap_coro_if_needed(coro: Any) -> Any:
    session = get_current_session()
    if session is None or branch_allocation_suppressed.get() or not inspect.iscoroutine(coro):
        return coro

    branch_id = session.allocate_child_branch(current_branch_id.get())
    return _run_in_branch(coro, branch_id)


def _patched_create_task(coro: Any, *, name: str | None = None, context: Any = None):
    wrapped = _wrap_coro_if_needed(coro)
    if context is None:
        task = _original_create_task(wrapped, name=name)
    else:
        task = _original_create_task(wrapped, name=name, context=context)
    if wrapped is not coro:
        setattr(task, "_replay_branch_id", current_branch_id.get())
    return task


def _patched_gather(*aws: Any, return_exceptions: bool = False):
    wrapped_aws = []
    for aw in aws:
        if isinstance(aw, asyncio.Future):
            wrapped_aws.append(aw)
        else:
            wrapped_aws.append(_wrap_coro_if_needed(aw))
    return _original_gather(*wrapped_aws, return_exceptions=return_exceptions)


def _patched_taskgroup_create_task(
    self: asyncio.TaskGroup,
    coro: Any,
    *,
    name: str | None = None,
    context: Any = None,
):
    wrapped = _wrap_coro_if_needed(coro)
    if context is None:
        return _original_taskgroup_create_task(self, wrapped, name=name)
    return _original_taskgroup_create_task(self, wrapped, name=name, context=context)


def install_asyncio_patch() -> None:
    global _installed
    global _original_create_task
    global _original_gather
    global _original_taskgroup_create_task

    if _installed:
        return

    _original_create_task = asyncio.create_task
    _original_gather = asyncio.gather
    _original_taskgroup_create_task = asyncio.TaskGroup.create_task

    asyncio.create_task = _patched_create_task
    asyncio.gather = _patched_gather
    asyncio.TaskGroup.create_task = _patched_taskgroup_create_task
    _installed = True


def uninstall_asyncio_patch() -> None:
    global _installed
    if not _installed:
        return

    asyncio.create_task = _original_create_task
    asyncio.gather = _original_gather
    asyncio.TaskGroup.create_task = _original_taskgroup_create_task
    _installed = False
