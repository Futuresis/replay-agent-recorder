from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from replay.tests.test_graph_ir import _synthetic_compare_records, _synthetic_records


ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = ROOT / "replay" / "tmp-visualize-tests"
TRACE = TMP_ROOT / "fixtures" / "base.jsonl"
BASE_TRACE = TMP_ROOT / "fixtures" / "compare-base.jsonl"
FORK_TRACE = TMP_ROOT / "fixtures" / "compare-fork.jsonl"
FOCUS_NODE = "base_run:rec_000001"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def _ensure_fixtures() -> None:
    _write_jsonl(TRACE, _synthetic_records())
    compare_records = _synthetic_compare_records()
    _write_jsonl(BASE_TRACE, [record for record in compare_records if record.get("run_id") == "base_run"])
    _write_jsonl(
        FORK_TRACE,
        [
            record
            for record in compare_records
            if record.get("_graph_run_id") == "fork_run" or record.get("run_id") == "fork_run"
        ],
    )


def _output_dir(name: str) -> Path:
    path = TMP_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_replay_graph(*args: str) -> subprocess.CompletedProcess[str]:
    _ensure_fixtures()
    return subprocess.run(
        [sys.executable, "-m", "replay", "graph", *(str(arg) for arg in args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


class VisualizeCLITests(unittest.TestCase):
    def test_python_cli_contract_exposes_record_and_replay_options(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "replay", "python", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--run-id", result.stdout)
        self.assertIn("--base-run", result.stdout)
        self.assertIn("--breakpoint-record-uid", result.stdout)
        self.assertIn("--override-output", result.stdout)

    def test_simplified_cli_contract_exposes_script_commands(self) -> None:
        for command in ("record", "replay", "fork"):
            result = subprocess.run(
                [sys.executable, "-m", "replay", command, "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("script", result.stdout)

    def test_fork_cli_contract_requires_breakpoint_and_override(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "replay", "fork", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--breakpoint-record-uid", result.stdout)
        self.assertIn("--override-output", result.stdout)
        self.assertIn("--override-message-json", result.stdout)
        self.assertIn("--override-input-json", result.stdout)

    def test_graph_summary_cli_contract_accepts_trace_path(self) -> None:
        result = _run_replay_graph("summary", TRACE)

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertGreater(summary["node_count"], 0)
        self.assertTrue(summary["edge_kinds"])

    def test_graph_export_ir_cli_contract_writes_json_file(self) -> None:
        output_path = _output_dir("cli-export-ir") / "graph.json"

        result = _run_replay_graph("export-ir", TRACE, "--output", str(output_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        graph_ir = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(graph_ir["schema_version"], 1)
        self.assertTrue(graph_ir["graph"]["nodes"])
        self.assertTrue(graph_ir["graph"]["timeline"]["items"])
        self.assertIn("diff", graph_ir["graph"])
        self.assertTrue(graph_ir["evidence"]["items"])

    def test_graph_mermaid_cli_contract_writes_markdown_file(self) -> None:
        output_path = _output_dir("cli-mermaid") / "graph.md"

        result = _run_replay_graph("mermaid", TRACE, "--group-by", "run", "--output", str(output_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        mermaid = output_path.read_text(encoding="utf-8")
        self.assertIn("flowchart", mermaid)
        self.assertIn(FOCUS_NODE, mermaid)

    def test_graph_html_cli_contract_writes_interactive_html(self) -> None:
        output_path = _output_dir("cli-html") / "graph.html"

        result = _run_replay_graph("html", TRACE, "--output", str(output_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        html = output_path.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('id="graph-svg"', html)
        self.assertIn(FOCUS_NODE, html)

    def test_graph_html_cli_contract_accepts_focus_and_vendored_assets(self) -> None:
        tmp_path = _output_dir("cli-html-vendored")
        output_path = tmp_path / "focus.html"

        result = _run_replay_graph(
            "html",
            TRACE,
            "--focus",
            FOCUS_NODE,
            "--direction",
            "downstream",
            "--max-depth",
            "1",
            "--asset-mode",
            "vendored",
            "--output",
            str(output_path),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output_path.exists())
        self.assertTrue((tmp_path / "visualize_assets" / "visualize.css").exists())
        self.assertTrue((tmp_path / "visualize_assets" / "visualize.js").exists())

    def test_graph_html_cli_contract_accepts_xyflow_renderer(self) -> None:
        tmp_path = _output_dir("cli-html-xyflow")
        output_path = tmp_path / "graph.html"

        result = _run_replay_graph(
            "html",
            TRACE,
            "--renderer",
            "xyflow",
            "--asset-mode",
            "vendored",
            "--output",
            str(output_path),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        html = output_path.read_text(encoding="utf-8")
        self.assertIn('data-renderer="xyflow"', html)
        self.assertIn('id="root"', html)
        self.assertIn('<script src="xyflow_assets/xyflow-viewer.js"></script>', html)
        self.assertTrue((tmp_path / "xyflow_assets" / "xyflow-viewer.css").exists())
        self.assertTrue((tmp_path / "xyflow_assets" / "xyflow-viewer.js").exists())

    def test_graph_summary_cli_contract_accepts_fork_trace_option(self) -> None:
        result = _run_replay_graph("summary", BASE_TRACE, "--fork", FORK_TRACE)

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["run_roles"]["base"], 1)
        self.assertEqual(summary["run_roles"]["fork"], 1)
        self.assertGreater(summary["timeline_count"], 0)
        self.assertGreaterEqual(summary["cross_run_edge_count"], 1)


if __name__ == "__main__":
    unittest.main()
