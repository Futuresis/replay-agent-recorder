from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from replay.scaffold import scaffold_integration
from replay.scaffold_detect import (
    detect_integration_targets,
    detect_langgraph_json,
    detect_python_ast_entries,
    write_replay_target_config,
)


ROOT = Path(__file__).resolve().parents[2]


def test_detect_langgraph_json_selects_agent_graph(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps(
            {
                "graphs": {
                    "agent": "agent.server:get_agent",
                    "reviewer": "agent.reviewer:get_reviewer_agent",
                }
            }
        ),
        encoding="utf-8",
    )

    result = detect_integration_targets(project)

    entries = {candidate.entry for candidate in result.candidates}
    assert "langgraph.json#agent" in entries
    assert "langgraph.json#reviewer" in entries
    assert result.selected is not None
    assert result.selected.entry == "langgraph.json#agent"


def test_detect_python_ast_deepagents_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_deep_agent\n"
        "agent = create_deep_agent(model='x', tools=[])\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "pkg.agent:agent")
    assert candidate.kind == "runnable"
    assert candidate.confidence >= 0.8


def test_detect_python_ast_module_with_config_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_deep_agent, create_agent\n"
        "agent = create_deep_agent(model='x', tools=[]).with_config({'a': 1})\n"
        "runner = create_agent(model='x', tools=[]).with_config({'b': 2})\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    agent_candidate = next(item for item in candidates if item.entry == "pkg.agent:agent")
    runner_candidate = next(item for item in candidates if item.entry == "pkg.agent:runner")
    assert agent_candidate.kind == "runnable"
    assert runner_candidate.kind == "runnable"


def test_detect_python_ast_direct_stategraph_compile_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict).compile()\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "pkg.graph:graph")
    assert candidate.kind == "runnable"


def test_detect_python_ast_builder_backed_compile_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "builder = StateGraph(dict)\n"
        "graph = builder.compile()\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "pkg.graph:graph")
    assert candidate.kind == "runnable"


def test_detect_python_ast_annotated_builder_backed_compile_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "builder: StateGraph = StateGraph(dict)\n"
        "graph = builder.compile()\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "pkg.graph:graph")
    assert candidate.kind == "runnable"


def test_detect_python_ast_ignores_re_compile_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "import re\n"
        "pattern = re.compile('abc')\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "pkg.graph:pattern" not in entries


def test_detect_python_ast_ignores_unknown_compile_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "class Builder:\n"
        "    def compile(self):\n"
        "        return object()\n"
        "\n"
        "builder = Builder()\n"
        "graph = builder.compile()\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "pkg.graph:graph" not in entries


def test_detect_python_ast_ignores_container_agent_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_agent\n"
        "x = {'agent': create_agent(model='x', tools=[])}\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "pkg.agent:x" not in entries


def test_detect_python_ast_ignores_wrapper_compile_assignment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "def wrapper(value):\n"
        "    return value\n"
        "\n"
        "y = wrapper(StateGraph(dict).compile())\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "pkg.graph:y" not in entries


def test_detect_python_ast_factory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_deep_agent\n"
        "async def get_agent(config):\n"
        "    return create_deep_agent(model='x', tools=[])\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "factory:pkg.agent:get_agent")
    assert candidate.kind == "factory"
    assert candidate.requires_factory_config is True


