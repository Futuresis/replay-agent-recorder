from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

from replay.entrypoints import (
    TargetEntry,
    add_target_entry_arguments,
    framework_install_flags,
    target_entry_from_args,
    target_env_files_from_args,
)
from replay.api import wrap_runnable
from replay.langgraph_patch import wrap_compiled_graph
from replay.target_env import load_env_file, parse_env_file, target_environment


def test_target_environment_chdir_sys_path_and_restore(tmp_path: Path) -> None:
    old_cwd = Path.cwd()
    old_path = sys.path[:]
    project = tmp_path / "project"
    project.mkdir()

    with target_environment(target_root=project) as info:
        assert Path.cwd() == project.resolve()
        assert sys.path[0] == str(project.resolve())
        assert info.target_root == project.resolve()
        assert info.target_cwd == project.resolve()

    assert Path.cwd() == old_cwd
    assert sys.path == old_path


def test_target_environment_resolves_relative_target_cwd_under_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    subdir = project / "apps" / "server"
    subdir.mkdir(parents=True)

    with target_environment(target_root=project, target_cwd=Path("apps/server")) as info:
        assert Path.cwd() == subdir.resolve()
        assert info.target_cwd == subdir.resolve()


def test_target_environment_keeps_absolute_target_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    with target_environment(target_root=project, target_cwd=other) as info:
        assert Path.cwd() == other.resolve()
        assert info.target_cwd == other.resolve()


def test_target_environment_defaults_target_cwd_to_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    with target_environment(target_root=project, target_cwd=None) as info:
        assert Path.cwd() == project.resolve()
        assert info.target_cwd == project.resolve()


def test_target_environment_adds_src_layout_before_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)

    with target_environment(target_root=project) as info:
        assert sys.path[:2] == [str(project.resolve()), str((project / "src").resolve())]
        assert info.pythonpath_entries[:2] == (project.resolve(), (project / "src").resolve())


def test_target_environment_loads_env_without_overriding_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "FOO=from-file\n"
        "BAR='quoted value'\n"
        'export BAZ="double quoted"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FOO", "existing")
    monkeypatch.delenv("BAR", raising=False)
    monkeypatch.delenv("BAZ", raising=False)

    with target_environment(target_root=project, env_files=[".env"]):
        assert os.environ["FOO"] == "existing"
        assert os.environ["BAR"] == "quoted value"
        assert os.environ["BAZ"] == "double quoted"

    assert os.environ["FOO"] == "existing"
    assert "BAR" not in os.environ
    assert "BAZ" not in os.environ


def test_load_env_file_can_override_existing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "existing")

    changed = load_env_file(env_path, override=True)

    assert changed == {"FOO": "from-file"}
    assert os.environ["FOO"] == "from-file"


def test_parse_env_file_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n"
        "# comment\n"
        "export A=1\n"
        "B = two\n",
        encoding="utf-8",
    )

    assert parse_env_file(env_path) == {"A": "1", "B": "two"}


def test_framework_install_flags() -> None:
    assert framework_install_flags("auto") == (True, True)
    assert framework_install_flags("both") == (True, True)
    assert framework_install_flags("none") == (False, False)
    assert framework_install_flags("langchain") == (True, False)
    assert framework_install_flags("langgraph") == (False, True)


def test_target_env_files_from_args_includes_explicit_and_langgraph_env(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        '{"graphs":{"G":"pkg:graph"},"env":"./.env"}',
        encoding="utf-8",
    )

    parser = argparse.ArgumentParser()
    add_target_entry_arguments(parser)
    args = parser.parse_args(
        [
            "--target-root",
            str(project),
            "--entry",
            "langgraph.json#G",
            "--env-file",
            ".custom.env",
            "--env-file",
            ".custom.env",
        ]
    )
    entry = target_entry_from_args(args)

    assert target_env_files_from_args(args, entry) == (Path(".custom.env"), Path("./.env"))


def test_target_env_files_from_args_ignores_non_langgraph_entries() -> None:
    args = argparse.Namespace(env_file=[Path(".env")])
    entry = TargetEntry(entry="pkg.agent:agent", kind="import")

    assert target_env_files_from_args(args, entry) == (Path(".env"),)


def test_wrap_runnable_is_idempotent_for_fake_compiled_graph() -> None:
    class FakeCompiled:
        def invoke(self, input_value):
            return {"input": input_value}

    graph = FakeCompiled()

    assert wrap_compiled_graph(graph) is graph
    assert wrap_compiled_graph(graph) is graph
    assert wrap_runnable(graph) is graph
    assert graph.__replay_langgraph_run_wrapper__ is True
    assert graph.invoke({"x": 1}) == {"input": {"x": 1}}


def test_wrap_compiled_graph_returns_original_when_methods_readonly() -> None:
    class UnpatchableCompiled:
        def invoke(self, input_value):
            return {"input": input_value}

        def __setattr__(self, name, value):
            if name in {"invoke", "ainvoke", "stream", "astream", "__replay_langgraph_run_wrapper__"}:
                raise AttributeError("readonly")
            object.__setattr__(self, name, value)

    graph = UnpatchableCompiled()

    assert wrap_compiled_graph(graph) is graph
    assert graph.invoke({"x": 1}) == {"input": {"x": 1}}


def test_wrap_compiled_graph_returns_original_when_methods_readonly() -> None:
    class UnpatchableCompiled:
        def invoke(self, input_value):
            return {"input": input_value}

        def __setattr__(self, name, value):
            if name in {
                "invoke",
                "ainvoke",
                "stream",
                "astream",
                "__replay_langgraph_run_wrapper__",
            }:
                raise AttributeError("readonly")
            object.__setattr__(self, name, value)

    graph = UnpatchableCompiled()

    assert wrap_compiled_graph(graph) is graph
    assert graph.invoke({"x": 1}) == {"input": {"x": 1}}
    assert not hasattr(graph, "__replay_langgraph_run_wrapper__")
