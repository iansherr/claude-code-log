"""Tests for the DAG-based message ordering module."""

import json
from pathlib import Path

import pytest

from claude_code_log.dag import (
    SessionTree,
    build_dag,
    build_dag_from_entries,
    build_message_index,
    extract_session_dag_lines,
    traverse_session_tree,
)
from claude_code_log.factories import create_transcript_entry
from claude_code_log.models import (
    SummaryTranscriptEntry,
    TranscriptEntry,
)

TEST_DATA = Path(__file__).parent / "test_data"
REAL_PROJECTS = TEST_DATA / "real_projects"


def load_entries_from_jsonl(path: Path) -> list[TranscriptEntry]:
    """Load transcript entries from a JSONL file, skipping unparseable lines."""
    entries: list[TranscriptEntry] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            entry_type = data.get("type")
            if entry_type in (
                "user",
                "assistant",
                "summary",
                "system",
                "queue-operation",
            ):
                entries.append(create_transcript_entry(data))
    return entries


def load_project_entries(project_dir: Path) -> list[TranscriptEntry]:
    """Load all entries from a project directory (excluding agent files)."""
    entries: list[TranscriptEntry] = []
    for jsonl_file in sorted(project_dir.glob("*.jsonl")):
        if jsonl_file.name.startswith("agent-"):
            continue
        entries.extend(load_entries_from_jsonl(jsonl_file))
    return entries


# =============================================================================
# Test: Single session (dag_simple.jsonl)
# =============================================================================


class TestSingleSession:
    """Tests using dag_simple.jsonl: a→b→c→d→e in session s1."""

    @pytest.fixture()
    def tree(self) -> SessionTree:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        return build_dag_from_entries(entries)

    def test_single_session_one_dagline(self, tree: SessionTree) -> None:
        assert len(tree.sessions) == 1
        assert "s1" in tree.sessions

    def test_single_session_chain_order(self, tree: SessionTree) -> None:
        dag_line = tree.sessions["s1"]
        assert dag_line.uuids == ["a", "b", "c", "d", "e"]

    def test_single_session_is_root(self, tree: SessionTree) -> None:
        assert tree.roots == ["s1"]

    def test_single_session_no_junction_points(self, tree: SessionTree) -> None:
        assert tree.junction_points == {}

    def test_single_session_traversal(self, tree: SessionTree) -> None:
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["a", "b", "c", "d", "e"]

    def test_single_session_first_timestamp(self, tree: SessionTree) -> None:
        dag_line = tree.sessions["s1"]
        assert dag_line.first_timestamp == "2025-07-01T10:00:00.000Z"

    def test_single_session_no_parent(self, tree: SessionTree) -> None:
        dag_line = tree.sessions["s1"]
        assert dag_line.parent_session_id is None
        assert dag_line.attachment_uuid is None


# =============================================================================
# Test: Resume session (dag_resume.jsonl)
# =============================================================================


class TestResumeSession:
    """Tests using dag_resume.jsonl: s1(a→b→c→d→e), s2(f→g→h) where f.parent=e."""

    @pytest.fixture()
    def tree(self) -> SessionTree:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_resume.jsonl")
        return build_dag_from_entries(entries)

    def test_two_sessions(self, tree: SessionTree) -> None:
        assert len(tree.sessions) == 2
        assert "s1" in tree.sessions
        assert "s2" in tree.sessions

    def test_s1_chain(self, tree: SessionTree) -> None:
        assert tree.sessions["s1"].uuids == ["a", "b", "c", "d", "e"]

    def test_s2_chain(self, tree: SessionTree) -> None:
        assert tree.sessions["s2"].uuids == ["f", "g", "h"]

    def test_s1_is_root(self, tree: SessionTree) -> None:
        assert tree.roots == ["s1"]

    def test_s2_parent_is_s1(self, tree: SessionTree) -> None:
        dag_line = tree.sessions["s2"]
        assert dag_line.parent_session_id == "s1"
        assert dag_line.attachment_uuid == "e"

    def test_junction_at_e(self, tree: SessionTree) -> None:
        assert "e" in tree.junction_points
        jp = tree.junction_points["e"]
        assert jp.session_id == "s1"
        assert jp.target_sessions == ["s2"]

    def test_traversal_order(self, tree: SessionTree) -> None:
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["a", "b", "c", "d", "e", "f", "g", "h"]


