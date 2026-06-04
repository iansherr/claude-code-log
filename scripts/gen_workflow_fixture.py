#!/usr/bin/env python3
"""Generate the synthesized ``workflow_basic`` test fixture for issue #174.

Mirrors the on-disk layout a Claude Code *dynamic Workflow* run leaves
(see ``work/dynamic-workflow-support.md`` §1), fully sanitized — no real
paths, session ids, or agent ids:

    test/test_data/workflow_basic/
      <trunk>.jsonl                              trunk transcript (Workflow tool_use + async_launched result)
      <trunk>/
        subagents/workflows/<runId>/
          journal.jsonl                          live spine (started/result, keyed by agentId)
          agent-<agentId>.jsonl        (×3)      per-agent side-channel transcript
          agent-<agentId>.meta.json    (×3)      {"agentType": "workflow-subagent"}
        workflows/
          scripts/<workflowName>-<runId>.js      the JS orchestrator
          <runId>.json                           terminal snapshot (phases + per-agent metadata)

The run: workflow ``demo-review`` (runId ``wf_demo01``), 2 phases
(Map → Synthesize), 3 agents — two Map readers returning StructuredOutput
dicts, one Synthesize agent returning a plain-string markdown result.

Re-run to regenerate: ``python3 scripts/gen_workflow_fixture.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "test" / "test_data" / "workflow_basic"

TRUNK_SID = "11110000-0000-4000-8000-000000000001"
RUN_ID = "wf_demo01"
TASK_ID = "task_demo01"
WORKFLOW_NAME = "demo-review"
TS = "2026-06-04T10:00:00.000Z"

AGENTS = [
    {
        "agentId": "ag000001",
        "label": "review:loader",
        # Real runs use a 1-BASED phaseIndex on agents (and on workflow_phase
        # nodes), while the phases[] array is 0-based. Mirror that here so the
        # fixture exercises the offset: title-based assignment must win.
        "phaseIndex": 1,
        "phaseTitle": "Map",
        "model": "claude-sonnet-4-6",
        "result": {
            "area": "loader",
            "summary": "Discovery glob misses subagents/workflows.",
            "key_functions": ["_scan_sidechain_uuids", "load_directory_transcripts"],
            "opportunities": ["extend glob to subagents/workflows/<runId>"],
        },
    },
    {
        "agentId": "ag000002",
        "label": "review:hierarchy",
        "phaseIndex": 1,
        "phaseTitle": "Map",
        "model": "claude-sonnet-4-6",
        "result": {
            "area": "hierarchy",
            "summary": "Tree already nests; level table can retire.",
            "key_functions": ["_build_message_tree"],
            "opportunities": ["derive depth from tree position"],
        },
    },
    {
        "agentId": "ag000003",
        "label": "synthesize",
        "phaseIndex": 2,
        "phaseTitle": "Synthesize",
        "model": "claude-opus-4-8",
        "result": "## Plan\n\nLand parsing first, then render workflow runs on the nested DOM.",
    },
]


def _base(
    uuid: str, parent: str | None, sid: str, sidechain: bool, agent_id: str | None
) -> dict:
    e: dict = {
        "type": "",  # set by caller
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": sidechain,
        "userType": "external",
        "cwd": "/repo",
        "sessionId": sid,
        "version": "2.1.2",
        "timestamp": TS,
    }
    if agent_id is not None:
        e["agentId"] = agent_id
    return e


def _user(
    uuid, parent, sid, content, *, sidechain=False, agent_id=None, tool_use_result=None
) -> dict:
    e = _base(uuid, parent, sid, sidechain, agent_id)
    e["type"] = "user"
    e["message"] = {"role": "user", "content": content}
    if tool_use_result is not None:
        e["toolUseResult"] = tool_use_result
    return e


def _assistant(
    uuid, parent, sid, model, content, *, sidechain=False, agent_id=None
) -> dict:
    e = _base(uuid, parent, sid, sidechain, agent_id)
    e["type"] = "assistant"
    e["message"] = {
        "id": f"msg_{uuid}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "stop_reason": "end_turn",
        "content": content,
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    return e


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _trunk() -> list[dict]:
    script = (
        "export const meta = {\n"
        "  name: 'demo-review',\n"
        "  description: 'Review changed files across dimensions',\n"
        "  phases: [{ title: 'Map' }, { title: 'Synthesize' }],\n"
        "}\n"
        "phase('Map')\n"
        "const findings = await parallel(DIMS.map(d => () => agent(d.prompt)))\n"
        "phase('Synthesize')\n"
        "return await agent('Merge: ' + JSON.stringify(findings))\n"
    )
    return [
        _user(
            "u0000001",
            None,
            TRUNK_SID,
            [{"type": "text", "text": "Review the diff with a workflow."}],
        ),
        _assistant(
            "a0000001",
            "u0000001",
            TRUNK_SID,
            "claude-opus-4-8",
            [
                {"type": "text", "text": "Launching a review workflow."},
                {
                    "type": "tool_use",
                    "id": "toolu_wf01",
                    "name": "Workflow",
                    "input": {"script": script},
                },
            ],
        ),
        _user(
            "u0000002",
            "a0000001",
            TRUNK_SID,
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_wf01",
                    "content": '{"status":"async_launched","runId":"wf_demo01"}',
                }
            ],
            tool_use_result={
                "isAsync": True,
                "status": "async_launched",
                "runId": RUN_ID,
                "taskId": TASK_ID,
                "transcriptDir": f"{TRUNK_SID}/subagents/workflows/{RUN_ID}",
                "scriptPath": f"{TRUNK_SID}/workflows/scripts/{WORKFLOW_NAME}-{RUN_ID}.js",
            },
        ),
    ]


def _journal() -> list[dict]:
    rows: list[dict] = []
    for i, a in enumerate(AGENTS):
        rows.append({"type": "started", "key": f"v2:hash{i}", "agentId": a["agentId"]})
    for i, a in enumerate(AGENTS):
        rows.append(
            {
                "type": "result",
                "key": f"v2:hash{i}",
                "agentId": a["agentId"],
                "result": a["result"],
            }
        )
    return rows


def _run_snapshot() -> dict:
    progress: list[dict] = []
    for p_idx, title in enumerate(["Map", "Synthesize"]):
        progress.append({"type": "workflow_phase", "index": p_idx + 1, "title": title})
    for idx, a in enumerate(AGENTS):
        result = a["result"]
        preview = (
            json.dumps(result)[:60] if isinstance(result, dict) else str(result)[:60]
        )
        progress.append(
            {
                "type": "workflow_agent",
                "index": idx,
                "label": a["label"],
                "phaseIndex": a["phaseIndex"],
                "phaseTitle": a["phaseTitle"],
                "agentId": a["agentId"],
                "model": a["model"],
                "state": "done",
                "attempt": 1,
                "tokens": 100 + idx,
                "toolCalls": 2 + idx,
                "durationMs": 1000 + idx,
                "resultPreview": preview,
            }
        )
    return {
        "runId": RUN_ID,
        "taskId": TASK_ID,
        "status": "completed",
        "workflowName": WORKFLOW_NAME,
        "timestamp": TS,
        "durationMs": 5000,
        "agentCount": len(AGENTS),
        "totalTokens": 303,
        "totalToolCalls": 9,
        "defaultModel": "claude-sonnet-4-6",
        "scriptPath": f"{TRUNK_SID}/workflows/scripts/{WORKFLOW_NAME}-{RUN_ID}.js",
        "phases": [
            {"title": "Map", "detail": "scan dimensions"},
            {"title": "Synthesize", "detail": "merge findings"},
        ],
        "workflowProgress": progress,
        "result": {"plan": "Land parsing first.", "areaCount": 2},
    }


def _agent_transcript(agent: dict) -> list[dict]:
    aid = agent["agentId"]
    sid = f"{TRUNK_SID}#agent-{aid}"
    result = agent["result"]
    content = [{"type": "text", "text": f"Working on {agent['label']}."}]
    if isinstance(result, dict):
        content.append(
            {
                "type": "tool_use",
                "id": f"toolu_{aid}_so",
                "name": "StructuredOutput",
                "input": result,
            }
        )
        last_text = "Returning structured output."
    else:
        last_text = str(result)
    return [
        _user(
            f"{aid}_u1",
            None,
            sid,
            [{"type": "text", "text": agent["label"]}],
            sidechain=True,
            agent_id=aid,
        ),
        _assistant(
            f"{aid}_a1",
            f"{aid}_u1",
            sid,
            agent["model"],
            content,
            sidechain=True,
            agent_id=aid,
        ),
        _assistant(
            f"{aid}_a2",
            f"{aid}_a1",
            sid,
            agent["model"],
            [{"type": "text", "text": last_text}],
            sidechain=True,
            agent_id=aid,
        ),
    ]


def main() -> None:
    trunk_dir = FIXTURE / TRUNK_SID
    run_dir = trunk_dir / "subagents" / "workflows" / RUN_ID

    _write_jsonl(FIXTURE / f"{TRUNK_SID}.jsonl", _trunk())
    _write_jsonl(run_dir / "journal.jsonl", _journal())
    for a in AGENTS:
        _write_jsonl(run_dir / f"agent-{a['agentId']}.jsonl", _agent_transcript(a))
        _write_json(
            run_dir / f"agent-{a['agentId']}.meta.json",
            {"agentType": "workflow-subagent"},
        )
    _write_json(trunk_dir / "workflows" / f"{RUN_ID}.json", _run_snapshot())
    script_path = trunk_dir / "workflows" / "scripts" / f"{WORKFLOW_NAME}-{RUN_ID}.js"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "// demo-review orchestrator (synthesized fixture)\n"
        "export const meta = { name: 'demo-review', phases: [{title:'Map'},{title:'Synthesize'}] }\n",
        encoding="utf-8",
    )
    print(f"Wrote workflow_basic fixture under {FIXTURE}")


if __name__ == "__main__":
    main()