def test_detect_python_ast_factory_with_direct_with_config_return(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_deep_agent\n"
        "def build_agent(config=None):\n"
        "    return create_deep_agent(model='x', tools=[]).with_config(config)\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "factory:pkg.agent:build_agent")
    assert candidate.kind == "factory"


def test_detect_python_ast_factory_with_assigned_agent_return(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_deep_agent\n"
        "async def get_agent(config):\n"
        "    agent = create_deep_agent(model='x', tools=[])\n"
        "    return agent\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "factory:pkg.agent:get_agent")
    assert candidate.kind == "factory"


def test_detect_python_ast_factory_with_assigned_compile_return(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "def build_agent():\n"
        "    builder = StateGraph(dict)\n"
        "    graph = builder.compile()\n"
        "    return graph\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "factory:pkg.agent:build_agent")
    assert candidate.kind == "factory"


def test_detect_python_ast_factory_with_annotated_builder_compile_return(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "def build_agent():\n"
        "    builder: StateGraph = StateGraph(dict)\n"
        "    graph = builder.compile()\n"
        "    return graph\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    candidate = next(item for item in candidates if item.entry == "factory:pkg.agent:build_agent")
    assert candidate.kind == "factory"


def test_detect_python_ast_ignores_factory_container_assignment_return(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_agent\n"
        "def helper():\n"
        "    x = {'agent': create_agent(model='x', tools=[])}\n"
        "    return x\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "factory:pkg.agent:helper" not in entries


def test_detect_python_ast_ignores_factory_wrapper_compile_return(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "def helper():\n"
        "    def wrapper(value):\n"
        "        return value\n"
        "\n"
        "    y = wrapper(StateGraph(dict).compile())\n"
        "    return y\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "factory:pkg.agent:helper" not in entries


def test_detect_python_ast_ignores_non_returned_create_agent_call(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        "from deepagents import create_agent\n"
        "def helper():\n"
        "    create_agent(model='x', tools=[])\n"
        "    return 'noop'\n",
        encoding="utf-8",
    )

    candidates = detect_python_ast_entries(project)

    entries = {item.entry for item in candidates}
    assert "factory:pkg.agent:helper" not in entries


def test_write_replay_target_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.server:get_agent"}}),
        encoding="utf-8",
    )
    result = detect_integration_targets(project)
    output = tmp_path / "replay_target.json"

    written = write_replay_target_config(output, result, target_root=project)
    data = json.loads(written.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["entry"] == "langgraph.json#agent"
    assert data["entry_kind"] == "langgraph-json"
    assert data["target_root"] == os.path.relpath(project.resolve(), start=output.parent.resolve()).replace(os.sep, "/")
    assert data["candidates"][0]["entry"] == "langgraph.json#agent"


def test_scaffold_integration_writes_replay_target_json_and_detected_readme(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.server:get_agent"}}),
        encoding="utf-8",
    )

    written = scaffold_integration(
        name="detect-agent",
        output_dir=tmp_path / "integrations",
        target_root=project,
        detect=True,
    )
    target = tmp_path / "integrations" / "detect_agent"

    assert target / "replay_target.json" in written
    data = json.loads((target / "replay_target.json").read_text(encoding="utf-8"))
    assert data["entry"] == "langgraph.json#agent"
    assert data["target_root"] == os.path.relpath(project.resolve(), start=target.resolve()).replace(os.sep, "/")
    assert "Detected target entry" in (target / "README.md").read_text(encoding="utf-8")
    compile((target / "runner.py").read_text(encoding="utf-8"), str(target / "runner.py"), "exec")


def test_generated_runner_uses_replay_target_framework_default(tmp_path: Path, monkeypatch: Any) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.server:get_agent"}}),
        encoding="utf-8",
    )
    scaffold_integration(
        name="detect-agent",
        output_dir=tmp_path / "integrations",
        target_root=project,
        detect=True,
        framework="none",
    )
    target = tmp_path / "integrations" / "detect_agent"
    target_config = target / "replay_target.json"
    data = json.loads(target_config.read_text(encoding="utf-8"))
    data["framework"] = "langgraph"
    target_config.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path / "integrations"))
    import detect_agent.runner as module

    args = module.build_parser().parse_args(["--input-json", "{}"])

    assert args.framework == "langgraph"


def test_generated_runner_runs_from_replay_target_defaults(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "server.py").write_text(
        "class Agent:\n"
        "    def invoke(self, input_value, config=None):\n"
        "        return {'echo': input_value, 'config': config}\n"
        "\n"
        "def get_agent(config):\n"
        "    return Agent()\n",
        encoding="utf-8",
    )
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.server:get_agent"}}),
        encoding="utf-8",
    )
    scaffold_integration(
        name="runner-defaults",
        output_dir=tmp_path / "integrations",
        target_root=project,
        detect=True,
        framework="none",
    )
    runner = tmp_path / "integrations" / "runner_defaults" / "runner.py"

    result = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--replay-mode",
            "none",
            "--input-json",
            '{"messages":[{"role":"user","content":"hello"}]}',
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"echo"' in result.stdout
    assert "hello" in result.stdout


def test_scaffold_cli_detect_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "agent.server:get_agent"}}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "replay",
            "scaffold",
            "integration",
            "--name",
            "detect-agent",
            "--output-dir",
            str(tmp_path / "integrations"),
            "--target-root",
            str(project),
            "--detect",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "integrations" / "detect_agent" / "replay_target.json").exists()
