"""Tests for dynamic-Workflow run parsing (issue #174, PR1 — parse only).

Exercises ``claude_code_log.workflow`` against the synthesized
``workflow_basic`` fixture (see ``scripts/gen_workflow_fixture.py``):
a 2-phase (Map → Synthesize) run of 3 agents — two Map readers returning
StructuredOutput dicts and one Synthesize agent returning a string.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_log.workflow import (
    WorkflowRun,
    discover_workflow_runs,
    load_workflow_runs,
    parse_workflow_run,
)

FIXTURE = Path(__file__).parent / "test_data" / "workflow_basic"
TRUNK_SID = "11110000-0000-4000-8000-000000000001"
RUN_ID = "wf_demo01"
SESSION_DIR = FIXTURE / TRUNK_SID
RUN_DIR = SESSION_DIR / "subagents" / "workflows" / RUN_ID
SNAPSHOT = SESSION_DIR / "workflows" / f"{RUN_ID}.json"


def _parsed() -> WorkflowRun:
    run = parse_workflow_run(RUN_DIR, SNAPSHOT)
    assert run is not None
    return run


class TestDiscovery:
    def test_discover_finds_the_run_and_snapshot(self) -> None:
        runs = discover_workflow_runs(SESSION_DIR)
        assert len(runs) == 1
        run_dir, snapshot = runs[0]
        assert run_dir == RUN_DIR
        assert snapshot == SNAPSHOT

    def test_discover_returns_empty_for_non_workflow_dir(self, tmp_path: Path) -> None:
        assert discover_workflow_runs(tmp_path) == []

    def test_load_workflow_runs_scans_the_project_dir(self) -> None:
        runs = load_workflow_runs(FIXTURE)
        assert [r.run_id for r in runs] == [RUN_ID]


class TestRunMetadata:
    def test_run_identity_from_snapshot(self) -> None:
        run = _parsed()
        assert run.run_id == RUN_ID
        assert run.task_id == "task_demo01"
        assert run.workflow_name == "demo-review"
        assert run.status == "completed"
        assert run.has_snapshot is True
        assert run.agent_count == 3
        assert run.total_tokens == 303
        assert run.result == {"plan": "Land parsing first.", "areaCount": 2}


class TestAgents:
    def test_three_agents_in_journal_order(self) -> None:
        run = _parsed()
        assert [a.agent_id for a in run.agents] == ["ag000001", "ag000002", "ag000003"]

    def test_structured_and_string_results_from_journal(self) -> None:
        run = _parsed()
        by_id = {a.agent_id: a for a in run.agents}
        # Two Map readers return StructuredOutput dicts ...
        assert isinstance(by_id["ag000001"].result, dict)
        assert by_id["ag000001"].result["area"] == "loader"
        assert isinstance(by_id["ag000002"].result, dict)
        # ... the Synthesize agent returns a plain string.
        assert isinstance(by_id["ag000003"].result, str)
        assert by_id["ag000003"].result.startswith("## Plan")

    def test_agent_metadata_enriched_from_snapshot(self) -> None:
        run = _parsed()
        a1 = run.agents[0]
        assert a1.label == "review:loader"
        # Real runs carry a 1-BASED phaseIndex on agents (the fixture mirrors
        # this); phase grouping must therefore key off phase_title, not index.
        assert a1.phase_index == 1
        assert a1.phase_title == "Map"
        assert a1.model == "claude-sonnet-4-6"
        assert a1.state == "done"
        assert a1.tokens == 100
        assert a1.tool_calls == 2

    def test_side_channel_transcripts_loaded(self) -> None:
        run = _parsed()
        # Each agent's agent-<id>.jsonl (3 entries) is recursively loaded.
        for agent in run.agents:
            assert len(agent.entries) == 3


class TestPhases:
    def test_two_phases_with_correct_membership(self) -> None:
        run = _parsed()
        assert [p.title for p in run.phases] == ["Map", "Synthesize"]
        assert [a.agent_id for a in run.phases[0].agents] == ["ag000001", "ag000002"]
        assert [a.agent_id for a in run.phases[1].agents] == ["ag000003"]

    def test_one_based_phase_index_does_not_shift_membership(self) -> None:
        """Regression guard (found against a real 42-agent run): agents carry a
        1-based phaseIndex while phases[] is 0-based. Indexing phases[] by the
        raw phaseIndex would shift every agent one phase over (Map→Synthesize
        here). Membership must be resolved by title, so the Map-titled agents
        land in phase 0 even though their phase_index is 1."""
        run = _parsed()
        map_phase = run.phases[0]
        assert map_phase.title == "Map"
        # Their raw index (1) does NOT equal their phase's array index (0) ...
        assert all(a.phase_index == 1 for a in map_phase.agents)
        # ... yet they are correctly grouped under Map by title.
        assert {a.agent_id for a in map_phase.agents} == {"ag000001", "ag000002"}

    def test_phase_agents_are_the_same_objects_as_flat_list(self) -> None:
        run = _parsed()
        flat = {id(a) for a in run.agents}
        for phase in run.phases:
            for agent in phase.agents:
                assert id(agent) in flat


class TestWipWithoutSnapshot:
    """A *running* workflow has journal.jsonl but no <runId>.json yet —
    it must still parse: flat agents, no phase grouping (D1)."""

    def test_journal_only_parse(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "subagents" / "workflows" / "wf_live"
        run_dir.mkdir(parents=True)
        rows = [
            {"type": "started", "key": "k0", "agentId": "live1"},
            {"type": "started", "key": "k1", "agentId": "live2"},
            {"type": "result", "key": "k0", "agentId": "live1", "result": "partial"},
        ]
        (run_dir / "journal.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

        run = parse_workflow_run(run_dir, None)
        assert run is not None
        assert run.has_snapshot is False
        assert run.phases == []
        assert [a.agent_id for a in run.agents] == ["live1", "live2"]
        by_id = {a.agent_id: a for a in run.agents}
        assert by_id["live1"].result == "partial"
        assert by_id["live2"].result is None  # still in flight

    def test_no_journal_returns_none(self, tmp_path: Path) -> None:
        empty = tmp_path / "subagents" / "workflows" / "wf_empty"
        empty.mkdir(parents=True)
        assert parse_workflow_run(empty, None) is None


class TestLoaderOrphanSuppression:
    """The directory loader must scan workflow side-channel UUIDs so the
    fixture loads without spurious orphan warnings (CR/dev-docs invariant)."""

    def test_scan_sidechain_uuids_includes_workflow_agents(self) -> None:
        from claude_code_log.converter import _scan_sidechain_uuids

        uuids = _scan_sidechain_uuids(FIXTURE)
        # agent-ag000001.jsonl's first entry uuid is "ag000001_u1".
        assert "ag000001_u1" in uuids
        assert "ag000003_a2" in uuids
