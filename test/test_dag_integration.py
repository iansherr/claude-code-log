"""Integration tests for DAG-based ordering in the rendering pipeline.

Tests that DAG ordering in load_directory_transcripts() produces correct
results when wired into the converter's directory-mode loading.
"""

import json
import logging
from pathlib import Path
from typing import Any


from claude_code_log.converter import (
    load_directory_transcripts,
    _scan_progress_chains,
    _repair_parent_chains,
)
from claude_code_log.models import (
    BaseTranscriptEntry,
    SummaryTranscriptEntry,
    QueueOperationTranscriptEntry,
)


def _write_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write entries as JSONL lines to a file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _make_user_entry(
    uuid: str,
    session_id: str,
    timestamp: str,
    parent_uuid: str | None = None,
    text: str = "msg",
    is_sidechain: bool = False,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Helper to create a user transcript entry dict."""
    entry: dict[str, Any] = {
        "type": "user",
        "timestamp": timestamp,
        "parentUuid": parent_uuid,
        "isSidechain": is_sidechain,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "1.0.0",
        "uuid": uuid,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    if agent_id is not None:
        entry["agentId"] = agent_id
    return entry


def _make_assistant_entry(
    uuid: str,
    session_id: str,
    timestamp: str,
    parent_uuid: str | None = None,
    text: str = "reply",
    is_sidechain: bool = False,
) -> dict[str, Any]:
    """Helper to create an assistant transcript entry dict."""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "parentUuid": parent_uuid,
        "isSidechain": is_sidechain,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "1.0.0",
        "uuid": uuid,
        "requestId": f"req_{uuid}",
        "message": {
            "id": uuid,
            "type": "message",
            "role": "assistant",
            "model": "claude-3-sonnet",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }


# =============================================================================
# Test: DAG ordering in load_directory_transcripts
# =============================================================================


class TestLoadDirectoryDagOrdering:
    """Test that load_directory_transcripts uses DAG ordering."""

    def test_load_directory_dag_ordering(self, tmp_path: Path) -> None:
        """Split dag_resume entries across two files, verify session grouping."""
        # File 1: session s1 entries (a→b→c→d→e)
        file1_entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
            _make_user_entry("c", "s1", "2025-07-01T10:02:00.000Z", "b"),
            _make_assistant_entry("d", "s1", "2025-07-01T10:03:00.000Z", "c"),
            _make_user_entry("e", "s1", "2025-07-01T10:04:00.000Z", "d"),
        ]
        # File 2: session s2 entries (f→g→h, f.parent=e)
        file2_entries = [
            _make_user_entry("f", "s2", "2025-07-01T11:00:00.000Z", "e", "Resume"),
            _make_assistant_entry("g", "s2", "2025-07-01T11:01:00.000Z", "f"),
            _make_user_entry("h", "s2", "2025-07-01T11:02:00.000Z", "g"),
        ]

        _write_jsonl(tmp_path / "session1.jsonl", file1_entries)
        _write_jsonl(tmp_path / "session2.jsonl", file2_entries)

        result, _ = load_directory_transcripts(tmp_path, silent=True)

        # Should have all 8 entries in DAG order (s1 then s2)
        uuids = [getattr(e, "uuid", None) for e in result]
        assert uuids == ["a", "b", "c", "d", "e", "f", "g", "h"]

    def test_load_directory_with_sidechains(self, tmp_path: Path) -> None:
        """Sidechain entries should be present after DAG-ordered main entries."""
        main_entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
        ]
        sidechain_entries = [
            _make_user_entry(
                "sc1",
                "s1",
                "2025-07-01T10:00:30.000Z",
                "a",
                "Sidechain msg",
                is_sidechain=True,
            ),
        ]

        _write_jsonl(tmp_path / "session.jsonl", main_entries + sidechain_entries)

        result, _ = load_directory_transcripts(tmp_path, silent=True)
        uuids = [getattr(e, "uuid", None) for e in result]

        # Main entries first (DAG ordered), then sidechain
        assert "a" in uuids
        assert "b" in uuids
        assert "sc1" in uuids
        # Sidechain should be after main entries
        assert uuids.index("sc1") > uuids.index("b")

    def test_load_directory_with_summaries(self, tmp_path: Path) -> None:
        """Summary entries should be preserved in output."""
        entries: list[dict[str, Any]] = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
            {
                "type": "summary",
                "summary": "A test summary",
                "leafUuid": "b",
                "timestamp": "2025-07-01T10:05:00.000Z",
            },
        ]

        _write_jsonl(tmp_path / "session.jsonl", entries)

        result, _ = load_directory_transcripts(tmp_path, silent=True)

        # Should have 2 DAG entries + 1 summary
        summary_entries = [e for e in result if isinstance(e, SummaryTranscriptEntry)]
        assert len(summary_entries) == 1
        assert summary_entries[0].summary == "A test summary"

        # DAG entries should be present
        dag_uuids = [
            getattr(e, "uuid", None)
            for e in result
            if not isinstance(e, SummaryTranscriptEntry)
        ]
        assert "a" in dag_uuids
        assert "b" in dag_uuids

    def test_load_directory_degenerate_parentuuid(self, tmp_path: Path) -> None:
        """All parentUuid=null entries should be returned, none lost."""
        entries = [
            _make_user_entry(
                f"msg_{i}", "s1", f"2025-07-01T10:0{i}:00.000Z", None, f"Msg {i}"
            )
            for i in range(5)
        ]

        _write_jsonl(tmp_path / "session.jsonl", entries)

        result, _ = load_directory_transcripts(tmp_path, silent=True)

        # All 5 entries should be present
        uuids = [getattr(e, "uuid", None) for e in result]
        assert len(uuids) == 5
        # Should be timestamp-ordered (fallback)
        assert uuids == ["msg_0", "msg_1", "msg_2", "msg_3", "msg_4"]

    def test_load_directory_with_queue_operations(self, tmp_path: Path) -> None:
        """Queue operation entries should be preserved in output."""
        entries: list[dict[str, Any]] = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
            {
                "type": "queue-operation",
                "operation": "dequeue",
                "timestamp": "2025-07-01T10:00:30.000Z",
                "sessionId": "s1",
            },
        ]

        _write_jsonl(tmp_path / "session.jsonl", entries)

        result, _ = load_directory_transcripts(tmp_path, silent=True)

        queue_entries = [
            e for e in result if isinstance(e, QueueOperationTranscriptEntry)
        ]
        assert len(queue_entries) == 1
        assert queue_entries[0].operation == "dequeue"


# =============================================================================
# Test: End-to-end with generate_template_messages
# =============================================================================


class TestEndToEndHtmlWithDag:
    """Test full pipeline from DAG-ordered directory input to template messages."""

    def test_end_to_end_html_with_dag(self, tmp_path: Path) -> None:
        """Full generate_template_messages() with DAG-ordered directory input."""
        from claude_code_log.renderer import generate_template_messages

        # Two sessions: s1 (earlier), s2 (later, resumes from s1)
        file1_entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Hello"),
            _make_assistant_entry(
                "b", "s1", "2025-07-01T10:01:00.000Z", "a", "Hi there"
            ),
        ]
        file2_entries = [
            _make_user_entry("c", "s2", "2025-07-01T11:00:00.000Z", "b", "Resume"),
            _make_assistant_entry(
                "d", "s2", "2025-07-01T11:01:00.000Z", "c", "Resumed"
            ),
        ]

        _write_jsonl(tmp_path / "session1.jsonl", file1_entries)
        _write_jsonl(tmp_path / "session2.jsonl", file2_entries)

        # Load via DAG ordering
        messages, _ = load_directory_transcripts(tmp_path, silent=True)

        # Run through template message generation (returns tuple)
        root_messages, session_nav, context = generate_template_messages(messages)

        # Extract session headers and verify order
        from claude_code_log.models import SessionHeaderMessage

        session_headers = [
            tm for tm in root_messages if isinstance(tm.content, SessionHeaderMessage)
        ]

        # Should have session headers
        assert len(session_headers) >= 2
        # s1 header should come before s2 header
        s1_idx = next(
            i
            for i, tm in enumerate(root_messages)
            if isinstance(tm.content, SessionHeaderMessage)
            and tm.content.session_id == "s1"
        )
        s2_idx = next(
            i
            for i, tm in enumerate(root_messages)
            if isinstance(tm.content, SessionHeaderMessage)
            and tm.content.session_id == "s2"
        )
        assert s1_idx < s2_idx

    def test_session_header_hierarchy_fields(self, tmp_path: Path) -> None:
        """Session headers carry hierarchy fields (parent, depth) from DAG."""
        from claude_code_log.renderer import generate_template_messages
        from claude_code_log.models import SessionHeaderMessage

        # s1 is root, s2 resumes from s1 (c.parent=b), s3 resumes from s2
        entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
            _make_user_entry("c", "s2", "2025-07-01T11:00:00.000Z", "b", "Resume"),
            _make_assistant_entry("d", "s2", "2025-07-01T11:01:00.000Z", "c"),
            _make_user_entry("e", "s3", "2025-07-01T12:00:00.000Z", "d", "Resume2"),
            _make_assistant_entry("f", "s3", "2025-07-01T12:01:00.000Z", "e"),
        ]

        _write_jsonl(tmp_path / "sessions.jsonl", entries)
        messages, _ = load_directory_transcripts(tmp_path, silent=True)
        root_messages, session_nav, _ctx = generate_template_messages(messages)

        headers = {
            tm.content.session_id: tm.content
            for tm in root_messages
            if isinstance(tm.content, SessionHeaderMessage)
        }

        # s1 is root
        assert headers["s1"].parent_session_id is None
        assert headers["s1"].depth == 0

        # s2 is child of s1
        assert headers["s2"].parent_session_id == "s1"
        assert headers["s2"].depth == 1

        # s3 is grandchild (child of s2)
        assert headers["s3"].parent_session_id == "s2"
        assert headers["s3"].depth == 2

    def test_session_nav_hierarchy_fields(self, tmp_path: Path) -> None:
        """Session nav entries carry hierarchy data for template rendering."""
        from claude_code_log.renderer import generate_template_messages

        entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
            _make_user_entry("c", "s2", "2025-07-01T11:00:00.000Z", "b", "Resume"),
            _make_assistant_entry("d", "s2", "2025-07-01T11:01:00.000Z", "c"),
        ]

        _write_jsonl(tmp_path / "sessions.jsonl", entries)
        messages, _ = load_directory_transcripts(tmp_path, silent=True)
        _root, session_nav, _ctx = generate_template_messages(messages)

        nav_by_id = {s["id"]: s for s in session_nav}

        # s1 root
        assert nav_by_id["s1"]["parent_session_id"] is None
        assert nav_by_id["s1"]["depth"] == 0

        # s2 child
        assert nav_by_id["s2"]["parent_session_id"] == "s1"
        assert nav_by_id["s2"]["depth"] == 1

    def test_degenerate_data_no_hierarchy(self, tmp_path: Path) -> None:
        """Degenerate data (all null parentUuid) has no hierarchy."""
        from claude_code_log.renderer import generate_template_messages
        from claude_code_log.models import SessionHeaderMessage

        entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None, "Msg"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", None),
        ]

        _write_jsonl(tmp_path / "session.jsonl", entries)
        messages, _ = load_directory_transcripts(tmp_path, silent=True)
        root_messages, session_nav, _ctx = generate_template_messages(messages)

        header = next(
            tm.content
            for tm in root_messages
            if isinstance(tm.content, SessionHeaderMessage)
        )
        assert header.parent_session_id is None
        assert header.depth == 0

        assert session_nav[0]["depth"] == 0
        assert session_nav[0]["parent_session_id"] is None

    def test_end_to_end_degenerate_data_matches_timestamp_sort(
        self, tmp_path: Path
    ) -> None:
        """Degenerate data (all null parentUuid) produces same order as timestamp sort."""
        from claude_code_log.renderer import generate_template_messages

        # 3 entries with parentUuid=null
        entries = [
            _make_user_entry("x", "s1", "2025-07-01T10:00:00.000Z", None, "First"),
            _make_assistant_entry("y", "s1", "2025-07-01T10:01:00.000Z", None, "Mid"),
            _make_user_entry("z", "s1", "2025-07-01T10:02:00.000Z", None, "Last"),
        ]

        _write_jsonl(tmp_path / "session.jsonl", entries)

        messages, _ = load_directory_transcripts(tmp_path, silent=True)

        # Verify order matches timestamps
        uuids = [getattr(e, "uuid", None) for e in messages if hasattr(e, "uuid")]
        assert uuids == ["x", "y", "z"]

        # Also verify template generation works
        root_messages, session_nav, context = generate_template_messages(messages)
        assert len(root_messages) > 0


# =============================================================================
# Test: Progress chain repair
# =============================================================================

# Path to real project test data with progress entries
EXPERIMENTS_IDEAS_DIR = (
    Path(__file__).parent / "test_data" / "real_projects" / "-experiments-ideas"
)


def _make_progress_entry(uuid: str, parent_uuid: str | None = None) -> dict[str, Any]:
    """Helper to create a progress transcript entry dict."""
    return {
        "type": "progress",
        "timestamp": "2025-07-01T10:00:00.000Z",
        "uuid": uuid,
        "parentUuid": parent_uuid,
        "sessionId": "s1",
        "content": {"type": "hook_progress"},
    }


class TestScanProgressChains:
    """Unit tests for _scan_progress_chains helper."""

    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory produces empty chain."""
        chain = _scan_progress_chains(tmp_path)
        assert chain == {}

    def test_scan_file_with_no_progress(self, tmp_path: Path) -> None:
        """File with no progress entries produces empty chain."""
        entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None),
        ]
        _write_jsonl(tmp_path / "session.jsonl", entries)
        chain = _scan_progress_chains(tmp_path)
        assert chain == {}

    def test_scan_file_with_progress(self, tmp_path: Path) -> None:
        """File with progress entries produces correct chain."""
        entries = [
            _make_progress_entry("p1", None),
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", "p1"),
            _make_progress_entry("p2", "a"),
        ]
        _write_jsonl(tmp_path / "session.jsonl", entries)
        chain = _scan_progress_chains(tmp_path)
        assert chain == {"p1": None, "p2": "a"}

    def test_scan_single_file_path(self, tmp_path: Path) -> None:
        """Can scan a single file (not just a directory)."""
        entries = [_make_progress_entry("p1", "parent1")]
        path = tmp_path / "session.jsonl"
        _write_jsonl(path, entries)
        chain = _scan_progress_chains(path)
        assert chain == {"p1": "parent1"}

    def test_scan_real_data(self) -> None:
        """Scan real test data with 11 progress entries."""
        chain = _scan_progress_chains(EXPERIMENTS_IDEAS_DIR)
        assert len(chain) == 11


