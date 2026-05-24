from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import replay

from .llm_client import ROOT_DIR, create_llm_client, load_llm_config
from .tools import MAPPING_TOOLS, WorkspaceToolClient, async_digest, score_payload


AGENT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = AGENT_DIR / "outputs"
SANDBOX_BASE_DIR = AGENT_DIR / "sandbox_base"
DEFAULT_SANDBOX_DIR = AGENT_DIR / "sandbox"
DEFAULT_COMMAND = "exercise replay with concurrent LLM branches, local tools, file effects, and expected failures"


async def chat_text(
    client: Any,
    model_name: str,
    *,
    phase: str,
    payload: dict[str, Any],
    temperature: float = 0.2,
) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You are Agent4, a deterministic replay test worker. "
                "Return one concise plain-text observation for the requested phase."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"phase": phase, **payload}, ensure_ascii=False, sort_keys=True),
        },
    ]
    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
        top_p=0.9,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM returned an empty response.")
    return content.strip()


async def branch_analysis(client: Any, model_name: str, command: str, seed: str, branch: str) -> dict[str, str]:
    observation = await chat_text(
        client,
        model_name,
        phase="fanout",
        payload={"command": command, "seed": seed, "branch": branch},
        temperature=0.35,
    )
    return {"branch": branch, "observation": observation}


async def duplicate_probe(client: Any, model_name: str, command: str) -> str:
    return await chat_text(
        client,
        model_name,
        phase="duplicate-probe",
        payload={"command": command, "probe": "same-input"},
        temperature=0.1,
    )


def direct_score_tool(texts: list[str]) -> dict[str, Any]:
    return replay.invoke_tool_sync(
        "score_payload",
        {"texts": texts},
        lambda: score_payload({"texts": texts}),
        namespace="agent4-direct",
        version="v1",
    )


async def direct_digest_tool(text: str) -> dict[str, Any]:
    return await replay.invoke_tool(
        "async_digest",
        {"text": text},
        lambda: async_digest({"text": text}),
        namespace="agent4-direct",
        version="v1",
    )


async def run_taskgroup_tools(command: str, seed: str) -> dict[str, Any]:
    async with asyncio.TaskGroup() as task_group:
        digest_task = task_group.create_task(direct_digest_tool(f"{command}\n{seed}"))
        tags_task = task_group.create_task(MAPPING_TOOLS["topic_tags"]({"text": f"{command}\n{seed}"}))
    return {"digest": digest_task.result(), "topic_tags": tags_task.result()}


async def run_expected_failure() -> dict[str, Any]:
    try:
        MAPPING_TOOLS["unstable_gate"]({"label": "agent4-expected", "should_fail": True})
    except Exception as exc:
        original_type = getattr(exc, "original_type", None)
        message = getattr(exc, "message", None)
        return {
            "ok": False,
            "type": str(original_type or exc.__class__.__name__),
            "message": str(message or exc),
        }
    return {"ok": True, "message": "unexpected success"}


async def run_workspace_tools(
    tool_client: WorkspaceToolClient,
    *,
    command: str,
    seed: str,
    fanout: list[dict[str, str]],
    tool_summary: dict[str, Any],
) -> dict[str, Any]:
    inventory = await tool_client.call_tool("inventory", {})
    report_text = render_workspace_report(
        command=command,
        seed=seed,
        fanout=fanout,
        tool_summary=tool_summary,
    )
    status = await tool_client.call_tool("write_text", {"path": "status.md", "text": report_text})
    created = await tool_client.call_tool(
        "write_text",
        {
            "path": "generated/branch_digest.md",
            "text": "\n".join(item["observation"] for item in fanout) + "\n",
        },
    )
    appended = await tool_client.call_tool(
        "append_text",
        {"path": "status.md", "text": f"\n\nWorkspace tools completed for seed: {seed}\n"},
    )
    deleted = await tool_client.call_tool("delete_file", {"path": "stale.txt"})
    return {
        "inventory": inventory,
        "status": status,
        "created": created,
        "appended": appended,
        "deleted": deleted,
    }


def render_workspace_report(
    *,
    command: str,
    seed: str,
    fanout: list[dict[str, str]],
    tool_summary: dict[str, Any],
) -> str:
    lines = [
        "# Agent4 Sandbox Status",
        "",
        f"Command: {command}",
        f"Seed: {seed}",
        "",
        "Fanout:",
    ]
    lines.extend(f"- {item['branch']}: {item['observation']}" for item in fanout)
    lines.extend(
        [
            "",
            "Tool summary:",
            json.dumps(tool_summary, ensure_ascii=False, sort_keys=True),
            "",
        ]
    )
    return "\n".join(lines)


