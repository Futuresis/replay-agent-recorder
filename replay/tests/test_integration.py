from __future__ import annotations

import argparse
import os
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import replay
from replay.integration import add_replay_arguments, config_from_args, replay_session
from replay.scaffold import scaffold_integration


ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = ROOT / "replay" / "tmp-integration-tests"


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not pythonpath else f"{ROOT}{os.pathsep}{pythonpath}"
    return env


class IntegrationHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)

    def test_replay_arguments_build_record_config_with_file_override(self) -> None:
        parser = argparse.ArgumentParser()
        add_replay_arguments(parser, default_run_id="default-run", default_log_dir=TMP_ROOT / "runs")
        override_path = TMP_ROOT / "override.json"
        override_path.write_text(json.dumps({"messages": [{"role": "user", "content": "patched"}]}), encoding="utf-8")

        args = parser.parse_args(
            [
                "--replay-mode",
                "replay",
                "--breakpoint-record-uid",
                "rec_000001",
                "--override-input-file",
                str(override_path),
                "--include",
                "src/*.py",
            ]
        )

        config = config_from_args(args, default_project_root=TMP_ROOT)

        self.assertEqual(config.mode, "replay")
        self.assertEqual(config.run_id, "default-run")
        self.assertEqual(config.base_run, "default-run")
        self.assertEqual(config.log_dir, TMP_ROOT / "runs")
        self.assertEqual(config.override_input, {"messages": [{"role": "user", "content": "patched"}]})
        self.assertEqual(config.project_root, TMP_ROOT)
        self.assertEqual(config.include, ("src/*.py",))

    def test_replay_arguments_reject_multiple_override_shapes(self) -> None:
        parser = argparse.ArgumentParser()
        add_replay_arguments(parser, default_run_id="default-run")
        args = parser.parse_args(
            [
                "--breakpoint-record-uid",
                "rec_000001",
                "--override-output",
                "content",
                "--override-message-json",
                '{"content":"message"}',
            ]
        )

        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            config_from_args(args)

    def test_replay_session_none_is_noop(self) -> None:
        parser = argparse.ArgumentParser()
        add_replay_arguments(parser, default_run_id="default-run")
        args = parser.parse_args(["--replay-mode", "none"])
        config = config_from_args(args)

        with replay_session(config):
            marker = "ran"

        self.assertEqual(marker, "ran")

    def test_replay_session_record_writes_trace(self) -> None:
        log_dir = TMP_ROOT / "runs"
        parser = argparse.ArgumentParser()
        add_replay_arguments(parser, default_run_id="session-run", default_log_dir=log_dir)
        config = config_from_args(parser.parse_args([]))

        with replay_session(config):
            replay.invoke_tool_sync("integration-marker", {"value": 1}, lambda: {"ok": True})

        trace_path = log_dir / "session-run.jsonl"
        self.assertTrue(trace_path.exists())
        records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[0]["kind"], "tool")
        self.assertEqual(records[0]["input"]["tool_name"], "integration-marker")

    def test_class_method_adapter_records_replays_and_filters_tools(self) -> None:
        log_dir = TMP_ROOT / "class-method-runs"

        class FrameworkClient:
            def __init__(self) -> None:
                self.count = 0

            def call_tool(self, name: str, **kwargs: object) -> dict[str, object]:
                self.count += 1
                return {"tool": name, "kwargs": kwargs, "count": self.count}

        def arguments_factory(args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
            return dict(kwargs)

        client = FrameworkClient()
        adapter = replay.ClassMethodToolAdapter(
            FrameworkClient,
            "call_tool",
            namespace="framework",
            arguments_factory=arguments_factory,
            tool_filter="search",
        )
        adapter.install()
        try:
            with replay.record("class-method", log_dir=log_dir):
                self.assertEqual(
                    client.call_tool("search", query="alpha"),
                    {"tool": "search", "kwargs": {"query": "alpha"}, "count": 1},
                )
                self.assertEqual(
                    client.call_tool("untracked", query="beta"),
                    {"tool": "untracked", "kwargs": {"query": "beta"}, "count": 2},
                )

            records = [
                json.loads(line)
                for line in (log_dir / "class-method.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["input"]["tool_name"], "framework:search")
            self.assertEqual(records[0]["input"]["arguments"], {"query": "alpha"})

            client.count = 0
            with replay.replay(base_run="class-method", log_dir=log_dir):
                self.assertEqual(
                    client.call_tool("search", query="alpha"),
                    {"tool": "search", "kwargs": {"query": "alpha"}, "count": 1},
                )
                self.assertEqual(client.count, 0)
                self.assertEqual(
                    client.call_tool("untracked", query="beta"),
                    {"tool": "untracked", "kwargs": {"query": "beta"}, "count": 1},
                )
        finally:
            adapter.uninstall()


class ScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)

    def test_scaffold_integration_writes_generic_wrapper(self) -> None:
        written = scaffold_integration(
            name="My External Agent",
            output_dir=TMP_ROOT,
            tool_style="class-method",
            framework="both",
        )
        target = TMP_ROOT / "my_external_agent"

        self.assertEqual(len(written), 5)
        self.assertTrue((target / "runner.py").exists())
        self.assertTrue((target / "tool_adapter.py").exists())
        self.assertTrue((target / "replay_target.json").exists())
        runner = (target / "runner.py").read_text(encoding="utf-8")
        adapter = (target / "tool_adapter.py").read_text(encoding="utf-8")
        self.assertIn("add_replay_arguments", runner)
        self.assertIn("add_target_entry_arguments", runner)
        self.assertIn("run_target_entry_blocking", runner)
        self.assertIn("target_environment", runner)
        self.assertIn("framework_install_flags", runner)
        self.assertIn("args.framework", runner)
        self.assertIn("replay_session", runner)
        self.assertIn("ClassMethodToolAdapter", adapter)
        readme = (target / "README.md").read_text(encoding="utf-8")
        self.assertIn("langgraph.json#GraphName", readme)
        self.assertIn("Framework patch mode: `both`", readme)
        self.assertTrue(readme.startswith("# "))
        self.assertNotIn("\n        #", readme)
        compile(runner, str(target / "runner.py"), "exec")
        compile(adapter, str(target / "tool_adapter.py"), "exec")

    def test_scaffold_integration_defaults_framework_auto(self) -> None:
        scaffold_integration(
            name="Default Agent",
            output_dir=TMP_ROOT,
        )
        runner = (TMP_ROOT / "default_agent" / "runner.py").read_text(encoding="utf-8")
        readme = (TMP_ROOT / "default_agent" / "README.md").read_text(encoding="utf-8")
        replay_target = json.loads((TMP_ROOT / "default_agent" / "replay_target.json").read_text(encoding="utf-8"))

        self.assertIn('defaults.setdefault("framework", "auto")', runner)
        self.assertEqual(replay_target["framework"], "auto")
        self.assertIn("Framework patch mode: `auto`", readme)
        self.assertTrue(readme.startswith("# "))
        self.assertNotIn("\n        | Confidence", readme)

    def test_scaffold_runner_bootstraps_local_replay_before_import(self) -> None:
        scaffold_integration(
            name="Bootstrap Agent",
            output_dir=TMP_ROOT,
        )
        runner = (TMP_ROOT / "bootstrap_agent" / "runner.py").read_text(encoding="utf-8")

        self.assertIn("RUNNER_DIR = Path(__file__).resolve().parent", runner)
        self.assertIn("def _bootstrap_replay_package_path()", runner)
        self.assertIn("Path.cwd()", runner)
        self.assertLess(runner.index("RUNNER_DIR = Path(__file__).resolve().parent"), runner.index("import replay"))
        self.assertLess(runner.index("def _bootstrap_replay_package_path()"), runner.index("import replay"))
        self.assertLess(runner.index("_bootstrap_replay_package_path()"), runner.index("import replay"))
        self.assertLess(
            runner.index("if str(RUNNER_DIR) not in sys.path:\n    sys.path.insert(0, str(RUNNER_DIR))"),
            runner.index("import replay"),
        )

    def test_scaffold_runner_help_from_repo_root_without_pythonpath(self) -> None:
        scaffold_integration(
            name="Bootstrap Help Agent",
            output_dir=TMP_ROOT,
        )
        runner = TMP_ROOT / "bootstrap_help_agent" / "runner.py"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        result = subprocess.run(
            [
                sys.executable,
                "-S",
                str(runner),
                "--help",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("No module named 'replay'", result.stderr)

    def test_scaffold_cli_contract(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "replay",
                "scaffold",
                "integration",
                "--name",
                "cli-agent",
                "--output-dir",
                str(TMP_ROOT),
                "--tool-style",
                "mapping",
                "--framework",
                "both",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((TMP_ROOT / "cli_agent" / "runner.py").exists())
        runner = (TMP_ROOT / "cli_agent" / "runner.py").read_text(encoding="utf-8")
        readme = (TMP_ROOT / "cli_agent" / "README.md").read_text(encoding="utf-8")
        self.assertIn("framework_install_flags", runner)
        self.assertIn("Framework patch mode: `both`", readme)
        self.assertIn("tool_adapter.py", result.stdout)

    def test_scaffold_cli_rejects_removed_langchain_langgraph_flags(self) -> None:
        help_result = subprocess.run(
            [sys.executable, "-m", "replay", "scaffold", "integration", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertNotIn("--langchain", help_result.stdout)
        self.assertNotIn("--langgraph", help_result.stdout)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "replay",
                "scaffold",
                "integration",
                "--name",
                "cli-agent",
                "--output-dir",
                str(TMP_ROOT),
                "--langchain",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments: --langchain", result.stderr)

    def test_generated_runner_module_entry_imports_from_target_root_outside_cwd(self) -> None:
        scaffold_integration(
            name="Module Runner",
            output_dir=TMP_ROOT,
            tool_style="none",
        )
        project = TMP_ROOT / "target_project"
        package = project / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "helper.py").write_text("VALUE = 'from-helper'\n", encoding="utf-8")
        (package / "cli.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "from pkg.helper import VALUE\n"
            "Path(sys.argv[1]).write_text(VALUE + ':' + sys.argv[2], encoding='utf-8')\n",
            encoding="utf-8",
        )
        output = TMP_ROOT / "module-output.txt"

        result = subprocess.run(
            [
                sys.executable,
                str(TMP_ROOT / "module_runner" / "runner.py"),
                "--replay-mode",
                "none",
                "--target-root",
                str(project),
                "--entry",
                "module:pkg.cli",
                "--framework",
                "none",
                "--",
                str(output),
                "ok",
            ],
            cwd=TMP_ROOT,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "from-helper:ok")

    def test_generated_runner_import_runnable_entry_imports_from_target_root(self) -> None:
        scaffold_integration(
            name="Runnable Runner",
            output_dir=TMP_ROOT,
            tool_style="none",
        )
        project = TMP_ROOT / "runnable_project"
        package = project / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "agent.py").write_text(
            "class Agent:\n"
            "    def invoke(self, input, config=None, **kwargs):\n"
            "        return {'received': input, 'config': config}\n"
            "\n"
            "agent = Agent()\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(TMP_ROOT / "runnable_runner" / "runner.py"),
                "--replay-mode",
                "none",
                "--target-root",
                str(project),
                "--entry",
                "pkg.agent:agent",
                "--framework",
                "none",
                "--input-json",
                '{"message": "hello"}',
            ],
            cwd=TMP_ROOT,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"received": {"message": "hello"}})

    def test_generated_runner_script_entry_allows_target_asyncio_run(self) -> None:
        scaffold_integration(
            name="Script Runner",
            output_dir=TMP_ROOT,
            tool_style="none",
        )
        project = TMP_ROOT / "script_project"
        project.mkdir()
        script = project / "main.py"
        output = TMP_ROOT / "script-output.txt"
        script.write_text(
            "import asyncio\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "async def main():\n"
            "    Path(sys.argv[1]).write_text('async-ok', encoding='utf-8')\n"
            "\n"
            "asyncio.run(main())\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(TMP_ROOT / "script_runner" / "runner.py"),
                "--replay-mode",
                "none",
                "--target-root",
                str(project),
                "--entry",
                "script:main.py",
                "--framework",
                "none",
                "--",
                str(output),
            ],
            cwd=TMP_ROOT,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "async-ok")

    def test_generated_runner_loads_langgraph_env_before_importing_target(self) -> None:
        scaffold_integration(
            name="Env Runner",
            output_dir=TMP_ROOT,
            tool_style="none",
            framework="none",
        )
        project = TMP_ROOT / "env_project"
        package = project / "src" / "env_pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "graph.py").write_text(
            "import os\n"
            "\n"
            "class Graph:\n"
            "    def invoke(self, input, config=None):\n"
            "        return {'env': os.environ.get('REPLAY_TEST_ENV_VALUE')}\n"
            "\n"
            "graph = Graph()\n",
            encoding="utf-8",
        )
        (project / ".env").write_text("REPLAY_TEST_ENV_VALUE=loaded\n", encoding="utf-8")
        (project / "langgraph.json").write_text(
            json.dumps({"graphs": {"G": "./src/env_pkg/graph.py:graph"}, "env": "./.env"}),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(TMP_ROOT / "env_runner" / "runner.py"),
                "--replay-mode",
                "none",
                "--target-root",
                str(project),
                "--entry",
                "langgraph.json#G",
                "--input-json",
                "{}",
            ],
            cwd=TMP_ROOT,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"env": "loaded"})


if __name__ == "__main__":
    unittest.main()