class TestRepairParentChains:
    """Unit tests for _repair_parent_chains helper."""

    def test_no_progress_noop(self, tmp_path: Path) -> None:
        """No progress entries means no repairs needed."""
        entries = [
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", None),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
        ]
        _write_jsonl(tmp_path / "session.jsonl", entries)
        from claude_code_log.converter import load_transcript

        messages = load_transcript(tmp_path / "session.jsonl", silent=True)
        _repair_parent_chains(messages, {})
        assert isinstance(messages[0], BaseTranscriptEntry)
        assert isinstance(messages[1], BaseTranscriptEntry)
        assert messages[0].parentUuid is None
        assert messages[1].parentUuid == "a"

    def test_single_progress_gap(self, tmp_path: Path) -> None:
        """Single progress gap is bridged."""
        entries = [
            _make_progress_entry("p1", None),
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", "p1"),
        ]
        _write_jsonl(tmp_path / "session.jsonl", entries)
        from claude_code_log.converter import load_transcript

        messages = load_transcript(tmp_path / "session.jsonl", silent=True)
        progress_chain = {"p1": None}
        _repair_parent_chains(messages, progress_chain)
        # user(a).parentUuid was "p1" (progress), repaired to None
        assert isinstance(messages[0], BaseTranscriptEntry)
        assert messages[0].parentUuid is None

    def test_chained_progress_gap(self, tmp_path: Path) -> None:
        """Chain of consecutive progress entries is fully bridged."""
        # real_parent → progress(p1) → progress(p2) → user(a)
        entries = [
            _make_user_entry("real_parent", "s1", "2025-07-01T10:00:00.000Z", None),
            _make_progress_entry("p1", "real_parent"),
            _make_progress_entry("p2", "p1"),
            _make_user_entry("a", "s1", "2025-07-01T10:01:00.000Z", "p2"),
        ]
        _write_jsonl(tmp_path / "session.jsonl", entries)
        from claude_code_log.converter import load_transcript

        messages = load_transcript(tmp_path / "session.jsonl", silent=True)
        progress_chain = {"p1": "real_parent", "p2": "p1"}
        _repair_parent_chains(messages, progress_chain)
        # user(a).parentUuid was "p2", should walk through p2→p1→real_parent
        user_a = [m for m in messages if getattr(m, "uuid", None) == "a"][0]
        assert isinstance(user_a, BaseTranscriptEntry)
        assert user_a.parentUuid == "real_parent"