async def run(
    command: str = DEFAULT_COMMAND,
    *,
    output_path: Path | None = None,
    tool_client: WorkspaceToolClient | None = None,
    allow_fake_llm: bool = False,
) -> dict[str, Any]:
    config = load_llm_config(allow_fake=allow_fake_llm)
    client = create_llm_client(config)
    workspace_client = tool_client or WorkspaceToolClient(DEFAULT_SANDBOX_DIR)

    seed = await chat_text(
        client,
        config.model_name,
        phase="seed",
        payload={"command": command, "label": "seed"},
        temperature=0.2,
    )

    created_task = asyncio.create_task(
        chat_text(
            client,
            config.model_name,
            phase="create-task",
            payload={"command": command, "seed": seed, "label": "created-task"},
            temperature=0.25,
        )
    )
    duplicate_results = await asyncio.gather(
        duplicate_probe(client, config.model_name, command),
        duplicate_probe(client, config.model_name, command),
    )
    fanout = await asyncio.gather(
        *(branch_analysis(client, config.model_name, command, seed, branch) for branch in ("alpha", "beta", "gamma"))
    )
    created_task_result = await created_task

    normalized = MAPPING_TOOLS["normalize_text"]({"text": command})
    score = direct_score_tool([command, seed, created_task_result, *duplicate_results])
    taskgroup_tools = await run_taskgroup_tools(command, seed)
    expected_failure = await run_expected_failure()
    tool_summary = {
        "normalized": normalized,
        "score": score,
        "taskgroup": taskgroup_tools,
        "expected_failure": expected_failure,
    }

    workspace = await run_workspace_tools(
        workspace_client,
        command=command,
        seed=seed,
        fanout=fanout,
        tool_summary=tool_summary,
    )

    if "agent4" in seed or "risk" in command.lower():
        control_note = await chat_text(
            client,
            config.model_name,
            phase="control-dependent",
            payload={"command": command, "seed": seed, "workspace": workspace, "label": "control"},
            temperature=0.2,
        )
    else:
        control_note = "control-dependent call skipped"

    synthesis = await chat_text(
        client,
        config.model_name,
        phase="synthesis",
        payload={
            "command": command,
            "seed": seed,
            "duplicate_results": duplicate_results,
            "created_task_result": created_task_result,
            "fanout": fanout,
            "tool_summary": tool_summary,
            "workspace": workspace,
            "control_note": control_note,
            "label": "final",
        },
        temperature=0.2,
    )

    result = {
        "ok": True,
        "command": command,
        "coverage": coverage_matrix(),
        "llm": {
            "seed": seed,
            "duplicate_results": duplicate_results,
            "created_task_result": created_task_result,
            "fanout": fanout,
            "control_note": control_note,
            "synthesis": synthesis,
        },
        "tools": tool_summary,
        "workspace": workspace,
    }
    saved_path = save_report(result, output_path)
    result["output_path"] = str(saved_path)
    return result


def coverage_matrix() -> list[dict[str, str]]:
    return [
        {"area": "llm", "scenario": "root call, dependent call, and duplicate concurrent inputs"},
        {"area": "async", "scenario": "asyncio.gather, asyncio.create_task, and asyncio.TaskGroup"},
        {"area": "tools", "scenario": "direct sync/async invoke_tool plus MappingToolAdapter"},
        {"area": "method_adapter", "scenario": "WorkspaceToolClient.call_tool wrapped by MethodToolAdapter"},
        {"area": "filesystem", "scenario": "sandbox inventory, modify, create, append, and delete effects"},
        {"area": "errors", "scenario": "expected tool exception recorded and replayed"},
        {"area": "forks", "scenario": "LLM breakpoint overrides make downstream calls run live in a fork"},
    ]


def save_report(result: dict[str, Any], output_path: Path | None = None) -> Path:
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"agent4_report_{timestamp}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(result), encoding="utf-8")
    return output_path


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Agent4 Replay Coverage Report",
        "",
        f"Command: {result['command']}",
        "",
        "## Coverage",
    ]
    lines.extend(f"- {item['area']}: {item['scenario']}" for item in result["coverage"])
    lines.extend(
        [
            "",
            "## LLM",
            f"- seed: {result['llm']['seed']}",
            f"- duplicate_results: {json.dumps(result['llm']['duplicate_results'], ensure_ascii=False)}",
            f"- created_task_result: {result['llm']['created_task_result']}",
            f"- control_note: {result['llm']['control_note']}",
            f"- synthesis: {result['llm']['synthesis']}",
            "",
            "## Tools",
            "```json",
            json.dumps(result["tools"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Workspace",
            "```json",
            json.dumps(result["workspace"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Agent4 comprehensive replay test workflow.")
    parser.add_argument("command", nargs="?", default=DEFAULT_COMMAND)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--fake-llm", action="store_true", help="Use deterministic local fake LLM responses.")
    parser.add_argument("--sandbox-root", type=Path, default=DEFAULT_SANDBOX_DIR)
    return parser


async def async_main() -> None:
    args = build_parser().parse_args()
    if args.fake_llm:
        from .fake_llm import install_fake_llm

        install_fake_llm()

    output_path = args.output
    if output_path and not output_path.is_absolute():
        output_path = ROOT_DIR / output_path
    sandbox_root = args.sandbox_root
    if not sandbox_root.is_absolute():
        sandbox_root = ROOT_DIR / sandbox_root

    result = await run(
        args.command,
        output_path=output_path,
        tool_client=WorkspaceToolClient(sandbox_root),
        allow_fake_llm=args.fake_llm,
    )
    print(f"agent4 report saved to: {result['output_path']}")
    print(result["llm"]["synthesis"])


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