# =============================================================================
# Test: Fork session (dag_fork.jsonl)
# =============================================================================


class TestForkSession:
    """Tests using dag_fork.jsonl: s1(a→e), s2(f→h from e), s3(i→k from c)."""

    @pytest.fixture()
    def tree(self) -> SessionTree:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_fork.jsonl")
        return build_dag_from_entries(entries)

    def test_three_sessions(self, tree: SessionTree) -> None:
        assert len(tree.sessions) == 3

    def test_s1_chain(self, tree: SessionTree) -> None:
        assert tree.sessions["s1"].uuids == ["a", "b", "c", "d", "e"]

    def test_s2_chain(self, tree: SessionTree) -> None:
        assert tree.sessions["s2"].uuids == ["f", "g", "h"]

    def test_s3_chain(self, tree: SessionTree) -> None:
        assert tree.sessions["s3"].uuids == ["i", "j", "k"]

    def test_only_s1_is_root(self, tree: SessionTree) -> None:
        assert tree.roots == ["s1"]

    def test_s2_attaches_at_e(self, tree: SessionTree) -> None:
        dag_line = tree.sessions["s2"]
        assert dag_line.parent_session_id == "s1"
        assert dag_line.attachment_uuid == "e"

    def test_s3_attaches_at_c(self, tree: SessionTree) -> None:
        dag_line = tree.sessions["s3"]
        assert dag_line.parent_session_id == "s1"
        assert dag_line.attachment_uuid == "c"

    def test_two_junction_points(self, tree: SessionTree) -> None:
        assert len(tree.junction_points) == 2
        assert "c" in tree.junction_points
        assert "e" in tree.junction_points

    def test_junction_c_targets_s3(self, tree: SessionTree) -> None:
        jp = tree.junction_points["c"]
        assert jp.session_id == "s1"
        assert jp.target_sessions == ["s3"]

    def test_junction_e_targets_s2(self, tree: SessionTree) -> None:
        jp = tree.junction_points["e"]
        assert jp.session_id == "s1"
        assert jp.target_sessions == ["s2"]

    def test_traversal_depth_first(self, tree: SessionTree) -> None:
        """Depth-first: s1 entries, then at junction c visit s3 (fork),
        continue s1, then at junction e visit s2 (continue)."""
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        # s1: a,b,c → s3: i,j,k → s1: d,e → s2: f,g,h
        assert uuids == ["a", "b", "c", "i", "j", "k", "d", "e", "f", "g", "h"]


# =============================================================================
# Test: Deduplication
# =============================================================================


class TestDeduplication:
    """Test that duplicate uuids are resolved by keeping earliest session."""

    def test_dedup_keeps_earliest_session(self) -> None:
        """Same uuid in two sessions; entry from earlier session wins."""
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        # Manually add a duplicate of uuid "c" with a later session
        dup_data = {
            "type": "user",
            "timestamp": "2025-07-02T10:00:00.000Z",
            "parentUuid": "b",
            "isSidechain": False,
            "userType": "human",
            "cwd": "/tmp",
            "sessionId": "s_later",
            "version": "1.0.0",
            "uuid": "c",
            "message": {"role": "user", "content": [{"type": "text", "text": "Dup"}]},
        }
        entries.append(create_transcript_entry(dup_data))

        nodes = build_message_index(entries)
        # "c" should belong to s1 (timestamp 2025-07-01) not s_later (2025-07-02)
        assert nodes["c"].session_id == "s1"

    def test_dedup_replaces_with_earlier(self) -> None:
        """If later-loaded entry is from an earlier session, it replaces."""
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        # Add a duplicate of uuid "c" from an EARLIER session
        dup_data = {
            "type": "user",
            "timestamp": "2025-06-30T10:00:00.000Z",
            "parentUuid": "b",
            "isSidechain": False,
            "userType": "human",
            "cwd": "/tmp",
            "sessionId": "s_earlier",
            "version": "1.0.0",
            "uuid": "c",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Earlier"}],
            },
        }
        entries.append(create_transcript_entry(dup_data))

        nodes = build_message_index(entries)
        # "c" should now belong to s_earlier
        assert nodes["c"].session_id == "s_earlier"


# =============================================================================
# Test: Junction Points
# =============================================================================