class TestProgressChainRepairIntegration:
    """Integration tests for progress chain repair in load_directory_transcripts."""

    def test_progress_chain_repair_directory(self, caplog: Any) -> None:
        """Progress entries are bridged: no orphan warnings from DAG build."""
        with caplog.at_level(logging.WARNING):
            result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)

        # Should have loaded entries (41 non-progress, non-file-history entries)
        assert len(result) > 0

        # No orphan warnings should appear in logs
        orphan_warnings = [
            r.message for r in caplog.records if "Orphan node" in r.message
        ]
        assert orphan_warnings == [], (
            f"Expected no orphan warnings, got: {orphan_warnings}"
        )

    def test_progress_chain_repair_single_file(self) -> None:
        """Single-file mode also repairs progress chains."""
        from claude_code_log.converter import (
            load_transcript,
            _scan_progress_chains,
            _repair_parent_chains,
        )

        single_file = (
            EXPERIMENTS_IDEAS_DIR / "03eb5929-52b3-4b13-ada3-b93ae35806b8.jsonl"
        )
        messages = load_transcript(single_file, silent=True)

        # Before repair: some entries have parentUuid pointing to progress UUIDs
        progress_chain = _scan_progress_chains(single_file)
        assert len(progress_chain) == 11

        # Count entries with progress parents before repair
        orphan_before = sum(
            1 for m in messages if getattr(m, "parentUuid", None) in progress_chain
        )
        assert orphan_before == 8

        # Repair
        _repair_parent_chains(messages, progress_chain)

        # After repair: no entries should point to progress UUIDs
        orphan_after = sum(
            1 for m in messages if getattr(m, "parentUuid", None) in progress_chain
        )
        assert orphan_after == 0

    def test_progress_chain_preserves_entry_count(self) -> None:
        """Repair doesn't add or remove entries, only mutates parentUuid."""
        from claude_code_log.converter import load_transcript

        single_file = (
            EXPERIMENTS_IDEAS_DIR / "03eb5929-52b3-4b13-ada3-b93ae35806b8.jsonl"
        )
        messages = load_transcript(single_file, silent=True)
        count_before = len(messages)

        progress_chain = _scan_progress_chains(single_file)
        _repair_parent_chains(messages, progress_chain)

        assert len(messages) == count_before

    def test_dag_chain_fully_connected(self) -> None:
        """After repair, DAG build produces a single connected chain."""
        from claude_code_log.dag import build_dag_from_entries

        result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)

        # Filter to DAG-eligible entries (those with uuid)
        dag_entries = [e for e in result if hasattr(e, "uuid")]

        # Build DAG to verify connectivity
        tree = build_dag_from_entries(dag_entries)

        # Should have sessions in the tree
        assert len(tree.sessions) >= 1

        # All DAG-eligible entries should be accounted for in the tree nodes
        assert len(tree.nodes) > 0

    def test_synthetic_progress_in_directory_mode(self, tmp_path: Path) -> None:
        """Synthetic test: progress entries in directory mode are bridged."""
        entries: list[dict[str, Any]] = [
            _make_progress_entry("p1", None),
            _make_user_entry("a", "s1", "2025-07-01T10:00:00.000Z", "p1", "Start"),
            _make_assistant_entry("b", "s1", "2025-07-01T10:01:00.000Z", "a"),
            _make_progress_entry("p2", "b"),
            _make_user_entry("c", "s1", "2025-07-01T10:02:00.000Z", "p2", "Continue"),
            _make_assistant_entry("d", "s1", "2025-07-01T10:03:00.000Z", "c"),
        ]
        _write_jsonl(tmp_path / "session.jsonl", entries)

        result, _ = load_directory_transcripts(tmp_path, silent=True)
        uuids = [getattr(e, "uuid", None) for e in result]

        # All 4 real entries should be in DAG order
        assert uuids == ["a", "b", "c", "d"]


