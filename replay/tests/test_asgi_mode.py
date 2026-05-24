from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import replay
from replay.entrypoints import (
    ResolvedEntryRef,
    TargetEntry,
    TargetInvocation,
    add_target_entry_arguments,
    parse_entry_ref,
    run_target_entry,
)
from replay.integration import ReplayRunConfig
from replay.storage import run_path


def test_parse_asgi_entry_and_serve_args() -> None:
    parser = argparse.ArgumentParser()
    add_target_entry_arguments(parser)

    parsed = parse_entry_ref("asgi:agent.webapp:app")
    args = parser.parse_args(
        [
            "--entry",
            "asgi:agent.webapp:app",
            "--serve",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--reload",
            "--run-id-template",
            "{method}-{path}",
            "--request-header-run-id",
            "x-run-id",
        ]
    )

    assert parsed == ResolvedEntryRef(kind="asgi", ref="agent.webapp:app", method="serve")
    assert args.serve is True
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.reload is True
    assert args.run_id_template == "{method}-{path}"
    assert args.request_header_run_id == "x-run-id"


def test_request_run_id_formatting_sanitizes_scope() -> None:
    from replay.asgi import format_run_id, request_context_from_scope

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/github/webhook",
        "query_string": b"repo=openai/codex",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"x-request-id", b"req/123")],
    }

    context = request_context_from_scope(scope)

    assert context["method"] == "POST"
    assert context["path"] == "github-webhook"
    assert context["request_id"] == "req-123"
    assert format_run_id("{method}-{path}-{request_id}", context) == "POST-github-webhook-req-123"


def test_asgi_middleware_opens_request_replay_session(tmp_path: Path) -> None:
    from replay.asgi import ReplayASGIMiddleware, replay_config_for_request

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await replay.invoke_tool("marker", {"x": 1}, lambda: {"ok": True})
        await send({"type": "http.response.body", "body": b"ok"})

    base_config = ReplayRunConfig(mode="record", log_dir=tmp_path, overwrite=True)
    wrapped = ReplayASGIMiddleware(
        app,
        replay_config_factory=lambda scope: replay_config_for_request(
            base_config,
            scope,
            run_id_template="{method}-{path}-{request_id}",
        ),
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(
        wrapped(
            {
                "type": "http",
                "method": "POST",
                "path": "/hook",
                "query_string": b"",
                "headers": [(b"x-request-id", b"abc")],
            },
            receive,
            send,
        )
    )

    run_file = run_path(tmp_path, "POST-hook-abc")
    assert [item["type"] for item in sent] == ["http.response.start", "http.response.body"]
    assert run_file.exists()
    records = [json.loads(line) for line in run_file.read_text(encoding="utf-8").splitlines()]
    assert any(record["kind"] == "tool" and record["input"]["tool_name"] == "marker" for record in records)


def test_asgi_replay_derives_base_run_when_base_run_not_explicit() -> None:
    from replay.asgi import replay_config_for_request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/hook",
        "query_string": b"",
        "headers": [(b"x-request-id", b"abc")],
    }
    base = ReplayRunConfig(
        mode="replay",
        run_id="default",
        base_run="default",
        base_run_explicit=False,
    )

    cfg = replay_config_for_request(base, scope, run_id_template="{method}-{path}-{request_id}")

    assert cfg.base_run == "POST-hook-abc"


def test_asgi_replay_respects_explicit_base_run() -> None:
    from replay.asgi import replay_config_for_request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/hook",
        "query_string": b"",
        "headers": [(b"x-request-id", b"abc")],
    }
    base = ReplayRunConfig(
        mode="replay",
        run_id="default",
        base_run="chosen",
        base_run_explicit=True,
    )

    cfg = replay_config_for_request(base, scope, run_id_template="{method}-{path}-{request_id}")

    assert cfg.base_run == "chosen"


