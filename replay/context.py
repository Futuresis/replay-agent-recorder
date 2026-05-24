from __future__ import annotations

import contextvars
import inspect
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from .edges import make_edge_records, provenance_to_json
from .errors import FilesystemCaptureError, InvalidBreakpointError, ReplayMissError, UnsupportedOverrideInputError
from .filesystem_effects import (
    FilesystemCapture,
    apply_filesystem_effect,
    build_filesystem_effect,
    filesystem_effect_from_record,
    snapshot_filesystem,
)
from .ids import build_input_record, compute_input_id
from .response import (
    build_override_message_response,
    build_override_response,
    response_from_record,
    response_to_record,
)
from .semantic_runtime import RUNTIME, Source, seed_response
from .storage import JsonlWriter, load_records, load_replay_records, make_record_uid, next_fork_run_id, run_path
from .tools import (
    replay_tool_record,
    tool_error_to_record,
    tool_output_to_record,
)


DEFAULT_BRANCH_ID = "root"
DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "runs"

current_branch_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "replay_current_branch_id",
    default=DEFAULT_BRANCH_ID,
)
_current_session: contextvars.ContextVar["BaseSession | None"] = contextvars.ContextVar(
    "replay_current_session",
    default=None,
)
branch_allocation_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "replay_branch_allocation_suppressed",
    default=False,
)


@dataclass(frozen=True)
class PreparedCall:
    input_record: dict[str, Any]
    input_id: str
    path_id: str
    provenance: dict[str, set[Source]]
    semantic: dict[str, Any] | None


def get_current_session() -> "BaseSession | None":
    return _current_session.get()


