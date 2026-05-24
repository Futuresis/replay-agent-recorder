from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .context import get_current_session
from .integration import ReplayRunConfig, replay_session
from .langgraph_config import import_path_symbol, is_path_graph_ref, split_graph_ref

if TYPE_CHECKING:
    from .entrypoints import TargetEntry, TargetInvocation


DEFAULT_RUN_ID_TEMPLATE = "{method}-{path}-{request_id}"
_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ReplayASGIMiddleware:
    """Wrap HTTP requests in a Replay session."""

    def __init__(
        self,
        app: Any,
        *,
        replay_config_factory: Callable[[dict[str, Any]], ReplayRunConfig],
    ) -> None:
        self.app = app
        self.replay_config_factory = replay_config_factory

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or get_current_session() is not None:
            await self.app(scope, receive, send)
            return

        config = self.replay_config_factory(scope)
        with replay_session(config):
            await self.app(scope, receive, send)


async def run_asgi_entry(
    entry: TargetEntry,
    invocation: TargetInvocation,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    run_id_template: str = DEFAULT_RUN_ID_TEMPLATE,
    request_header_run_id: str | None = None,
    replay_config: ReplayRunConfig | None = None,
) -> None:
    """Import an ASGI app and serve it under Replay middleware."""

    from .entrypoints import EntryImportError, EntryInvocationError, import_symbol

    try:
        import uvicorn
    except ImportError as exc:
        raise EntryInvocationError(
            "ASGI serve mode requires uvicorn. Install uvicorn or run the app yourself with Replay bootstrap."
        ) from exc

    try:
        app = import_symbol(entry.entry, target_root=entry.target_root)
    except EntryImportError:
        app = _import_asgi_path_symbol(entry.entry, target_root=entry.target_root)
    base_config = replay_config or invocation.replay_config or ReplayRunConfig(mode="record")
    wrapped = ReplayASGIMiddleware(
        app,
        replay_config_factory=lambda scope: replay_config_for_request(
            base_config,
            scope,
            run_id_template=run_id_template,
            request_header_run_id=request_header_run_id,
        ),
    )
    config = uvicorn.Config(wrapped, host=host, port=port, reload=reload)
    server = uvicorn.Server(config)
    await server.serve()


def _import_asgi_path_symbol(ref: str, *, target_root: Path) -> Any:
    if not is_path_graph_ref(ref):
        from .entrypoints import EntryImportError

        raise EntryImportError(f"Failed to import module for entry {ref!r}.")

    left, symbol = split_graph_ref(ref)
    path = Path(left)
    if not path.is_absolute():
        path = target_root / path
    return import_path_symbol(path, symbol)


def replay_config_for_request(
    base_config: ReplayRunConfig,
    scope: dict[str, Any],
    *,
    run_id_template: str = DEFAULT_RUN_ID_TEMPLATE,
    request_header_run_id: str | None = None,
) -> ReplayRunConfig:
    """Return a per-request ReplayRunConfig derived from an ASGI scope."""

    values = request_context_from_scope(
        scope,
        request_header_run_id=request_header_run_id,
        unique_fallback=base_config.mode == "record",
    )
    request_run_id = format_run_id(run_id_template, values)
    if base_config.mode == "record":
        return replace(base_config, run_id=request_run_id)
    if base_config.mode == "replay":
        if not getattr(base_config, "base_run_explicit", False):
            return replace(base_config, base_run=request_run_id)
    return base_config


def request_context_from_scope(
    scope: dict[str, Any],
    *,
    request_header_run_id: str | None = None,
    unique_fallback: bool = False,
) -> dict[str, str]:
    """Build stable run-id template values from an ASGI HTTP scope."""

    headers = _headers_from_scope(scope)
    method = str(scope.get("method") or "HTTP").upper()
    raw_path = str(scope.get("path") or "/")
    query = scope.get("query_string") or b""
    query_bytes = query if isinstance(query, bytes) else str(query).encode("utf-8")
    client = scope.get("client") or ("unknown", "")
    client_text = "-".join(str(item) for item in client if item not in (None, ""))
    request_id = _request_id_from_headers(headers, request_header_run_id=request_header_run_id)
    if not request_id:
        stable = _short_hash(f"{method}:{raw_path}:".encode("utf-8") + query_bytes)
        if unique_fallback:
            request_id = f"{stable}-{uuid.uuid4().hex[:8]}"
        else:
            request_id = stable
    return {
        "method": sanitize_run_id_part(method),
        "path": sanitize_run_id_part(raw_path.strip("/") or "root"),
        "query_hash": _short_hash(query_bytes),
        "request_id": sanitize_run_id_part(request_id),
        "client": sanitize_run_id_part(client_text or "unknown"),
    }


def format_run_id(template: str, values: dict[str, str]) -> str:
    """Format and sanitize a Replay run id."""

    rendered = template.format_map(_DefaultFormatValues(values))
    return sanitize_run_id_part(rendered, limit=160)


def sanitize_run_id_part(value: Any, *, limit: int = 160) -> str:
    text = str(value) if value not in (None, "") else "unknown"
    text = text.strip().strip("/")
    text = _RUN_ID_SAFE.sub("-", text)
    text = text.strip("-")
    if not text:
        text = "unknown"
    return text[:limit]


def _request_id_from_headers(
    headers: dict[str, str],
    *,
    request_header_run_id: str | None,
) -> str | None:
    if request_header_run_id:
        value = headers.get(request_header_run_id.lower())
        if value:
            return value
    for name in ("x-request-id", "x-github-delivery"):
        value = headers.get(name)
        if value:
            return value
    slack_timestamp = headers.get("x-slack-request-timestamp")
    if slack_timestamp:
        return f"{slack_timestamp}-{_short_hash(slack_timestamp.encode('utf-8'))}"
    return None


def _headers_from_scope(scope: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in scope.get("headers", []) or []:
        key_text = key.decode("latin1").lower() if isinstance(key, bytes) else str(key).lower()
        value_text = value.decode("latin1") if isinstance(value, bytes) else str(value)
        headers[key_text] = value_text
    return headers


def _short_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:12]


class _DefaultFormatValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "unknown"
