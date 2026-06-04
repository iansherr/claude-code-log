"""Parse Claude Code *dynamic Workflow* runs (issue #174, PR1 — parse only).

A Workflow tool_use launches an orchestrator that fans out into many
side-channel sub-agents. On disk (see ``work/dynamic-workflow-support.md``
§1) a run under a trunk session ``<sid>/`` leaves:

    <sid>/subagents/workflows/<runId>/
        journal.jsonl                 live spine: started/result events, keyed by agentId
        agent-<agentId>.jsonl         per-agent side-channel transcript
        agent-<agentId>.meta.json     {"agentType": "workflow-subagent"}
    <sid>/workflows/<runId>.json      terminal snapshot: phases + per-agent metadata

This module turns that into a :class:`WorkflowRun`. Strategy (D1):
journal-led, ``<runId>.json``-enriched — ``journal.jsonl`` is the
authoritative live spine (present from the start, carries full results,
keyed by ``agentId``); ``<runId>.json`` is *optional* enrichment present
only after completion (phases + tokens/state/model per agent). A running
workflow with no snapshot still parses: agents in journal order, no phase
grouping.

This module does **no rendering** — wiring runs into the message tree is
a later phase. ``load_transcript`` is imported lazily to avoid a circular
import with ``converter``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

if TYPE_CHECKING:
    from claude_code_log.models import TranscriptEntry


@dataclass
class WorkflowAgent:
    """One sub-agent of a workflow run.

    ``result`` is the agent's full output from the journal (a dict for
    ``StructuredOutput`` agents, a string for plain-text agents, or
    ``None`` if the run is still in flight). Phase/metadata fields are
    populated only when the ``<runId>.json`` snapshot is present.
    """

    agent_id: str
    label: str = ""
    phase_index: Optional[int] = None
    phase_title: str = ""
    model: str = ""
    state: str = ""
    tokens: Optional[int] = None
    tool_calls: Optional[int] = None
    duration_ms: Optional[int] = None
    attempt: Optional[int] = None
    result: Any = None
    result_preview: str = ""
    entries: list["TranscriptEntry"] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


@dataclass
class WorkflowPhase:
    """A phase grouping of agents (only built when the snapshot is present)."""

    index: int
    title: str
    detail: str = ""
    agents: list[WorkflowAgent] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


@dataclass
class WorkflowRun:
    """A parsed dynamic-workflow run.

    ``agents`` is the flat list in journal (launch) order — always present.
    ``phases`` is populated only when ``<runId>.json`` was found
    (``has_snapshot``); each phase references the same WorkflowAgent objects
    as ``agents``. ``result`` is the run's final answer (snapshot ``result``).
    """

    run_id: str
    task_id: str = ""
    workflow_name: str = ""
    status: str = ""
    phases: list[WorkflowPhase] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    agents: list[WorkflowAgent] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    result: Any = None
    total_tokens: Optional[int] = None
    agent_count: Optional[int] = None
    has_snapshot: bool = False


def _read_jsonl(path: Path) -> list[Any]:
    """Read a JSONL file into a list of parsed values (skip blank/bad lines)."""
    rows: list[Any] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _parse_journal(path: Path) -> tuple[list[str], dict[str, Any]]:
    """Parse ``journal.jsonl`` into (agent order, {agentId: result}).

    Order is by first appearance (``started`` preferred, else ``result``).
    The last ``result`` for an agent wins (covers retries/attempts).
    """
    order: list[str] = []
    seen: set[str] = set()
    results: dict[str, Any] = {}
    for raw_row in _read_jsonl(path):
        if not isinstance(raw_row, dict):
            continue
        row = cast("dict[str, Any]", raw_row)
        agent_id = row.get("agentId")
        if not isinstance(agent_id, str):
            continue
        if agent_id not in seen:
            seen.add(agent_id)
            order.append(agent_id)
        if row.get("type") == "result":
            results[agent_id] = row.get("result")
    return order, results


def _load_snapshot(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load ``<runId>.json`` → (raw, phases[], {agentId: agent-progress-node}).

    Returns empty structures when the file is missing or unparseable, so
    callers can treat the snapshot as purely optional enrichment.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}, [], {}
    if not isinstance(loaded, dict):
        return {}, [], {}
    raw = cast("dict[str, Any]", loaded)

    raw_phases = raw.get("phases")
    phases: list[dict[str, Any]] = []
    if isinstance(raw_phases, list):
        phases = [
            cast("dict[str, Any]", p)
            for p in cast("list[Any]", raw_phases)
            if isinstance(p, dict)
        ]

    agent_meta: dict[str, dict[str, Any]] = {}
    raw_progress = raw.get("workflowProgress")
    if isinstance(raw_progress, list):
        for raw_node in cast("list[Any]", raw_progress):
            if not isinstance(raw_node, dict):
                continue
            node = cast("dict[str, Any]", raw_node)
            if node.get("type") != "workflow_agent":
                continue
            aid = node.get("agentId")
            if isinstance(aid, str):
                agent_meta[aid] = node
    return raw, phases, agent_meta


def parse_workflow_run(
    run_dir: Path,
    snapshot_path: Optional[Path] = None,
    *,
    silent: bool = True,
) -> Optional[WorkflowRun]:
    """Parse one workflow run from its ``subagents/workflows/<runId>/`` dir.

    ``snapshot_path`` is the optional ``<runId>.json`` terminal snapshot.
    Returns ``None`` if there is no ``journal.jsonl`` (not a workflow run).
    """
    journal = run_dir / "journal.jsonl"
    if not journal.is_file():
        return None

    order, results = _parse_journal(journal)

    run_id = run_dir.name
    task_id = workflow_name = status = ""
    total_tokens: Optional[int] = None
    agent_count: Optional[int] = None
    run_result: Any = None
    phases_meta: list[dict[str, Any]] = []
    agent_meta: dict[str, dict[str, Any]] = {}
    has_snapshot = False

    if snapshot_path is not None and snapshot_path.is_file():
        raw, phases_meta, agent_meta = _load_snapshot(snapshot_path)
        if raw:
            has_snapshot = True
            run_id = raw.get("runId") or run_id
            task_id = raw.get("taskId") or ""
            workflow_name = raw.get("workflowName") or ""
            status = raw.get("status") or ""
            total_tokens = raw.get("totalTokens")
            agent_count = raw.get("agentCount")
            run_result = raw.get("result")

    # Union of journal order with any snapshot-only agent ids (defensive).
    all_ids = list(order)
    for aid in agent_meta:
        if aid not in all_ids:
            all_ids.append(aid)

    # Lazy import avoids a circular dependency with converter.
    from claude_code_log.converter import load_transcript

    agents: list[WorkflowAgent] = []
    for aid in all_ids:
        meta = agent_meta.get(aid, {})
        agent_file = run_dir / f"agent-{aid}.jsonl"
        entries: list[Any] = []
        if agent_file.is_file():
            entries = load_transcript(agent_file, silent=silent)
        agents.append(
            WorkflowAgent(
                agent_id=aid,
                label=meta.get("label") or "",
                phase_index=meta.get("phaseIndex"),
                phase_title=meta.get("phaseTitle") or "",
                model=meta.get("model") or "",
                state=meta.get("state") or "",
                tokens=meta.get("tokens"),
                tool_calls=meta.get("toolCalls"),
                duration_ms=meta.get("durationMs"),
                attempt=meta.get("attempt"),
                result=results.get(aid),
                result_preview=meta.get("resultPreview") or "",
                entries=entries,
            )
        )

    phases = _group_into_phases(phases_meta, agents)

    return WorkflowRun(
        run_id=run_id,
        task_id=task_id,
        workflow_name=workflow_name,
        status=status,
        phases=phases,
        agents=agents,
        result=run_result,
        total_tokens=total_tokens,
        agent_count=agent_count,
        has_snapshot=has_snapshot,
    )


def _group_into_phases(
    phases_meta: list[dict[str, Any]], agents: list[WorkflowAgent]
) -> list[WorkflowPhase]:
    """Build phases from snapshot ``phases[]`` and assign agents to them.

    Agents map to a phase by ``phase_index`` (falling back to a title
    match). Returns ``[]`` when there is no snapshot — the WIP/journal-only
    view groups agents only as the flat ``agents`` list.
    """
    if not phases_meta:
        return []
    phases = [
        WorkflowPhase(
            index=idx, title=pm.get("title") or "", detail=pm.get("detail") or ""
        )
        for idx, pm in enumerate(phases_meta)
    ]
    title_to_idx = {p.title: p.index for p in phases if p.title}
    for agent in agents:
        idx = agent.phase_index
        if idx is None and agent.phase_title:
            idx = title_to_idx.get(agent.phase_title)
        if idx is not None and 0 <= idx < len(phases):
            phases[idx].agents.append(agent)
    return phases


def discover_workflow_runs(session_dir: Path) -> list[tuple[Path, Optional[Path]]]:
    """Find ``(run_dir, snapshot_path)`` pairs under one trunk session dir.

    ``run_dir`` is ``<session_dir>/subagents/workflows/<runId>/`` (must
    contain ``journal.jsonl``); ``snapshot_path`` is the matching
    ``<session_dir>/workflows/<runId>.json`` if present, else ``None``.
    """
    base = session_dir / "subagents" / "workflows"
    if not base.is_dir():
        return []
    runs: list[tuple[Path, Optional[Path]]] = []
    for run_dir in sorted(base.iterdir()):
        if not run_dir.is_dir() or not (run_dir / "journal.jsonl").is_file():
            continue
        snapshot = session_dir / "workflows" / f"{run_dir.name}.json"
        runs.append((run_dir, snapshot if snapshot.is_file() else None))
    return runs


def load_workflow_runs(
    directory_path: Path, *, silent: bool = True
) -> list[WorkflowRun]:
    """Discover and parse every workflow run under a project directory.

    Each trunk ``<session>.jsonl`` has a sibling ``<session>/`` dir whose
    ``subagents/workflows/<runId>/`` subtrees are the runs. Parse-only —
    the caller decides what to do with the returned runs.
    """
    runs: list[WorkflowRun] = []
    for session_dir in sorted(p for p in directory_path.iterdir() if p.is_dir()):
        for run_dir, snapshot in discover_workflow_runs(session_dir):
            parsed = parse_workflow_run(run_dir, snapshot, silent=silent)
            if parsed is not None:
                runs.append(parsed)
    return runs