class BaseSession:
    def __init__(self, *, log_dir: str | Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
        self.lock = threading.RLock()
        self.branch_child_indexes: dict[str, int] = defaultdict(int)
        self.branch_llm_indexes: dict[str, int] = defaultdict(int)
        self.branch_tool_indexes: dict[str, int] = defaultdict(int)
        self.branch_parents: dict[str, str] = {}
        self._session_token: contextvars.Token | None = None
        self._branch_token: contextvars.Token | None = None
        self._runtime_token: Any = None

    def __enter__(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_token = RUNTIME.enter_context(enabled=RUNTIME.enabled)
        self._session_token = _current_session.set(self)
        self._branch_token = current_branch_id.set(DEFAULT_BRANCH_ID)
        self.on_enter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.on_exit()
        finally:
            if self._branch_token is not None:
                current_branch_id.reset(self._branch_token)
            if self._session_token is not None:
                _current_session.reset(self._session_token)
            if self._runtime_token is not None:
                RUNTIME.exit_context(self._runtime_token)

    def on_enter(self) -> None:
        pass

    def on_exit(self) -> None:
        pass

    def allocate_child_branch(self, parent_branch_id: str) -> str:
        with self.lock:
            child_index = self.branch_child_indexes[parent_branch_id]
            self.branch_child_indexes[parent_branch_id] += 1
            child_branch_id = f"{parent_branch_id}.{child_index}"
            self.branch_parents[child_branch_id] = parent_branch_id
            self.on_child_branch_allocated(parent_branch_id, child_branch_id)
            return child_branch_id

    def on_child_branch_allocated(self, parent_branch_id: str, child_branch_id: str) -> None:
        pass

    def finish_branch(self, branch_id: str) -> None:
        pass

    def allocate_path_id(self) -> str:
        branch_id = current_branch_id.get()
        with self.lock:
            local_index = self.branch_llm_indexes[branch_id]
            self.branch_llm_indexes[branch_id] += 1
        return f"{branch_id}/{local_index}"

    def allocate_tool_path_id(self) -> str:
        branch_id = current_branch_id.get()
        with self.lock:
            local_index = self.branch_tool_indexes[branch_id]
            self.branch_tool_indexes[branch_id] += 1
        return f"{branch_id}/tool/{local_index}"

    def next_record_uid(self) -> str:
        raise NotImplementedError

    def _merged_metadata(
        self,
        metadata: dict[str, Any] | None = None,
        *,
        semantic_hint: str | None = None,
    ) -> dict[str, Any] | None:
        merged = RUNTIME.merge_metadata(metadata, semantic_hint=semantic_hint)
        return merged or None

    def build_call_record(
        self,
        *,
        input_record: dict[str, Any],
        input_id: str,
        path_id: str,
        response: Any,
        started_at: float,
        matched_by: str | None = None,
        override: bool = False,
        provenance: dict[str, set[Source]] | None = None,
        semantic: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_record = response_to_record(response)
        record_metadata = {
            "created_at": iso_now(),
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
            "usage": output_record.get("usage"),
            "matched_by": matched_by,
            "override": override,
        }
        if metadata:
            record_metadata.update(metadata)
        record = {
            "record_uid": self.next_record_uid(),
            "kind": "llm",
            "input_id": input_id,
            "path_id": path_id,
            "input": input_record,
            "output": output_record,
            "callsite": capture_callsite(),
            "metadata": record_metadata,
        }
        self.attach_semantic_metadata(record, provenance=provenance, semantic=semantic)
        return record

    def build_tool_record(
        self,
        *,
        input_record: dict[str, Any],
        input_id: str,
        path_id: str,
        output_record: dict[str, Any] | None,
        error_record: dict[str, Any] | None,
        started_at: float,
        matched_by: str | None = None,
        effects: dict[str, Any] | None = None,
        provenance: dict[str, set[Source]] | None = None,
        semantic: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record_metadata = {
            "created_at": iso_now(),
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
            "matched_by": matched_by,
        }
        if metadata:
            record_metadata.update(metadata)
        record = {
            "record_uid": self.next_record_uid(),
            "kind": "tool",
            "input_id": input_id,
            "path_id": path_id,
            "input": input_record,
            "output": output_record,
            "error": error_record,
            "callsite": capture_callsite(),
            "metadata": record_metadata,
        }
        if effects is not None:
            record["effects"] = effects
        self.attach_semantic_metadata(record, provenance=provenance, semantic=semantic)
        return record

    def attach_semantic_metadata(
        self,
        record: dict[str, Any],
        *,
        provenance: dict[str, set[Source]] | None = None,
        semantic: dict[str, Any] | None = None,
    ) -> None:
        if provenance is None and semantic is None:
            return
        record["schema_version"] = 2
        if provenance is not None:
            record["metadata"]["provenance"] = provenance_to_json(provenance)
        if semantic is not None:
            existing = record["metadata"].get("semantic")
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(semantic)
                record["metadata"]["semantic"] = merged
            else:
                record["metadata"]["semantic"] = semantic

    def snapshot_tool_filesystem(
        self,
        fs_capture: FilesystemCapture | None,
    ) -> dict[str, Any] | None:
        if fs_capture is None:
            return None
        return snapshot_filesystem(fs_capture)

    def build_tool_filesystem_effect(
        self,
        fs_capture: FilesystemCapture | None,
        before_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if fs_capture is None:
            return None
        if before_snapshot is None:
            raise FilesystemCaptureError("Missing filesystem snapshot before tool execution.")
        after_snapshot = snapshot_filesystem(fs_capture)
        return {
            "filesystem": build_filesystem_effect(
                fs_capture,
                before_snapshot,
                after_snapshot,
            )
        }

    def apply_replayed_tool_effects(
        self,
        record: dict[str, Any],
        fs_capture: FilesystemCapture | None,
    ) -> None:
        filesystem_effect = filesystem_effect_from_record(record)
        if filesystem_effect is None:
            return
        if fs_capture is None:
            raise FilesystemCaptureError(
                "Replay record contains filesystem effects; pass fs_capture to apply them."
            )
        apply_filesystem_effect(filesystem_effect, fs_capture)


class RecordSession(BaseSession):
    def __init__(
        self,
        *,
        run_id: str,
        log_dir: str | Path | None = None,
        overwrite: bool = True,
    ) -> None:
        super().__init__(log_dir=log_dir)
        self.run_id = run_id
        self.overwrite = overwrite
        self.writer: JsonlWriter | None = None
        self._record_counter = 0

    def on_enter(self) -> None:
        self.writer = JsonlWriter(run_path(self.log_dir, self.run_id), overwrite=self.overwrite)

    def on_exit(self) -> None:
        if self.writer is not None:
            self.writer.close()

    def next_record_uid(self) -> str:
        with self.lock:
            self._record_counter += 1
            return make_record_uid(self._record_counter)

    async def handle_async_llm_boundary(
        self,
        *,
        name: str,
        record_kwargs: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        provider: str = "openai",
        api: str = "chat.completions.create",
        extra_records_factory=None,
    ):
        prepared = prepare_llm_call(
            self,
            record_kwargs,
            semantic_hint=semantic_hint,
            provider=provider,
            api=api,
        )
        started_at = time.perf_counter()
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("llm")
        try:
            response = invoke()
            response = await response if inspect.isawaitable(response) else response
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)
        record = self.build_call_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            response=response,
            started_at=started_at,
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        source = source_for_record(self.run_id, record)
        self.write_record(
            record,
            edge_records_for(prepared, source),
            extra_records_factory=extra_records_factory,
        )
        seed_response(response, source)
        return response

    def handle_sync_llm_boundary(
        self,
        *,
        name: str,
        record_kwargs: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        provider: str = "openai",
        api: str = "chat.completions.create",
        extra_records_factory=None,
    ):
        prepared = prepare_llm_call(
            self,
            record_kwargs,
            semantic_hint=semantic_hint,
            provider=provider,
            api=api,
        )
        started_at = time.perf_counter()
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("llm")
        try:
            response = invoke()
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)
        record = self.build_call_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            response=response,
            started_at=started_at,
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        source = source_for_record(self.run_id, record)
        self.write_record(
            record,
            edge_records_for(prepared, source),
            extra_records_factory=extra_records_factory,
        )
        seed_response(response, source)
        return response

    async def handle_async_llm_call(self, original_create, resource, args, kwargs):
        def invoke():
            call_args, call_kwargs = RUNTIME.plain_call_args(args, kwargs)
            return original_create(resource, *call_args, **call_kwargs)

        return await self.handle_async_llm_boundary(
            name="chat.completions.create",
            record_kwargs=kwargs,
            invoke=invoke,
            provider="openai",
            api="chat.completions.create",
        )

    def handle_sync_llm_call(self, original_create, resource, args, kwargs):
        def invoke():
            call_args, call_kwargs = RUNTIME.plain_call_args(args, kwargs)
            return original_create(resource, *call_args, **call_kwargs)

        return self.handle_sync_llm_boundary(
            name="chat.completions.create",
            record_kwargs=kwargs,
            invoke=invoke,
            provider="openai",
            api="chat.completions.create",
        )

    async def handle_async_tool_event(
        self,
        *,
        input_record: dict[str, Any],
        input_id: str,
        tool_name: str,
        invoke,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
    ):
        return await self.handle_async_tool_boundary(
            tool_name=tool_name,
            input_record=input_record,
            invoke=invoke,
            fs_capture=fs_capture,
            path_id=path_id,
            input_arguments=input_arguments,
            input_id=input_id,
        )

    async def handle_async_tool_boundary(
        self,
        *,
        tool_name: str,
        input_record: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
        input_id: str | None = None,
    ):
        input_record = RUNTIME.plain_snapshot(input_record)
        if input_id is None:
            input_id = compute_input_id(input_record)
        if path_id is None:
            path_id = self.allocate_tool_path_id()
        prepared = prepare_tool_event(
            input_record=input_record,
            input_id=input_id,
            path_id=path_id,
            input_arguments=input_arguments,
            semantic_hint=semantic_hint,
        )
        started_at = time.perf_counter()
        before_snapshot = self.snapshot_tool_filesystem(fs_capture)
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("tool")
        try:
            try:
                result = invoke()
                output = await result if inspect.isawaitable(result) else result
            except Exception as exc:
                effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
                record = self.build_tool_record(
                    input_record=prepared.input_record,
                    input_id=prepared.input_id,
                    path_id=prepared.path_id,
                    output_record=None,
                    error_record=tool_error_to_record(exc),
                    started_at=started_at,
                    effects=effects,
                    provenance=prepared.provenance if prepared.semantic is not None else None,
                    semantic=prepared.semantic,
                    metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
                )
                source = source_for_record(self.run_id, record)
                self.write_record(record, edge_records_for(prepared, source))
                raise
            output_record = tool_output_to_record(output, tool_name=tool_name)
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)

        effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
        record = self.build_tool_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            output_record=output_record,
            error_record=None,
            started_at=started_at,
            effects=effects,
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        source = source_for_record(self.run_id, record)
        self.write_record(record, edge_records_for(prepared, source))
        return seed_response(output, source)

    def handle_sync_tool_event(
        self,
        *,
        input_record: dict[str, Any],
        input_id: str,
        tool_name: str,
        invoke,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
    ):
        return self.handle_sync_tool_boundary(
            tool_name=tool_name,
            input_record=input_record,
            invoke=invoke,
            fs_capture=fs_capture,
            path_id=path_id,
            input_arguments=input_arguments,
            input_id=input_id,
        )

    def handle_sync_tool_boundary(
        self,
        *,
        tool_name: str,
        input_record: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
        input_id: str | None = None,
    ):
        input_record = RUNTIME.plain_snapshot(input_record)
        if input_id is None:
            input_id = compute_input_id(input_record)
        if path_id is None:
            path_id = self.allocate_tool_path_id()
        prepared = prepare_tool_event(
            input_record=input_record,
            input_id=input_id,
            path_id=path_id,
            input_arguments=input_arguments,
            semantic_hint=semantic_hint,
        )
        started_at = time.perf_counter()
        before_snapshot = self.snapshot_tool_filesystem(fs_capture)
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("tool")
        try:
            try:
                output = invoke()
            except Exception as exc:
                effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
                record = self.build_tool_record(
                    input_record=prepared.input_record,
                    input_id=prepared.input_id,
                    path_id=prepared.path_id,
                    output_record=None,
                    error_record=tool_error_to_record(exc),
                    started_at=started_at,
                    effects=effects,
                    provenance=prepared.provenance if prepared.semantic is not None else None,
                    semantic=prepared.semantic,
                    metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
                )
                source = source_for_record(self.run_id, record)
                self.write_record(record, edge_records_for(prepared, source))
                raise
            output_record = tool_output_to_record(output, tool_name=tool_name)
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)

        effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
        record = self.build_tool_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            output_record=output_record,
            error_record=None,
            started_at=started_at,
            effects=effects,
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        source = source_for_record(self.run_id, record)
        self.write_record(record, edge_records_for(prepared, source))
        return seed_response(output, source)

    def write_record(
        self,
        record: dict[str, Any],
        edge_records: list[dict[str, Any]] | None = None,
        *,
        extra_records_factory=None,
    ) -> None:
        if self.writer is None:
            raise RuntimeError("Record session writer is not open.")
        with self.lock:
            self.writer.write(record)
            extra_records = (
                extra_records_factory(record)
                if extra_records_factory is not None
                else []
            )
            for extra_record in extra_records:
                self.writer.write(extra_record)
            for edge_record in edge_records or []:
                self.writer.write(edge_record)


class ReplaySession(BaseSession):
    def __init__(
        self,
        *,
        base_run: str,
        breakpoint_record_uid: str | None = None,
        override_output: str | None = None,
        override_input: dict[str, Any] | None = None,
        override_message: dict[str, Any] | None = None,
        log_dir: str | Path | None = None,
        fork_run: str | None = None,
        semantic_fallback: bool = False,
    ) -> None:
        super().__init__(log_dir=log_dir)
        override_count = sum(
            item is not None
            for item in (override_output, override_input, override_message)
        )
        if override_count > 1:
            raise ValueError(
                "override_output, override_input, and override_message are mutually exclusive."
            )
        self.base_run = base_run
        self.breakpoint_record_uid = breakpoint_record_uid
        self.override_output = override_output
        self.override_input = override_input
        self.override_message = override_message
        self.fork_run = fork_run
        self.semantic_fallback = semantic_fallback
        self.branch_modes: dict[str, str] = defaultdict(lambda: "replay")
        self.dirty_branches: set[str] = set()
        self.dirty_filesystem_roots: set[Path] = set()
        self.fork_sources: set[Source] = set()
        self.history: list[dict[str, Any]] = []
        self.consumed_record_uids: set[str] = set()
        self.fork_writer: JsonlWriter | None = None
        self._fork_record_counter = 0

    def on_enter(self) -> None:
        self.history = load_replay_records(run_path(self.log_dir, self.base_run))
        self.validate_breakpoint()
        self.branch_modes[DEFAULT_BRANCH_ID] = "replay"

    def on_exit(self) -> None:
        if self.fork_writer is not None:
            self.fork_writer.close()

    def next_record_uid(self) -> str:
        with self.lock:
            self._fork_record_counter += 1
            return make_record_uid(self._fork_record_counter)

    def on_child_branch_allocated(self, parent_branch_id: str, child_branch_id: str) -> None:
        self.branch_modes[child_branch_id] = self.branch_modes[parent_branch_id]

    def finish_branch(self, branch_id: str) -> None:
        with self.lock:
            parent_branch_id = self.branch_parents.get(branch_id)
            if parent_branch_id is None or branch_id not in self.dirty_branches:
                return
            if self.branch_modes[branch_id] == "live":
                self.mark_branch_live(parent_branch_id)
            else:
                self.mark_branch_dirty(parent_branch_id)

    def validate_breakpoint(self) -> None:
        if self.breakpoint_record_uid is None:
            return
        matches = [
            record
            for record in self.history
            if record.get("record_uid") == self.breakpoint_record_uid
        ]
        if not matches:
            raise InvalidBreakpointError(
                f"breakpoint_record_uid not found in base run: {self.breakpoint_record_uid}"
            )
        record = matches[0]
        if record.get("kind") != "llm":
            raise InvalidBreakpointError(
                "breakpoint_record_uid must refer to an LLM record, "
                f"but got kind={record.get('kind')!r}."
            )

    async def handle_async_llm_boundary(
        self,
        *,
        name: str,
        record_kwargs: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        provider: str = "openai",
        api: str = "chat.completions.create",
        extra_records_factory=None,
    ):
        prepared = prepare_llm_call(
            self,
            record_kwargs,
            semantic_hint=semantic_hint,
            provider=provider,
            api=api,
        )
        decision = self.decide_replay(
            kind="llm",
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            semantic=prepared.semantic,
            provenance=prepared.provenance,
        )

        if decision["action"] == "replay":
            response = response_from_record(decision["record"])
            return seed_response(response, source_for_record(self.base_run, decision["record"]))

        if decision["action"] == "override":
            started_at = time.perf_counter()
            response = build_override_response(decision["record"], self.override_output or "")
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
                metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
            )
            self.write_fork_record_with_edges(
                record,
                prepared,
                extra_records_factory=extra_records_factory,
            )
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_message":
            started_at = time.perf_counter()
            response = build_override_message_response(
                decision["record"],
                self.override_message or {},
            )
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
                metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
            )
            self.write_fork_record_with_edges(
                record,
                prepared,
                extra_records_factory=extra_records_factory,
            )
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_input":
            raise UnsupportedOverrideInputError(
                f"override_input is not supported for generic LLM boundary {provider}:{api}."
            )

        started_at = time.perf_counter()
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("llm")
        try:
            response = invoke()
            response = await response if inspect.isawaitable(response) else response
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)
        record = self.build_call_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            response=response,
            started_at=started_at,
            matched_by=decision.get("matched_by"),
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        self.write_fork_record_with_edges(
            record,
            prepared,
            extra_records_factory=extra_records_factory,
        )
        return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

    def handle_sync_llm_boundary(
        self,
        *,
        name: str,
        record_kwargs: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        provider: str = "openai",
        api: str = "chat.completions.create",
        extra_records_factory=None,
    ):
        prepared = prepare_llm_call(
            self,
            record_kwargs,
            semantic_hint=semantic_hint,
            provider=provider,
            api=api,
        )
        decision = self.decide_replay(
            kind="llm",
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            semantic=prepared.semantic,
            provenance=prepared.provenance,
        )

        if decision["action"] == "replay":
            response = response_from_record(decision["record"])
            return seed_response(response, source_for_record(self.base_run, decision["record"]))

        if decision["action"] == "override":
            started_at = time.perf_counter()
            response = build_override_response(decision["record"], self.override_output or "")
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
                metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
            )
            self.write_fork_record_with_edges(
                record,
                prepared,
                extra_records_factory=extra_records_factory,
            )
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_message":
            started_at = time.perf_counter()
            response = build_override_message_response(
                decision["record"],
                self.override_message or {},
            )
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
                metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
            )
            self.write_fork_record_with_edges(
                record,
                prepared,
                extra_records_factory=extra_records_factory,
            )
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_input":
            raise UnsupportedOverrideInputError(
                f"override_input is not supported for generic LLM boundary {provider}:{api}."
            )

        started_at = time.perf_counter()
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("llm")
        try:
            response = invoke()
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)
        record = self.build_call_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            response=response,
            started_at=started_at,
            matched_by=decision.get("matched_by"),
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        self.write_fork_record_with_edges(
            record,
            prepared,
            extra_records_factory=extra_records_factory,
        )
        return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

    async def handle_async_llm_call(self, original_create, resource, args, kwargs):
        prepared = prepare_llm_call(self, kwargs)
        decision = self.decide_replay(
            kind="llm",
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            semantic=prepared.semantic,
            provenance=prepared.provenance,
        )

        if decision["action"] == "replay":
            response = response_from_record(decision["record"])
            return seed_response(response, source_for_record(self.base_run, decision["record"]))

        if decision["action"] == "override":
            started_at = time.perf_counter()
            response = build_override_response(decision["record"], self.override_output or "")
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
            )
            self.write_fork_record_with_edges(record, prepared)
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_message":
            started_at = time.perf_counter()
            response = build_override_message_response(
                decision["record"],
                self.override_message or {},
            )
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
            )
            self.write_fork_record_with_edges(record, prepared)
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_input":
            patched_kwargs = self.apply_override_input(kwargs)
            input_record, input_id = prepare_replayed_llm_input(patched_kwargs)
            started_at = time.perf_counter()
            token = branch_allocation_suppressed.set(True)
            try:
                call_args, call_kwargs = RUNTIME.plain_call_args(args, patched_kwargs)
                response = await original_create(resource, *call_args, **call_kwargs)
            finally:
                branch_allocation_suppressed.reset(token)
            record = self.build_call_record(
                input_record=input_record,
                input_id=input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
            )
            record["metadata"]["input_override"] = True
            record["metadata"]["base_record_uid"] = decision["record"]["record_uid"]
            self.write_fork_record_with_edges(record, prepared)
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        started_at = time.perf_counter()
        token = branch_allocation_suppressed.set(True)
        try:
            call_args, call_kwargs = RUNTIME.plain_call_args(args, kwargs)
            response = await original_create(resource, *call_args, **call_kwargs)
        finally:
            branch_allocation_suppressed.reset(token)
        record = self.build_call_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            response=response,
            started_at=started_at,
            matched_by=decision.get("matched_by"),
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
        )
        self.write_fork_record_with_edges(record, prepared)
        return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

    def handle_sync_llm_call(self, original_create, resource, args, kwargs):
        prepared = prepare_llm_call(self, kwargs)
        decision = self.decide_replay(
            kind="llm",
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            semantic=prepared.semantic,
            provenance=prepared.provenance,
        )

        if decision["action"] == "replay":
            response = response_from_record(decision["record"])
            return seed_response(response, source_for_record(self.base_run, decision["record"]))

        if decision["action"] == "override":
            started_at = time.perf_counter()
            response = build_override_response(decision["record"], self.override_output or "")
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
            )
            self.write_fork_record_with_edges(record, prepared)
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_message":
            started_at = time.perf_counter()
            response = build_override_message_response(
                decision["record"],
                self.override_message or {},
            )
            record = self.build_call_record(
                input_record=prepared.input_record,
                input_id=prepared.input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                override=True,
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
            )
            self.write_fork_record_with_edges(record, prepared)
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        if decision["action"] == "override_input":
            patched_kwargs = self.apply_override_input(kwargs)
            input_record, input_id = prepare_replayed_llm_input(patched_kwargs)
            started_at = time.perf_counter()
            token = branch_allocation_suppressed.set(True)
            try:
                call_args, call_kwargs = RUNTIME.plain_call_args(args, patched_kwargs)
                response = original_create(resource, *call_args, **call_kwargs)
            finally:
                branch_allocation_suppressed.reset(token)
            record = self.build_call_record(
                input_record=input_record,
                input_id=input_id,
                path_id=prepared.path_id,
                response=response,
                started_at=started_at,
                matched_by=decision["matched_by"],
                provenance=prepared.provenance if prepared.semantic is not None else None,
                semantic=prepared.semantic,
            )
            record["metadata"]["input_override"] = True
            record["metadata"]["base_record_uid"] = decision["record"]["record_uid"]
            self.write_fork_record_with_edges(record, prepared)
            return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

        started_at = time.perf_counter()
        token = branch_allocation_suppressed.set(True)
        try:
            call_args, call_kwargs = RUNTIME.plain_call_args(args, kwargs)
            response = original_create(resource, *call_args, **call_kwargs)
        finally:
            branch_allocation_suppressed.reset(token)
        record = self.build_call_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            response=response,
            started_at=started_at,
            matched_by=decision.get("matched_by"),
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
        )
        self.write_fork_record_with_edges(record, prepared)
        return seed_response(response, source_for_record(self.fork_run or self.base_run, record))

    async def handle_async_tool_event(
        self,
        *,
        input_record: dict[str, Any],
        input_id: str,
        tool_name: str,
        invoke,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
    ):
        return await self.handle_async_tool_boundary(
            tool_name=tool_name,
            input_record=input_record,
            invoke=invoke,
            fs_capture=fs_capture,
            path_id=path_id,
            input_arguments=input_arguments,
            input_id=input_id,
        )

    async def handle_async_tool_boundary(
        self,
        *,
        tool_name: str,
        input_record: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
        input_id: str | None = None,
    ):
        input_record = RUNTIME.plain_snapshot(input_record)
        if input_id is None:
            input_id = compute_input_id(input_record)
        if path_id is None:
            path_id = self.allocate_tool_path_id()
        prepared = prepare_tool_event(
            input_record=input_record,
            input_id=input_id,
            path_id=path_id,
            input_arguments=input_arguments,
            semantic_hint=semantic_hint,
        )
        decision = self.decide_tool_replay(
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            semantic=prepared.semantic,
            provenance=prepared.provenance,
            fs_capture=fs_capture,
        )

        if decision["action"] == "replay":
            self.apply_replayed_tool_effects(decision["record"], fs_capture)
            output = replay_tool_record(decision["record"])
            return seed_response(output, source_for_record(self.base_run, decision["record"]))

        started_at = time.perf_counter()
        before_snapshot = self.snapshot_tool_filesystem(fs_capture)
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("tool")
        try:
            try:
                result = invoke()
                output = await result if inspect.isawaitable(result) else result
            except Exception as exc:
                effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
                record = self.build_tool_record(
                    input_record=prepared.input_record,
                    input_id=prepared.input_id,
                    path_id=prepared.path_id,
                    output_record=None,
                    error_record=tool_error_to_record(exc),
                    started_at=started_at,
                    matched_by=decision.get("matched_by"),
                    effects=effects,
                    provenance=prepared.provenance if prepared.semantic is not None else None,
                    semantic=prepared.semantic,
                    metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
                )
                self.mark_filesystem_dirty_if_changed(fs_capture, effects)
                self.write_fork_record_with_edges(record, prepared)
                raise
            output_record = tool_output_to_record(output, tool_name=tool_name)
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)

        effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
        record = self.build_tool_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            output_record=output_record,
            error_record=None,
            started_at=started_at,
            matched_by=decision.get("matched_by"),
            effects=effects,
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        self.mark_filesystem_dirty_if_changed(fs_capture, effects)
        self.write_fork_record_with_edges(record, prepared)
        return seed_response(output, source_for_record(self.fork_run or self.base_run, record))

    def handle_sync_tool_event(
        self,
        *,
        input_record: dict[str, Any],
        input_id: str,
        tool_name: str,
        invoke,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
    ):
        return self.handle_sync_tool_boundary(
            tool_name=tool_name,
            input_record=input_record,
            invoke=invoke,
            fs_capture=fs_capture,
            path_id=path_id,
            input_arguments=input_arguments,
            input_id=input_id,
        )

    def handle_sync_tool_boundary(
        self,
        *,
        tool_name: str,
        input_record: dict[str, Any],
        invoke,
        metadata: dict[str, Any] | None = None,
        semantic_hint: str | None = None,
        fs_capture: FilesystemCapture | None = None,
        path_id: str | None = None,
        input_arguments: Any = None,
        input_id: str | None = None,
    ):
        input_record = RUNTIME.plain_snapshot(input_record)
        if input_id is None:
            input_id = compute_input_id(input_record)
        if path_id is None:
            path_id = self.allocate_tool_path_id()
        prepared = prepare_tool_event(
            input_record=input_record,
            input_id=input_id,
            path_id=path_id,
            input_arguments=input_arguments,
            semantic_hint=semantic_hint,
        )
        decision = self.decide_tool_replay(
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            semantic=prepared.semantic,
            provenance=prepared.provenance,
            fs_capture=fs_capture,
        )

        if decision["action"] == "replay":
            self.apply_replayed_tool_effects(decision["record"], fs_capture)
            output = replay_tool_record(decision["record"])
            return seed_response(output, source_for_record(self.base_run, decision["record"]))

        started_at = time.perf_counter()
        before_snapshot = self.snapshot_tool_filesystem(fs_capture)
        token = branch_allocation_suppressed.set(True)
        RUNTIME.push_kind("tool")
        try:
            try:
                output = invoke()
            except Exception as exc:
                effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
                record = self.build_tool_record(
                    input_record=prepared.input_record,
                    input_id=prepared.input_id,
                    path_id=prepared.path_id,
                    output_record=None,
                    error_record=tool_error_to_record(exc),
                    started_at=started_at,
                    matched_by=decision.get("matched_by"),
                    effects=effects,
                    provenance=prepared.provenance if prepared.semantic is not None else None,
                    semantic=prepared.semantic,
                    metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
                )
                self.mark_filesystem_dirty_if_changed(fs_capture, effects)
                self.write_fork_record_with_edges(record, prepared)
                raise
            output_record = tool_output_to_record(output, tool_name=tool_name)
        finally:
            RUNTIME.pop_kind()
            branch_allocation_suppressed.reset(token)

        effects = self.build_tool_filesystem_effect(fs_capture, before_snapshot)
        record = self.build_tool_record(
            input_record=prepared.input_record,
            input_id=prepared.input_id,
            path_id=prepared.path_id,
            output_record=output_record,
            error_record=None,
            started_at=started_at,
            matched_by=decision.get("matched_by"),
            effects=effects,
            provenance=prepared.provenance if prepared.semantic is not None else None,
            semantic=prepared.semantic,
            metadata=self._merged_metadata(metadata, semantic_hint=semantic_hint),
        )
        self.mark_filesystem_dirty_if_changed(fs_capture, effects)
        self.write_fork_record_with_edges(record, prepared)
        return seed_response(output, source_for_record(self.fork_run or self.base_run, record))

    def decide_tool_replay(
        self,
        *,
        input_id: str,
        path_id: str,
        semantic: dict[str, Any] | None = None,
        provenance: dict[str, set[Source]] | None = None,
        fs_capture: FilesystemCapture | None = None,
    ) -> dict[str, Any]:
        root = self.filesystem_root(fs_capture)
        with self.lock:
            if root is not None and root in self.dirty_filesystem_roots:
                self.mark_branch_dirty(current_branch_id.get())
                matched_by = self.consume_matching_record_if_available(
                    kind="tool",
                    input_id=input_id,
                    path_id=path_id,
                )
                return {"action": "live", "matched_by": matched_by or "filesystem_dirty"}

        return self.decide_replay(
            kind="tool",
            input_id=input_id,
            path_id=path_id,
            semantic=semantic,
            provenance=provenance,
        )

    def filesystem_root(self, fs_capture: FilesystemCapture | None) -> Path | None:
        if fs_capture is None:
            return None
        return fs_capture.resolved_root()

    def mark_filesystem_dirty_if_changed(
        self,
        fs_capture: FilesystemCapture | None,
        effects: dict[str, Any] | None,
    ) -> None:
        root = self.filesystem_root(fs_capture)
        if root is None or not effects:
            return
        filesystem = effects.get("filesystem")
        if isinstance(filesystem, dict) and filesystem.get("changes"):
            self.dirty_filesystem_roots.add(root)

    def decide_replay(
        self,
        *,
        kind: str,
        input_id: str,
        path_id: str,
        semantic: dict[str, Any] | None = None,
        provenance: dict[str, set[Source]] | None = None,
    ) -> dict[str, Any]:
        branch_id = current_branch_id.get()
        with self.lock:
            if self.branch_modes[branch_id] == "live":
                return {"action": "live"}

            if self.has_fork_dependency(provenance):
                self.mark_branch_dirty(branch_id)
                matched_by = self.consume_matching_record_if_available(
                    kind=kind,
                    input_id=input_id,
                    path_id=path_id,
                )
                return {"action": "live", "matched_by": matched_by or "provenance"}

            try:
                record, matched_by = self.match_record(
                    kind=kind,
                    input_id=input_id,
                    path_id=path_id,
                    semantic=semantic,
                )
            except ReplayMissError:
                if branch_id in self.dirty_branches:
                    self.consume_matching_record_if_available(
                        kind=kind,
                        input_id=input_id,
                        path_id=path_id,
                    )
                    return {"action": "live", "matched_by": "dirty_branch"}
                raise

            record_uid = record["record_uid"]
            self.consumed_record_uids.add(record_uid)

            if (
                kind == "llm"
                and self.breakpoint_record_uid is not None
                and record_uid == self.breakpoint_record_uid
            ):
                self.mark_branch_dirty(branch_id)
                if self.override_output is not None:
                    return {"action": "override", "record": record, "matched_by": matched_by}
                if self.override_message is not None:
                    return {
                        "action": "override_message",
                        "record": record,
                        "matched_by": matched_by,
                    }
                if self.override_input is not None:
                    return {"action": "override_input", "record": record, "matched_by": matched_by}
                return {"action": "live", "record": record, "matched_by": matched_by}

            return {"action": "replay", "record": record, "matched_by": matched_by}

    def mark_branch_dirty(self, branch_id: str) -> None:
        self.dirty_branches.add(branch_id)

    def mark_branch_live(self, branch_id: str) -> None:
        self.branch_modes[branch_id] = "live"
        self.mark_branch_dirty(branch_id)

    def has_fork_dependency(self, provenance: dict[str, set[Source]] | None) -> bool:
        if not provenance:
            return False
        return any(self.is_fork_source(source) for source in provenance_sources(provenance))

    def is_fork_source(self, source: Source) -> bool:
        if source in self.fork_sources:
            return True
        return self.fork_run is not None and source.run_id == self.fork_run

    def consume_matching_record_if_available(
        self,
        *,
        kind: str,
        input_id: str,
        path_id: str,
    ) -> str | None:
        candidates = [
            record
            for record in self.history
            if record.get("kind") == kind
            and record.get("input_id") == input_id
            and record.get("record_uid") not in self.consumed_record_uids
        ]
        path_matches = [record for record in candidates if record.get("path_id") == path_id]
        if len(path_matches) == 1:
            self.consumed_record_uids.add(path_matches[0]["record_uid"])
            return "provenance+input_id+path_id"
        if len(candidates) == 1:
            self.consumed_record_uids.add(candidates[0]["record_uid"])
            return "provenance+input_id"
        path_only_matches = [
            record
            for record in self.history
            if record.get("kind") == kind
            and record.get("path_id") == path_id
            and record.get("record_uid") not in self.consumed_record_uids
        ]
        if len(path_only_matches) == 1:
            self.consumed_record_uids.add(path_only_matches[0]["record_uid"])
            return "provenance+path_id"
        return None

    def apply_override_input(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        patched_kwargs = dict(kwargs)
        if self.override_input is not None:
            patched_kwargs.update(self.override_input)
        return patched_kwargs

    def match_record(
        self,
        *,
        kind: str,
        input_id: str,
        path_id: str,
        semantic: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        candidates = [
            record
            for record in self.history
            if record.get("kind") == kind
            and record.get("input_id") == input_id
            and record.get("record_uid") not in self.consumed_record_uids
        ]
        if not candidates:
            return self.match_semantic_record(
                kind=kind,
                input_id=input_id,
                path_id=path_id,
                semantic=semantic,
            )

        if len(candidates) == 1:
            return candidates[0], "input_id"

        path_matches = [record for record in candidates if record.get("path_id") == path_id]
        if len(path_matches) == 1:
            return path_matches[0], "input_id+path_id"

        from .errors import AmbiguousReplayError

        raise AmbiguousReplayError(
            "Multiple records have the same input_id, and path_id cannot disambiguate: "
            f"input_id={input_id}, path_id={path_id}, candidates="
            f"{[record.get('record_uid') for record in candidates]}"
        )

    def match_semantic_record(
        self,
        *,
        kind: str,
        input_id: str,
        path_id: str,
        semantic: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], str]:
        if not self.semantic_fallback:
            raise_replay_miss(kind=kind, input_id=input_id, path_id=path_id)
        fingerprint = semantic_callsite_fingerprint(semantic)
        if fingerprint is None:
            raise_replay_miss(kind=kind, input_id=input_id, path_id=path_id)

        candidates = [
            record
            for record in self.history
            if record.get("kind") == kind
            and record.get("record_uid") not in self.consumed_record_uids
            and record_callsite_fingerprint(record) == fingerprint
        ]
        if not candidates:
            raise_replay_miss(kind=kind, input_id=input_id, path_id=path_id)

        if len(candidates) == 1:
            return candidates[0], "semantic"

        path_matches = [record for record in candidates if record.get("path_id") == path_id]
        if len(path_matches) == 1:
            return path_matches[0], "semantic+path_id"

        from .errors import AmbiguousReplayError

        raise AmbiguousReplayError(
            "Multiple semantic replay records have the same callsite_fingerprint, "
            "and path_id cannot disambiguate: "
            f"kind={kind}, callsite_fingerprint={fingerprint}, input_id={input_id}, path_id={path_id}, candidates="
            f"{[record.get('record_uid') for record in candidates]}"
        )

    def write_fork_record(
        self,
        record: dict[str, Any],
        edge_records: list[dict[str, Any]] | None = None,
        *,
        extra_records_factory=None,
    ) -> None:
        self.ensure_fork_writer()
        with self.lock:
            if self.fork_writer is None:
                raise RuntimeError("Replay fork writer is not open.")
            self.fork_writer.write(record)
            extra_records = (
                extra_records_factory(record)
                if extra_records_factory is not None
                else []
            )
            for extra_record in extra_records:
                self.fork_writer.write(extra_record)
            for edge_record in edge_records or []:
                self.fork_writer.write(edge_record)

    def write_fork_record_with_edges(
        self,
        record: dict[str, Any],
        prepared: PreparedCall,
        *,
        extra_records_factory=None,
    ) -> None:
        self.ensure_fork_writer()
        source = source_for_record(self.fork_run or self.base_run, record)
        self.write_fork_record(
            record,
            edge_records_for(prepared, source),
            extra_records_factory=extra_records_factory,
        )
        self.fork_sources.add(source)
        self.mark_branch_dirty(current_branch_id.get())

    def ensure_fork_writer(self) -> None:
        with self.lock:
            if self.fork_writer is not None:
                return
            if self.fork_run is None:
                self.fork_run = next_fork_run_id(self.log_dir, self.base_run)
            self.fork_writer = JsonlWriter(run_path(self.log_dir, self.fork_run), overwrite=True)
            self.fork_writer.write(
                {
                    "fork_metadata": {
                        "base_run": self.base_run,
                        "breakpoint_record_uid": self.breakpoint_record_uid,
                        "created_at": iso_now(),
                        "mode": "fork",
                    }
                }
            )


def prepare_llm_call(
    session: BaseSession,
    kwargs: dict[str, Any],
    *,
    semantic_hint: str | None = None,
    provider: str = "openai",
    api: str = "chat.completions.create",
) -> PreparedCall:
    semantic = None
    provenance: dict[str, set[Source]] = {"data_sources": set(), "control_sources": set()}
    if RUNTIME.enabled:
        provenance = RUNTIME.capture_input_provenance(kwargs=kwargs)
        callsite = capture_callsite()
        semantic = {
            "callsite_fingerprint": semantic_hint
            or RUNTIME.current_record_semantic_hint()
            or callsite_fingerprint(callsite)
        }

    input_record, input_id = prepare_replayed_llm_input(
        RUNTIME.plain_snapshot(kwargs),
        provider=provider,
        api=api,
    )
    path_id = session.allocate_path_id()
    return PreparedCall(
        input_record=input_record,
        input_id=input_id,
        path_id=path_id,
        provenance=provenance,
        semantic=semantic,
    )


def prepare_replayed_llm_input(
    kwargs: dict[str, Any],
    *,
    provider: str = "openai",
    api: str = "chat.completions.create",
) -> tuple[dict[str, Any], str]:
    input_record = build_input_record(RUNTIME.plain_snapshot(kwargs), provider=provider, api=api)
    input_id = compute_input_id(input_record)
    return input_record, input_id


def prepare_tool_event(
    *,
    input_record: dict[str, Any],
    input_id: str,
    path_id: str,
    input_arguments: Any = None,
    semantic_hint: str | None = None,
) -> PreparedCall:
    semantic = None
    provenance: dict[str, set[Source]] = {"data_sources": set(), "control_sources": set()}
    if RUNTIME.enabled:
        provenance = RUNTIME.capture_input_provenance(input_arguments)
        callsite = capture_callsite()
        semantic = {
            "callsite_fingerprint": semantic_hint
            or RUNTIME.current_record_semantic_hint()
            or callsite_fingerprint(callsite)
        }
    return PreparedCall(
        input_record=RUNTIME.plain_snapshot(input_record),
        input_id=input_id,
        path_id=path_id,
        provenance=provenance,
        semantic=semantic,
    )


def semantic_callsite_fingerprint(semantic: dict[str, Any] | None) -> str | None:
    if not isinstance(semantic, dict):
        return None
    fingerprint = semantic.get("callsite_fingerprint")
    return fingerprint if isinstance(fingerprint, str) and fingerprint else None


def record_callsite_fingerprint(record: dict[str, Any]) -> str | None:
    metadata = record.get("metadata")
    semantic = metadata.get("semantic") if isinstance(metadata, dict) else None
    return semantic_callsite_fingerprint(semantic)


def raise_replay_miss(*, kind: str, input_id: str, path_id: str) -> None:
    raise ReplayMissError(f"No replay record found for kind={kind}, input_id={input_id}, path_id={path_id}.")


def source_for_record(run_id: str, record: dict[str, Any]) -> Source:
    return Source(
        run_id=run_id,
        record_uid=record["record_uid"],
        kind=record["kind"],
        path_id=record.get("path_id"),
    )


def edge_records_for(prepared: PreparedCall, to_source: Source) -> list[dict[str, Any]]:
    if prepared.semantic is None:
        return []
    return [
        *make_edge_records(prepared.provenance.get("data_sources"), to_source, "data"),
        *make_edge_records(prepared.provenance.get("control_sources"), to_source, "control"),
    ]


def provenance_sources(provenance: dict[str, set[Source]]) -> set[Source]:
    return set(provenance.get("data_sources") or set()) | set(provenance.get("control_sources") or set())


def callsite_fingerprint(callsite: dict[str, Any]) -> str | None:
    if callsite.get("file") is None:
        return None
    return f"{callsite.get('file')}:{callsite.get('function')}:{callsite.get('line')}"


def capture_callsite() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    for frame in inspect.stack()[2:]:
        filename = Path(frame.filename).resolve()
        if package_root not in filename.parents:
            return {
                "file": str(filename),
                "line": frame.lineno,
                "function": frame.function,
            }
    return {"file": None, "line": None, "function": None}


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