def test_asgi_record_fallback_request_id_is_unique_without_headers() -> None:
    from replay.asgi import replay_config_for_request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/hook",
        "query_string": b"",
        "headers": [],
    }
    base = ReplayRunConfig(mode="record")

    cfg1 = replay_config_for_request(base, scope, run_id_template="{method}-{path}-{request_id}")
    cfg2 = replay_config_for_request(base, scope, run_id_template="{method}-{path}-{request_id}")

    assert cfg1.run_id != cfg2.run_id


def test_asgi_replay_fallback_request_id_is_stable_without_headers() -> None:
    from replay.asgi import replay_config_for_request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/hook",
        "query_string": b"",
        "headers": [],
    }
    base = ReplayRunConfig(mode="replay", run_id="default", base_run="default", base_run_explicit=False)

    cfg1 = replay_config_for_request(base, scope, run_id_template="{method}-{path}-{request_id}")
    cfg2 = replay_config_for_request(base, scope, run_id_template="{method}-{path}-{request_id}")

    assert cfg1.base_run == cfg2.base_run


def test_asgi_middleware_does_not_nest_existing_session(tmp_path: Path) -> None:
    from replay.asgi import ReplayASGIMiddleware
    from replay.integration import replay_session

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await replay.invoke_tool("marker", {"x": 1}, lambda: {"ok": True})

    wrapped = ReplayASGIMiddleware(
        app,
        replay_config_factory=lambda _scope: ReplayRunConfig(mode="record", run_id="request", log_dir=tmp_path),
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        return None

    with replay_session(ReplayRunConfig(mode="record", run_id="outer", log_dir=tmp_path)):
        asyncio.run(wrapped({"type": "http", "method": "GET", "path": "/", "headers": []}, receive, send))

    assert run_path(tmp_path, "outer").exists()
    assert not run_path(tmp_path, "request").exists()


def test_run_target_entry_serves_langgraph_http_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import replay.asgi

    project = tmp_path / "project"
    package = project / "agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "webapp.py").write_text("async def app(scope, receive, send):\n    pass\n", encoding="utf-8")
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.graph:graph"}, "http": {"app": "agent.webapp:app"}}),
        encoding="utf-8",
    )
    calls: list[tuple[TargetEntry, TargetInvocation, dict[str, Any]]] = []

    async def fake_run_asgi_entry(entry: TargetEntry, invocation: TargetInvocation, **kwargs: Any) -> None:
        calls.append((entry, invocation, kwargs))

    monkeypatch.setattr(replay.asgi, "run_asgi_entry", fake_run_asgi_entry)

    asyncio.run(
        run_target_entry(
            TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="http", target_root=project, method="serve"),
            TargetInvocation(serve=True, host="0.0.0.0", port=9000),
        )
    )

    assert calls
    assert calls[0][0].kind == "asgi"
    assert calls[0][0].entry == "agent.webapp:app"
    assert calls[0][2]["host"] == "0.0.0.0"
    assert calls[0][2]["port"] == 9000


def test_run_target_entry_serves_langgraph_http_app_path_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    webapp_dir = project / "weird.dir"
    webapp_dir.mkdir(parents=True)
    (webapp_dir / "webapp.py").write_text(
        "async def app(scope, receive, send):\n    pass\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps(
            {
                "graphs": {"agent": "agent.graph:graph"},
                "http": {"app": "./weird.dir/webapp.py:app"},
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    class FakeConfig:
        def __init__(self, app: Any, *, host: str, port: int, reload: bool) -> None:
            captured["app"] = app
            captured["host"] = host
            captured["port"] = port
            captured["reload"] = reload

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            captured["config"] = config

        async def serve(self) -> None:
            captured["served"] = True

    fake_uvicorn = type("FakeUvicorn", (), {"Config": FakeConfig, "Server": FakeServer})
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    asyncio.run(
        run_target_entry(
            TargetEntry(kind="langgraph-json", entry="langgraph.json", graph="http", target_root=project, method="serve"),
            TargetInvocation(serve=True, host="0.0.0.0", port=9000),
        )
    )

    assert captured["served"] is True
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
    assert captured["reload"] is False
    assert callable(captured["app"].app)