class TestJunctionPoints:
    """Detailed junction point tests."""

    def test_no_junctions_single_session(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        tree = build_dag_from_entries(entries)
        assert len(tree.junction_points) == 0

    def test_single_junction_resume(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_resume.jsonl")
        tree = build_dag_from_entries(entries)
        assert len(tree.junction_points) == 1
        assert "e" in tree.junction_points

    def test_multiple_junctions_fork(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_fork.jsonl")
        tree = build_dag_from_entries(entries)
        assert len(tree.junction_points) == 2
        # Both c and e are junctions
        assert set(tree.junction_points.keys()) == {"c", "e"}

    def test_junction_target_sessions_ordered_chronologically(self) -> None:
        """If multiple sessions fork from the same point, targets are ordered."""
        # Create data where two sessions both fork from the same message
        base = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        fork1_data = {
            "type": "user",
            "timestamp": "2025-07-02T10:00:00.000Z",
            "parentUuid": "c",
            "isSidechain": False,
            "userType": "human",
            "cwd": "/tmp",
            "sessionId": "s_fork1",
            "version": "1.0.0",
            "uuid": "f1",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Fork 1"}],
            },
        }
        fork2_data = {
            "type": "user",
            "timestamp": "2025-07-01T12:00:00.000Z",
            "parentUuid": "c",
            "isSidechain": False,
            "userType": "human",
            "cwd": "/tmp",
            "sessionId": "s_fork2",
            "version": "1.0.0",
            "uuid": "f2",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Fork 2"}],
            },
        }
        base.append(create_transcript_entry(fork1_data))
        base.append(create_transcript_entry(fork2_data))

        tree = build_dag_from_entries(base)
        jp = tree.junction_points["c"]
        # s_fork2 is earlier (2025-07-01T12:00) than s_fork1 (2025-07-02T10:00)
        assert jp.target_sessions == ["s_fork2", "s_fork1"]


# =============================================================================
# Test: Traversal Order
# =============================================================================


class TestTraversalOrder:
    """Test depth-first session tree traversal produces correct order."""

    def test_simple_traversal(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        tree = build_dag_from_entries(entries)
        result = traverse_session_tree(tree)
        assert len(result) == 5
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["a", "b", "c", "d", "e"]

    def test_resume_traversal(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_resume.jsonl")
        tree = build_dag_from_entries(entries)
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["a", "b", "c", "d", "e", "f", "g", "h"]

    def test_fork_traversal(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_fork.jsonl")
        tree = build_dag_from_entries(entries)
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        # Depth-first: at junction c (after emitting c), visit s3 first
        # then continue s1, at junction e visit s2
        assert uuids == ["a", "b", "c", "i", "j", "k", "d", "e", "f", "g", "h"]

    def test_traversal_returns_entries(self) -> None:
        """Verify traversal returns actual TranscriptEntry objects."""
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        tree = build_dag_from_entries(entries)
        result = traverse_session_tree(tree)
        for entry in result:
            assert hasattr(entry, "type")
            assert hasattr(entry, "uuid")


# =============================================================================
# Test: Real project data
# =============================================================================


EXPERIMENTS_DIR = REAL_PROJECTS / "-src-experiments-claude_p"


@pytest.mark.skipif(
    not EXPERIMENTS_DIR.exists(),
    reason="Real project test data not available",
)
class TestRealProjectExperiments:
    """Test DAG construction against real project data.

    The -src-experiments-claude_p project has 4 independent sessions
    with no cross-session references, so should produce 4 root sessions.
    """

    @pytest.fixture()
    def tree(self) -> SessionTree:
        entries = load_project_entries(EXPERIMENTS_DIR)
        return build_dag_from_entries(entries)

    def test_loads_multiple_sessions(self, tree: SessionTree) -> None:
        assert len(tree.sessions) >= 4

    def test_all_sessions_are_roots(self, tree: SessionTree) -> None:
        """Independent sessions should all be roots."""
        # All sessions with DAG-lines should be root (no cross-refs)
        for session_id in tree.sessions:
            dag_line = tree.sessions[session_id]
            assert dag_line.parent_session_id is None, (
                f"Session {session_id} unexpectedly has parent "
                f"{dag_line.parent_session_id}"
            )

    def test_no_junction_points(self, tree: SessionTree) -> None:
        """Independent sessions should have no junction points."""
        assert len(tree.junction_points) == 0

    def test_each_session_has_entries(self, tree: SessionTree) -> None:
        """Each session's DAG-line should have at least one message."""
        for session_id, dag_line in tree.sessions.items():
            assert len(dag_line.uuids) > 0, f"Session {session_id} has empty DAG-line"

    def test_traversal_covers_all_entries(self, tree: SessionTree) -> None:
        """Traversal should include all entries from all sessions."""
        total_in_daglines = sum(len(dl.uuids) for dl in tree.sessions.values())
        result = traverse_session_tree(tree)
        assert len(result) == total_in_daglines

    def test_sessions_ordered_chronologically(self, tree: SessionTree) -> None:
        """Root sessions should be ordered by first_timestamp."""
        timestamps = [tree.sessions[sid].first_timestamp for sid in tree.roots]
        assert timestamps == sorted(timestamps)


# =============================================================================
# Test: Edge cases
# =============================================================================


class TestOrphanParent:
    """Test handling of parentUuid pointing to unknown uuid."""

    def test_orphan_treated_as_root(self) -> None:
        """A session whose first message has parentUuid pointing to
        an unknown uuid should be treated as a root session."""
        orphan_data = {
            "type": "user",
            "timestamp": "2025-07-01T10:00:00.000Z",
            "parentUuid": "nonexistent_uuid",
            "isSidechain": False,
            "userType": "human",
            "cwd": "/tmp",
            "sessionId": "s_orphan",
            "version": "1.0.0",
            "uuid": "orphan_a",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Orphan"}],
            },
        }
        entries: list[TranscriptEntry] = [create_transcript_entry(orphan_data)]
        tree = build_dag_from_entries(entries)

        assert "s_orphan" in tree.sessions
        assert tree.roots == ["s_orphan"]
        dag_line = tree.sessions["s_orphan"]
        assert dag_line.parent_session_id is None

    def test_orphan_with_children(self) -> None:
        """Orphan node still chains correctly within its session."""
        data = [
            {
                "type": "user",
                "timestamp": "2025-07-01T10:00:00.000Z",
                "parentUuid": "nonexistent",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "x",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Start"}],
                },
            },
            {
                "type": "assistant",
                "timestamp": "2025-07-01T10:01:00.000Z",
                "parentUuid": "x",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "y",
                "requestId": "req_1",
                "message": {
                    "id": "y",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-sonnet",
                    "content": [{"type": "text", "text": "Reply"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        assert tree.sessions["s1"].uuids == ["x", "y"]
        assert tree.roots == ["s1"]


class TestSummaryEntriesSkipped:
    """Test that SummaryTranscriptEntry entries are excluded from DAG."""

    def test_summary_not_in_nodes(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        # Add a summary entry
        summary_data = {
            "type": "summary",
            "summary": "A test summary",
            "leafUuid": "e",
            "timestamp": "2025-07-01T10:05:00.000Z",
        }
        entries.append(create_transcript_entry(summary_data))

        nodes = build_message_index(entries)
        # Summary has no uuid, so it can't be in nodes
        for node in nodes.values():
            assert not isinstance(node.entry, SummaryTranscriptEntry)

    def test_summary_not_in_traversal(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        summary_data = {
            "type": "summary",
            "summary": "A test summary",
            "leafUuid": "e",
            "timestamp": "2025-07-01T10:05:00.000Z",
        }
        entries.append(create_transcript_entry(summary_data))

        tree = build_dag_from_entries(entries)
        result = traverse_session_tree(tree)
        # Should still be 5 entries (a-e), summary excluded
        assert len(result) == 5
        for entry in result:
            assert not isinstance(entry, SummaryTranscriptEntry)


class TestQueueOperationSkipped:
    """Test that QueueOperationTranscriptEntry entries are excluded from DAG."""

    def test_queue_op_not_in_nodes(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        queue_data = {
            "type": "queue-operation",
            "operation": "dequeue",
            "timestamp": "2025-07-01T09:59:00.000Z",
            "sessionId": "s1",
        }
        entries.append(create_transcript_entry(queue_data))

        nodes = build_message_index(entries)
        # queue-operation has no uuid field
        assert len(nodes) == 5  # Only a,b,c,d,e


# =============================================================================
# Test: Individual algorithm steps
# =============================================================================


class TestBuildMessageIndex:
    """Test build_message_index in isolation."""

    def test_indexes_all_entries(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        nodes = build_message_index(entries)
        assert set(nodes.keys()) == {"a", "b", "c", "d", "e"}

    def test_preserves_entry_data(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        nodes = build_message_index(entries)
        assert nodes["a"].session_id == "s1"
        assert nodes["a"].parent_uuid is None
        assert nodes["b"].parent_uuid == "a"

    def test_empty_entries(self) -> None:
        nodes = build_message_index([])
        assert nodes == {}


class TestBuildDAG:
    """Test build_dag (parent→children links)."""

    def test_children_populated(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        nodes = build_message_index(entries)
        build_dag(nodes)
        assert nodes["a"].children_uuids == ["b"]
        assert nodes["b"].children_uuids == ["c"]
        assert nodes["e"].children_uuids == []

    def test_root_has_no_parent(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        nodes = build_message_index(entries)
        build_dag(nodes)
        assert nodes["a"].parent_uuid is None

    def test_cross_session_children(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_resume.jsonl")
        nodes = build_message_index(entries)
        build_dag(nodes)
        # "e" has child "f" which is in a different session
        assert "f" in nodes["e"].children_uuids


class TestExtractSessionDAGLines:
    """Test extract_session_dag_lines."""

    def test_single_session_chain(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_simple.jsonl")
        nodes = build_message_index(entries)
        build_dag(nodes)
        sessions = extract_session_dag_lines(nodes)
        assert sessions["s1"].uuids == ["a", "b", "c", "d", "e"]

    def test_multi_session_chains(self) -> None:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_resume.jsonl")
        nodes = build_message_index(entries)
        build_dag(nodes)
        sessions = extract_session_dag_lines(nodes)
        assert sessions["s1"].uuids == ["a", "b", "c", "d", "e"]
        assert sessions["s2"].uuids == ["f", "g", "h"]


# =============================================================================
# Test: Degenerate parentUuid (all null)
# =============================================================================


class TestDegenerateParentUuid:
    """Test handling of entries where all parentUuid values are null.

    This is the common case for existing test data and older transcripts
    that lack parentUuid chains. The DAG should fall back to timestamp sort.
    """

    @pytest.fixture()
    def entries(self) -> list[TranscriptEntry]:
        """Create 5 entries with parentUuid=null, out of timestamp order."""
        data = [
            {
                "type": "user",
                "timestamp": f"2025-07-01T10:0{i}:00.000Z",
                "parentUuid": None,
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": f"msg_{i}",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": f"Message {i}"}],
                },
            }
            for i in [3, 1, 4, 0, 2]  # Out of order
        ]
        return [create_transcript_entry(d) for d in data]

    def test_all_null_parent_uuid_falls_back_to_timestamp(
        self, entries: list[TranscriptEntry]
    ) -> None:
        """All 5 entries with parentUuid=null should appear in timestamp order."""
        nodes = build_message_index(entries)
        build_dag(nodes)
        sessions = extract_session_dag_lines(nodes)

        dag_line = sessions["s1"]
        assert len(dag_line.uuids) == 5
        # Should be sorted by timestamp
        assert dag_line.uuids == ["msg_0", "msg_1", "msg_2", "msg_3", "msg_4"]

    def test_all_null_traversal_returns_all_entries(
        self, entries: list[TranscriptEntry]
    ) -> None:
        """Traversal should return all 5 entries, none dropped."""
        tree = build_dag_from_entries(entries)
        result = traverse_session_tree(tree)
        assert len(result) == 5
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["msg_0", "msg_1", "msg_2", "msg_3", "msg_4"]


# =============================================================================
# Test: Within-session fork (dag_within_fork.jsonl)
# =============================================================================


class TestWithinSessionFork:
    """Tests using dag_within_fork.jsonl: s1(a→b→c) with fork at c.

    c has two same-session children: d→e→f (branch 1) and d'→e' (branch 2).
    This produces three DAG-lines: trunk (a,b,c), branch 1 (d,e,f), branch 2 (d',e').
    """

    @pytest.fixture()
    def tree(self) -> SessionTree:
        entries = load_entries_from_jsonl(TEST_DATA / "dag_within_fork.jsonl")
        return build_dag_from_entries(entries)

    def test_trunk_stops_at_fork(self, tree: SessionTree) -> None:
        """Trunk DAG-line stops at fork point c."""
        assert "s1" in tree.sessions
        assert tree.sessions["s1"].uuids == ["a", "b", "c"]

    def test_branch_sessions_created(self, tree: SessionTree) -> None:
        """Two branch pseudo-sessions are created."""
        branch_ids = [sid for sid in tree.sessions if "@" in sid]
        assert len(branch_ids) == 2

    def test_branch1_chain(self, tree: SessionTree) -> None:
        """Branch 1 (d→e→f) has correct chain."""
        branch1_id = "s1@d"
        assert branch1_id in tree.sessions
        assert tree.sessions[branch1_id].uuids == ["d", "e", "f"]

    def test_branch2_chain(self, tree: SessionTree) -> None:
        """Branch 2 (d'→e') has correct chain."""
        branch2_id = "s1@d_prime"
        assert branch2_id in tree.sessions
        assert tree.sessions[branch2_id].uuids == ["d_prime", "e_prime"]

    def test_branches_are_marked(self, tree: SessionTree) -> None:
        """Branch DAG-lines have is_branch=True and original_session_id set."""
        for sid in tree.sessions:
            if "@" in sid:
                dl = tree.sessions[sid]
                assert dl.is_branch is True
                assert dl.original_session_id == "s1"
            else:
                dl = tree.sessions[sid]
                assert dl.is_branch is False
                assert dl.original_session_id is None

    def test_trunk_is_root(self, tree: SessionTree) -> None:
        """Only trunk s1 is a root session."""
        assert tree.roots == ["s1"]

    def test_branches_parent_is_trunk(self, tree: SessionTree) -> None:
        """Both branches have parent_session_id = trunk."""
        for sid in tree.sessions:
            if "@" in sid:
                assert tree.sessions[sid].parent_session_id == "s1"
                assert tree.sessions[sid].attachment_uuid == "c"

    def test_junction_at_fork_point(self, tree: SessionTree) -> None:
        """Fork point c is a junction point with both branches as targets."""
        assert "c" in tree.junction_points
        jp = tree.junction_points["c"]
        assert jp.session_id == "s1"
        assert len(jp.target_sessions) == 2
        assert "s1@d" in jp.target_sessions
        assert "s1@d_prime" in jp.target_sessions

    def test_traversal_order(self, tree: SessionTree) -> None:
        """Depth-first: trunk, then branch 1 at junction, then branch 2."""
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["a", "b", "c", "d", "e", "f", "d_prime", "e_prime"]

    def test_node_session_ids_updated(self, tree: SessionTree) -> None:
        """MessageNode.session_id is updated for branch nodes."""
        assert tree.nodes["a"].session_id == "s1"
        assert tree.nodes["b"].session_id == "s1"
        assert tree.nodes["c"].session_id == "s1"
        assert tree.nodes["d"].session_id == "s1@d"
        assert tree.nodes["e"].session_id == "s1@d"
        assert tree.nodes["f"].session_id == "s1@d"
        assert tree.nodes["d_prime"].session_id == "s1@d_prime"
        assert tree.nodes["e_prime"].session_id == "s1@d_prime"

    def test_traversal_covers_all_entries(self, tree: SessionTree) -> None:
        """All 8 entries should appear in traversal."""
        result = traverse_session_tree(tree)
        assert len(result) == 8


class TestNestedFork:
    """Test nested within-session forks (fork within a fork)."""

    def test_nested_fork(self) -> None:
        """Session with fork at b, then nested fork at d within first branch."""
        # a → b (fork) → d (fork) → f, g
        #             → e
        data = [
            {
                "type": "user",
                "timestamp": "2025-07-01T10:00:00.000Z",
                "parentUuid": None,
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "a",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Start"}],
                },
            },
            {
                "type": "assistant",
                "timestamp": "2025-07-01T10:01:00.000Z",
                "parentUuid": "a",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "b",
                "requestId": "req_1",
                "message": {
                    "id": "b",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-sonnet",
                    "content": [{"type": "text", "text": "Fork point 1"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            # Branch 1 from b: c → d (fork)
            {
                "type": "user",
                "timestamp": "2025-07-01T10:02:00.000Z",
                "parentUuid": "b",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "c",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Branch 1"}],
                },
            },
            {
                "type": "assistant",
                "timestamp": "2025-07-01T10:03:00.000Z",
                "parentUuid": "c",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "d",
                "requestId": "req_2",
                "message": {
                    "id": "d",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-sonnet",
                    "content": [{"type": "text", "text": "Fork point 2"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            # Nested branch 1a from d
            {
                "type": "user",
                "timestamp": "2025-07-01T10:04:00.000Z",
                "parentUuid": "d",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "f",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Nested branch 1a"}],
                },
            },
            # Nested branch 1b from d
            {
                "type": "user",
                "timestamp": "2025-07-01T10:05:00.000Z",
                "parentUuid": "d",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "g",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Nested branch 1b"}],
                },
            },
            # Branch 2 from b
            {
                "type": "user",
                "timestamp": "2025-07-01T10:06:00.000Z",
                "parentUuid": "b",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "uuid": "e",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Branch 2"}],
                },
            },
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # Trunk: a, b (stops at fork)
        assert tree.sessions["s1"].uuids == ["a", "b"]

        # Branch 1 from b: c, d (stops at nested fork)
        branch1_id = "s1@c"
        assert branch1_id in tree.sessions
        assert tree.sessions[branch1_id].uuids == ["c", "d"]

        # Nested branches from d (within branch 1)
        nested1a_id = f"{branch1_id}@f"
        nested1b_id = f"{branch1_id}@g"
        assert nested1a_id in tree.sessions
        assert tree.sessions[nested1a_id].uuids == ["f"]
        assert nested1b_id in tree.sessions
        assert tree.sessions[nested1b_id].uuids == ["g"]

        # Branch 2 from b
        branch2_id = "s1@e"
        assert branch2_id in tree.sessions
        assert tree.sessions[branch2_id].uuids == ["e"]

        # Traversal
        result = traverse_session_tree(tree)
        uuids = [e.uuid for e in result]  # type: ignore[union-attr]
        assert uuids == ["a", "b", "c", "d", "f", "g", "e"]


def _make_entry(
    etype: str,
    uuid: str,
    parent: str | None,
    ts: str,
    session: str = "s1",
    text: str = "",
) -> dict:
    """Helper to build a minimal transcript entry dict."""
    base = {
        "type": etype,
        "timestamp": ts,
        "parentUuid": parent,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": session,
        "version": "1.0.0",
        "uuid": uuid,
    }
    if etype == "user":
        base["message"] = {
            "role": "user",
            "content": [{"type": "text", "text": text or uuid}],
        }
    elif etype == "assistant":
        base["requestId"] = f"req_{uuid}"
        base["message"] = {
            "id": uuid,
            "type": "message",
            "role": "assistant",
            "model": "claude-3-sonnet",
            "content": [{"type": "text", "text": text or uuid}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    elif etype == "system":
        base["message"] = {"content": []}
    return base


class TestCompactionReplay:
    """Context compaction replays should not create branches."""

    def test_same_timestamp_children_not_forked(self) -> None:
        """Multiple children with identical timestamps are compaction replays."""
        # a → sys → replay1, replay2, replay3 (all same ts)
        data = [
            _make_entry("user", "a", None, "2025-07-01T10:00:00.000Z"),
            _make_entry("system", "sys", "a", "2025-07-01T10:01:00.000Z"),
            _make_entry("assistant", "r1", "sys", "2025-07-01T10:02:00.000Z"),
            _make_entry("assistant", "r2", "sys", "2025-07-01T10:02:00.000Z"),
            _make_entry("assistant", "r3", "sys", "2025-07-01T10:02:00.000Z"),
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # Should be a single linear session, no branches
        assert "s1" in tree.sessions
        assert tree.sessions["s1"].uuids == ["a", "sys", "r1"]
        branch_count = sum(1 for s in tree.sessions.values() if s.is_branch)
        assert branch_count == 0

    def test_different_timestamps_create_branches(self) -> None:
        """Children with different timestamps are real forks (rewinds)."""
        data = [
            _make_entry("user", "a", None, "2025-07-01T10:00:00.000Z"),
            _make_entry("assistant", "b", "a", "2025-07-01T10:01:00.000Z"),
            _make_entry("user", "c", "b", "2025-07-01T10:02:00.000Z"),
            _make_entry("user", "d", "b", "2025-07-01T10:05:00.000Z"),
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # Trunk stops at b, two branches
        assert tree.sessions["s1"].uuids == ["a", "b"]
        branch_count = sum(1 for s in tree.sessions.values() if s.is_branch)
        assert branch_count == 2


class TestToolResultStitching:
    """Tool-result side-branches should be stitched into the main chain."""

    def test_single_tool_result_stitched(self) -> None:
        """A(tool_use) → U(result) + A(next) should become linear."""
        # a → tool_use → tool_result (dead end) + next_assistant
        data = [
            _make_entry("user", "a", None, "2025-07-01T10:00:00.000Z"),
            _make_entry("assistant", "tool1", "a", "2025-07-01T10:01:00.000Z"),
            _make_entry("user", "result1", "tool1", "2025-07-01T10:01:00.100Z"),
            _make_entry("assistant", "tool2", "tool1", "2025-07-01T10:01:00.200Z"),
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # Should be linear: a → tool1 → result1 → tool2
        assert tree.sessions["s1"].uuids == ["a", "tool1", "result1", "tool2"]
        branch_count = sum(1 for s in tree.sessions.values() if s.is_branch)
        assert branch_count == 0

    def test_multiple_tool_results_stitched(self) -> None:
        """Multiple parallel tool_use with results should all be stitched."""
        # a → tool1 → result1 (dead end) + result2 (dead end) + tool2
        data = [
            _make_entry("user", "a", None, "2025-07-01T10:00:00.000Z"),
            _make_entry("assistant", "tool1", "a", "2025-07-01T10:01:00.000Z"),
            _make_entry("user", "res1", "tool1", "2025-07-01T10:01:00.100Z"),
            _make_entry("user", "res2", "tool1", "2025-07-01T10:01:00.150Z"),
            _make_entry("assistant", "tool2", "tool1", "2025-07-01T10:01:00.200Z"),
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # Should be linear: a → tool1 → res1 → res2 → tool2
        assert tree.sessions["s1"].uuids == ["a", "tool1", "res1", "res2", "tool2"]
        branch_count = sum(1 for s in tree.sessions.values() if s.is_branch)
        assert branch_count == 0

    def test_user_child_with_continuation_stitched(self) -> None:
        """Variant 2: User child continues, Assistant subtree dead-ends.
        Should stitch linearly with dead-end first, then continuation."""
        data = [
            _make_entry("user", "a", None, "2025-07-01T10:00:00.000Z"),
            _make_entry("assistant", "b", "a", "2025-07-01T10:01:00.000Z"),
            _make_entry("user", "c", "b", "2025-07-01T10:02:00.000Z"),
            _make_entry("assistant", "d", "b", "2025-07-01T10:03:00.000Z"),
            # c continues (not a dead end), d is a leaf (dead end)
            _make_entry("assistant", "e", "c", "2025-07-01T10:04:00.000Z"),
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # Should stitch: dead-end d first, then continuing c
        assert tree.sessions["s1"].uuids == ["a", "b", "d", "c", "e"]
        branch_count = sum(1 for s in tree.sessions.values() if s.is_branch)
        assert branch_count == 0

    def test_assistant_subtree_dead_end_stitched(self) -> None:
        """Variant 2 with deeper dead-end: Assistant child has a subtree
        that eventually terminates, User child continues."""
        data = [
            _make_entry("user", "a", None, "2025-07-01T10:00:00.000Z"),
            _make_entry("assistant", "b", "a", "2025-07-01T10:01:00.000Z"),
            # b forks into assistant d (dead-end subtree) and user c (continues)
            _make_entry("assistant", "d", "b", "2025-07-01T10:02:00.000Z"),
            _make_entry("user", "c", "b", "2025-07-01T10:02:50.000Z"),
            # d's subtree: d → d2 → d3 (all dead ends)
            _make_entry("assistant", "d2", "d", "2025-07-01T10:02:01.000Z"),
            _make_entry("user", "d3", "d2", "2025-07-01T10:02:02.000Z"),
            # c continues the main chain
            _make_entry("assistant", "e", "c", "2025-07-01T10:03:00.000Z"),
        ]
        entries = [create_transcript_entry(d) for d in data]
        tree = build_dag_from_entries(entries)

        # d's subtree dead-ends, c continues: stitch d first, then c
        assert tree.sessions["s1"].uuids == ["a", "b", "d", "c", "e"]
        branch_count = sum(1 for s in tree.sessions.values() if s.is_branch)
        assert branch_count == 0
