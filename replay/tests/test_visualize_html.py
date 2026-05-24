from __future__ import annotations

import unittest
from pathlib import Path

from replay.tests.test_graph_ir import _synthetic_compare_records, _synthetic_records
from replay.graph_ir import build_graph_ir
from replay.visualize import graph_ir_to_html, graph_ir_to_xyflow_html, write_html_graph


ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = ROOT / "replay" / "tmp-visualize-tests"


def _output_dir(name: str) -> Path:
    path = TMP_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class VisualizeHTMLTests(unittest.TestCase):
    def test_graph_ir_to_html_returns_offline_interactive_shell(self) -> None:
        ir = build_graph_ir(_synthetic_records(), title="Synthetic graph")

        html = graph_ir_to_html(ir, title="Synthetic graph")

        self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn('id="graph-svg"', html)
        self.assertIn('id="inspector"', html)
        self.assertIn('id="timeline-panel"', html)
        self.assertIn('id="search-results"', html)
        self.assertIn("edge_layers", html)
        self.assertIn("display edges", html)
        self.assertIn('data-action="apply-focus"', html)
        self.assertIn('data-action="apply-grouping"', html)
        self.assertIn("renderNodeInspector", html)
        self.assertIn("renderEdgeInspector", html)
        self.assertIn("copy_cli_snippet.replay_breakpoint", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("//cdn", html.lower())

    def test_graph_ir_to_html_includes_structured_inspector_tabs(self) -> None:
        ir = build_graph_ir(_synthetic_compare_records())

        html = graph_ir_to_html(ir)

        self.assertIn(">Summary<", html)
        self.assertIn(">Payload<", html)
        self.assertIn(">Evidence<", html)
        self.assertIn(">Actions<", html)
        self.assertIn("Fork boundary", html)
        self.assertIn("renderDiffCard", html)
        self.assertIn("show-fork-downstream", html)
        self.assertIn("changed", html)

    def test_write_html_graph_vendored_mode_writes_adjacent_assets(self) -> None:
        ir = build_graph_ir(_synthetic_records())

        tmp_path = _output_dir("html-vendored")
        output_path = write_html_graph(ir, tmp_path / "graph.html", asset_mode="vendored")

        html = output_path.read_text(encoding="utf-8")
        self.assertIn('data-asset-mode="vendored"', html)
        self.assertTrue((tmp_path / "visualize_assets" / "visualize.css").exists())
        self.assertTrue((tmp_path / "visualize_assets" / "visualize.js").exists())
        self.assertNotIn("https://", html)
        self.assertNotIn("//cdn", html.lower())

    def test_graph_ir_to_xyflow_html_returns_static_react_flow_shell(self) -> None:
        ir = build_graph_ir(_synthetic_records(), title="Synthetic graph")

        html = graph_ir_to_xyflow_html(ir, title="Synthetic graph")

        self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn('data-renderer="xyflow"', html)
        self.assertIn('id="root"', html)
        self.assertIn('id="graph-ir-data"', html)
        self.assertIn("react-flow", html)
        self.assertIn("xy-app", html)
        self.assertIn("display", html)
        self.assertIn("工具调用", html)
        self.assertNotIn('<script src="http', html)
        self.assertNotIn('<link rel="stylesheet" href="http', html)
        self.assertNotIn("//cdn", html.lower())

    def test_write_html_graph_xyflow_vendored_mode_writes_adjacent_assets(self) -> None:
        ir = build_graph_ir(_synthetic_records())

        tmp_path = _output_dir("html-xyflow-vendored")
        output_path = write_html_graph(
            ir,
            tmp_path / "graph.html",
            asset_mode="vendored",
            renderer="xyflow",
        )

        html = output_path.read_text(encoding="utf-8")
        self.assertIn('data-renderer="xyflow"', html)
        self.assertIn('<script src="xyflow_assets/xyflow-viewer.js"></script>', html)
        self.assertTrue((tmp_path / "xyflow_assets" / "xyflow-viewer.css").exists())
        self.assertTrue((tmp_path / "xyflow_assets" / "xyflow-viewer.js").exists())


if __name__ == "__main__":
    unittest.main()