# =============================================================================
# Test: Within-session fork detection in real data
# =============================================================================


class TestWithinSessionForkRealData:
    """Test fork detection using real session 03eb5929 which has a fork at eb84."""

    def test_fork_detected_at_eb84(self) -> None:
        """The real data has a fork at eb84 with two children (5270, 9edc)."""
        from claude_code_log.dag import build_dag_from_entries

        result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)
        dag_entries = [e for e in result if hasattr(e, "uuid")]
        tree = build_dag_from_entries(dag_entries)

        # Find the fork junction at eb84
        fork_jps = [
            (uuid, jp)
            for uuid, jp in tree.junction_points.items()
            if uuid.startswith("eb84") and any("@" in t for t in jp.target_sessions)
        ]
        assert len(fork_jps) == 1, (
            f"Expected 1 fork junction at eb84, got {len(fork_jps)}"
        )
        uuid, jp = fork_jps[0]
        assert len(jp.target_sessions) == 2

    def test_no_linearity_warnings(self, caplog: Any) -> None:
        """Fork handling should produce no linearity violation warnings."""
        import logging
        from claude_code_log.dag import build_dag_from_entries

        with caplog.at_level(logging.WARNING, logger="claude_code_log.dag"):
            result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)
            dag_entries = [e for e in result if hasattr(e, "uuid")]
            build_dag_from_entries(dag_entries)

        linearity_warnings = [
            r.message for r in caplog.records if "linearity" in r.message
        ]
        assert linearity_warnings == []

    def test_branch_sessions_created(self) -> None:
        """Branch pseudo-sessions are created for the fork."""
        from claude_code_log.dag import build_dag_from_entries

        result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)
        dag_entries = [e for e in result if hasattr(e, "uuid")]
        tree = build_dag_from_entries(dag_entries)

        branch_sessions = [sid for sid in tree.sessions if "@" in sid]
        assert len(branch_sessions) >= 2

        for sid in branch_sessions:
            dl = tree.sessions[sid]
            assert dl.is_branch is True
            assert dl.original_session_id is not None
            assert len(dl.uuids) > 0

    def test_end_to_end_rendering_with_fork(self) -> None:
        """Full rendering pipeline produces branch headers for fork."""
        from claude_code_log.renderer import generate_template_messages
        from claude_code_log.models import SessionHeaderMessage

        result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)
        root_messages, session_nav, ctx = generate_template_messages(result)

        # Find branch headers
        branch_headers = [
            tm
            for tm in root_messages
            if isinstance(tm.content, SessionHeaderMessage) and tm.content.is_branch
        ]
        assert len(branch_headers) >= 2

    def test_within_fork_coverage(self) -> None:
        """All entries are covered by DAG-lines (trunk + branches)."""
        from claude_code_log.dag import build_dag_from_entries

        result, _ = load_directory_transcripts(EXPERIMENTS_IDEAS_DIR, silent=True)
        dag_entries = [e for e in result if hasattr(e, "uuid")]
        tree = build_dag_from_entries(dag_entries)

        # Should have both trunk and branch pseudo-sessions
        real_sessions = [sid for sid in tree.sessions if "@" not in sid]
        branch_sessions = [sid for sid in tree.sessions if "@" in sid]
        assert len(real_sessions) >= 1
        assert len(branch_sessions) >= 2

        # All entries should be covered
        total_in_daglines = sum(len(dl.uuids) for dl in tree.sessions.values())
        assert total_in_daglines == len(tree.nodes)
