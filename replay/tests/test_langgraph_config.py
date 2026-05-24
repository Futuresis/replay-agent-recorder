from __future__ import annotations

import json
from pathlib import Path

import pytest

from replay.langgraph_config import (
    LangGraphAppConfig,
    load_langgraph_config,
    resolve_langgraph_graph,
    resolve_langgraph_http_app,
    select_langgraph_graph,
)


def write_langgraph_json(project: Path, value: dict[str, object]) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "langgraph.json").write_text(json.dumps(value), encoding="utf-8")


def test_load_langgraph_config_reads_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_langgraph_json(
        project,
        {
            "graphs": {"agent": "agent.server:get_agent"},
            "env": "./.env",
            "dependencies": ["."],
            "http": {"app": "agent.webapp:app"},
        },
    )

    app = load_langgraph_config(project)

    assert app.path == (project / "langgraph.json").resolve()
    assert app.root == project.resolve()
    assert app.graphs == {"agent": "agent.server:get_agent"}
    assert app.env == "./.env"
    assert app.dependencies == (".",)
    assert app.http_app == "agent.webapp:app"
    assert app.raw is not None


def test_load_langgraph_config_validates_field_types(tmp_path: Path) -> None:
    project = tmp_path / "project"
    write_langgraph_json(project, {"graphs": ["not", "object"]})

    with pytest.raises(ValueError, match="langgraph.json.*graphs"):
        load_langgraph_config(project)

    write_langgraph_json(project, {"graphs": {"agent": "pkg:graph"}, "dependencies": [".", 3]})
    with pytest.raises(ValueError, match="langgraph.json.*dependencies"):
        load_langgraph_config(project)


def test_select_langgraph_graph_requires_name_when_multiple_are_available(tmp_path: Path) -> None:
    app = LangGraphAppConfig(
        path=tmp_path / "langgraph.json",
        root=tmp_path,
        graphs={"a": "pkg:a", "b": "pkg:b"},
    )

    with pytest.raises(ValueError, match="a.*b"):
        select_langgraph_graph(app, None)


def test_select_langgraph_graph_uses_only_graph_when_name_is_missing(tmp_path: Path) -> None:
    app = LangGraphAppConfig(
        path=tmp_path / "langgraph.json",
        root=tmp_path,
        graphs={"only": "pkg:graph"},
    )

    assert select_langgraph_graph(app, None) == ("only", "pkg:graph")


def test_resolve_langgraph_graph_keeps_module_refs(tmp_path: Path) -> None:
    app = LangGraphAppConfig(
        path=tmp_path / "langgraph.json",
        root=tmp_path,
        graphs={"agent": "agent.server:get_agent"},
    )

    resolved = resolve_langgraph_graph(app, "agent")

    assert resolved.name == "agent"
    assert resolved.raw_ref == "agent.server:get_agent"
    assert resolved.import_ref == "agent.server:get_agent"
    assert resolved.pythonpath_hints == ()


def test_resolve_langgraph_graph_converts_src_path_refs_to_import_refs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "my_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text("graph = object()\n", encoding="utf-8")
    write_langgraph_json(project, {"graphs": {"G": "./src/my_pkg/graph.py:graph"}})

    app = load_langgraph_config(project)
    resolved = resolve_langgraph_graph(app, "G")

    assert resolved.import_ref == "my_pkg.graph:graph"
    assert resolved.pythonpath_hints == ((project / "src").resolve(),)


def test_resolve_langgraph_graph_keeps_file_metadata_for_non_package_path_ref(tmp_path: Path) -> None:
    project = tmp_path / "project"
    graph_dir = project / "weird.dir"
    graph_dir.mkdir(parents=True)
    (graph_dir / "agent.py").write_text("graph = object()\n", encoding="utf-8")
    write_langgraph_json(project, {"graphs": {"agent": "./weird.dir/agent.py:graph"}})

    app = load_langgraph_config(project)
    resolved = resolve_langgraph_graph(app, "agent")

    assert resolved.import_ref == "weird.dir.agent:graph"
    assert resolved.pythonpath_hints == (project.resolve(),)
    assert resolved.path == (graph_dir / "agent.py").resolve()
    assert resolved.symbol == "graph"


def test_resolve_langgraph_http_app_path_ref(tmp_path: Path) -> None:
    project = tmp_path / "project"
    webapp_dir = project / "weird.dir"
    webapp_dir.mkdir(parents=True)
    (webapp_dir / "webapp.py").write_text("app = object()\n", encoding="utf-8")
    write_langgraph_json(
        project,
        {
            "graphs": {"agent": "pkg:graph"},
            "http": {"app": "./weird.dir/webapp.py:app"},
        },
    )

    cfg = load_langgraph_config(project)
    resolved = resolve_langgraph_http_app(cfg)

    assert resolved.import_ref == "weird.dir.webapp:app"
    assert resolved.path == (project / "weird.dir" / "webapp.py").resolve()
    assert resolved.symbol == "app"
