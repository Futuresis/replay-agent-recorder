from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def test_install_from_env_noop_without_autoinstall(monkeypatch: Any) -> None:
    import replay.bootstrap as bootstrap

    calls: list[dict[str, Any]] = []
    monkeypatch.delenv("REPLAY_AUTOINSTALL", raising=False)
    monkeypatch.setattr(bootstrap, "replay_install", lambda **kwargs: calls.append(kwargs))

    bootstrap.install_from_env()

    assert calls == []


def test_install_from_env_installs_framework_and_autosession(monkeypatch: Any, tmp_path: Path) -> None:
    import replay.bootstrap as bootstrap
    from replay.autosession import AutoSessionConfig

    install_calls: list[dict[str, Any]] = []
    session_configs: list[AutoSessionConfig] = []
    monkeypatch.setenv("REPLAY_AUTOINSTALL", "1")
    monkeypatch.setenv("REPLAY_FRAMEWORK", "langgraph")
    monkeypatch.setenv("REPLAY_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("REPLAY_NO_SEMANTIC", "1")
    monkeypatch.setenv("REPLAY_INCLUDE", os.pathsep.join(("agent/**/*.py", "src/**/*.py")))
    monkeypatch.setenv("REPLAY_EXCLUDE", ".venv/**")
    monkeypatch.setenv("REPLAY_AUTO_SESSION", "1")
    monkeypatch.setenv("REPLAY_MODE", "record")
    monkeypatch.setenv("REPLAY_RUN_ID_TEMPLATE", "{thread_id}")
    monkeypatch.setenv("REPLAY_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(bootstrap, "replay_install", lambda **kwargs: install_calls.append(kwargs))
    monkeypatch.setattr(bootstrap, "enable_auto_session", lambda config: session_configs.append(config) or object())

    bootstrap.install_from_env()

    assert install_calls == [
        {
            "semantic": False,
            "project_root": tmp_path,
            "include": ("agent/**/*.py", "src/**/*.py"),
            "exclude": (".venv/**",),
            "langchain": False,
            "langgraph": True,
        }
    ]
    assert len(session_configs) == 1
    assert session_configs[0].mode == "record"
    assert session_configs[0].run_id_template == "{thread_id}"
    assert session_configs[0].log_dir == tmp_path / "runs"


def test_install_from_env_framework_none_disables_framework_patches(monkeypatch: Any) -> None:
    import replay.bootstrap as bootstrap

    install_calls: list[dict[str, Any]] = []
    monkeypatch.setenv("REPLAY_AUTOINSTALL", "1")
    monkeypatch.setenv("REPLAY_FRAMEWORK", "none")
    monkeypatch.setattr(bootstrap, "replay_install", lambda **kwargs: install_calls.append(kwargs))

    bootstrap.install_from_env()

    assert install_calls[0]["langchain"] is False
    assert install_calls[0]["langgraph"] is False


def test_install_from_env_uses_replay_run_id_when_template_missing(monkeypatch: Any) -> None:
    import replay.bootstrap as bootstrap
    from replay.autosession import AutoSessionConfig

    session_configs: list[AutoSessionConfig] = []
    monkeypatch.setenv("REPLAY_AUTOINSTALL", "1")
    monkeypatch.setenv("REPLAY_AUTO_SESSION", "1")
    monkeypatch.setenv("REPLAY_RUN_ID", "fixed-run-id")
    monkeypatch.delenv("REPLAY_RUN_ID_TEMPLATE", raising=False)
    monkeypatch.setattr(bootstrap, "replay_install", lambda **kwargs: None)
    monkeypatch.setattr(bootstrap, "enable_auto_session", lambda config: session_configs.append(config) or object())

    bootstrap.install_from_env()

    assert len(session_configs) == 1
    assert session_configs[0].run_id_template == "fixed-run-id"
