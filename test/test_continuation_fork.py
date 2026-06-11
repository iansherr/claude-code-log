"""Regression: assistant-continuation tool-flow forks must NOT branch.

When an assistant turn issues a tool_use and Claude Code records both the
turn's continuation (a further assistant message — a parallel next tool_use or
a max_tokens split) AND the tool_result for that tool_use as siblings, the DAG
walker previously mis-read it as a user rewind and forked — recursively,
producing a staircase of spurious branches. It must instead linearize:
continuation inline, then the lagging tool_result.

(The existing ``_stitch_tool_results`` Variant 2 already handles the case where
the continuation subtree is *shallow* / dead-ends; this covers the *deep* live
continuation — `max_tokens` streaming — which Variant 2 bails on.)
"""

from claude_code_log.dag import (
    _is_continuation_fork,
    build_dag_from_entries,
    build_dag,
    build_message_index,
)
from claude_code_log.factories import create_transcript_entry


def _user(uuid, parent, content, ts):
    return create_transcript_entry(
        {
            "type": "user",
            "uuid": uuid,
            "parentUuid": parent,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/x",
            "sessionId": "s1",
            "version": "1.0",
            "timestamp": ts,
            "message": {"role": "user", "content": content},
        }
    )


def _assistant(uuid, parent, content, ts, stop_reason="tool_use"):
    return create_transcript_entry(
        {
            "type": "assistant",
            "uuid": uuid,
            "parentUuid": parent,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/x",
            "sessionId": "s1",
            "version": "1.0",
            "timestamp": ts,
            "requestId": "r-" + uuid,
            "message": {
                "id": "m-" + uuid,
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": content,
            },
        }
    )


def _tool_use(tool_id, name="Bash"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": {"command": "x"}}


def _tool_result(tool_id):
    return [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]


class TestIsContinuationForkDetection:
    """The detection predicate (depth-independent)."""

    def _nodes(self, entries):
        nodes = build_message_index(entries)
        build_dag(nodes)
        return nodes

    def test_assistant_continuation_plus_toolresult_is_continuation_fork(self):
        # a1(tool_use X) → {a2 (assistant continuation), u1 (tool_result for X)}
        nodes = self._nodes(
            [
                _user("u0", None, "go", "2025-01-01T00:00:00Z"),
                _assistant("a1", "u0", [_tool_use("X")], "2025-01-01T00:00:01Z"),
                _assistant("a2", "a1", [_tool_use("Y")], "2025-01-01T00:00:02Z"),
                _user("u1", "a1", _tool_result("X"), "2025-01-01T00:00:30Z"),
            ]
        )
        assert _is_continuation_fork(nodes["a1"], ["a2", "u1"], nodes) is True

    def test_user_rewind_is_not_a_continuation_fork(self):
        # a1 → {a2, u1(new user PROMPT text)} — a real rewind, not tool-flow
        nodes = self._nodes(
            [
                _user("u0", None, "go", "2025-01-01T00:00:00Z"),
                _assistant("a1", "u0", [_tool_use("X")], "2025-01-01T00:00:01Z"),
                _assistant("a2", "a1", [_tool_use("Y")], "2025-01-01T00:00:02Z"),
                _user(
                    "u1", "a1", "actually, do something else", "2025-01-01T00:00:30Z"
                ),
            ]
        )
        assert _is_continuation_fork(nodes["a1"], ["a2", "u1"], nodes) is False

    def test_toolresult_for_unrelated_tool_is_not_continuation_fork(self):
        # u1's tool_result is for a DIFFERENT tool_use id than a1's
        nodes = self._nodes(
            [
                _user("u0", None, "go", "2025-01-01T00:00:00Z"),
                _assistant("a1", "u0", [_tool_use("X")], "2025-01-01T00:00:01Z"),
                _assistant("a2", "a1", [_tool_use("Y")], "2025-01-01T00:00:02Z"),
                _user("u1", "a1", _tool_result("ZZZ"), "2025-01-01T00:00:30Z"),
            ]
        )
        assert _is_continuation_fork(nodes["a1"], ["a2", "u1"], nodes) is False

    def test_no_assistant_child_is_not_continuation_fork(self):
        nodes = self._nodes(
            [
                _user("u0", None, "go", "2025-01-01T00:00:00Z"),
                _assistant("a1", "u0", [_tool_use("X")], "2025-01-01T00:00:01Z"),
                _user("u1", "a1", _tool_result("X"), "2025-01-01T00:00:30Z"),
            ]
        )
        assert _is_continuation_fork(nodes["a1"], ["u1"], nodes) is False


def _deep_continuation_chain(parent, n, start_sec):
    """A single-child assistant chain of length n, so its subtree exceeds the
    dead-end depth cap (>20) — i.e. a *live* continuation, like a max_tokens
    streaming turn."""
    entries = []
    cur = parent
    for i in range(n):
        uid = f"c{i:02d}"
        entries.append(
            _assistant(
                uid,
                cur,
                [{"type": "text", "text": f"step {i}"}],
                f"2025-01-01T00:01:{start_sec + i:02d}Z",
                "end_turn",
            )
        )
        cur = uid
    return entries


class TestContinuationForkInsideBranch:
    """A continuation fork occurring *within* a rewind branch must not drop
    branch segments.

    The re-enqueued continuation/result chains carry the branch's line id,
    so the branch ends up as multiple SessionDAGLine segments. Inserting
    branch lines by key would keep only the last segment — the merge in
    ``extract_session_dag_lines`` must cover branch ids too (PR #214
    review finding).
    """

    def _build(self):
        entries = [
            _user("u0", None, "go", "2025-01-01T00:00:00Z"),
            _assistant(
                "a1",
                "u0",
                [{"type": "text", "text": "which approach?"}],
                "2025-01-01T00:00:01Z",
                "end_turn",
            ),
            # Real rewind at a1: two user prompts at different timestamps.
            _user("u1", "a1", "try approach A", "2025-01-01T00:00:30Z"),
            _user("u2", "a1", "actually, approach B", "2025-01-01T00:10:00Z"),
            # Branch 1 contains a continuation fork at a2.
            _assistant("a2", "u1", [_tool_use("X")], "2025-01-01T00:00:31Z"),
            _assistant("a3", "a2", [_tool_use("Y")], "2025-01-01T00:00:32Z"),
            _user("u3", "a2", _tool_result("X"), "2025-01-01T00:03:00Z"),
            _assistant(
                "a4",
                "u3",
                [{"type": "text", "text": "X done"}],
                "2025-01-01T00:03:01Z",
                "end_turn",
            ),
            # Branch 2 is a plain reply.
            _assistant(
                "a5",
                "u2",
                [{"type": "text", "text": "doing B"}],
                "2025-01-01T00:10:01Z",
                "end_turn",
            ),
        ]
        # Deep live chain under a3 so the continuation fork isn't a V2 dead end.
        entries += _deep_continuation_chain("a3", 25, 0)
        return build_dag_from_entries(entries)

    def test_branch_segments_are_merged_not_overwritten(self):
        tree = self._build()
        branches = [s for s in tree.sessions.values() if s.is_branch]
        assert len(branches) == 2
        (b1,) = [b for b in branches if "u1" in b.uuids]
        # Every segment of branch 1 survives the merge:
        for uuid in ("u1", "a2", "a3", "c24", "u3", "a4"):
            assert uuid in b1.uuids, f"{uuid} dropped from branch line"
        # Continuation inline after its tool_use; lagging result after it.
        assert b1.uuids.index("a2") < b1.uuids.index("a3") < b1.uuids.index("u3")

    def test_merged_branch_keeps_fork_point_attachment(self):
        tree = self._build()
        b1 = next(b for b in tree.sessions.values() if "u1" in b.uuids)
        # The merged line's attachment is the rewind fork point (a1), not
        # the continuation fork's parent (a2) from a later segment.
        assert b1.attachment_uuid == "a1"
        b2 = next(b for b in tree.sessions.values() if "u2" in b.uuids)
        assert b2.attachment_uuid == "a1"


class TestContinuationForkLinearization:
    def _build(self):
        # a1(tool_use X) → { a2 (deep live continuation), u1 (tool_result X) }
        entries = [
            _user("u0", None, "go", "2025-01-01T00:00:00Z"),
            _assistant("a1", "u0", [_tool_use("X")], "2025-01-01T00:00:01Z"),
            _assistant("a2", "a1", [_tool_use("Y")], "2025-01-01T00:00:02Z"),
        ]
        entries += _deep_continuation_chain("a2", 25, 0)  # a2 → c00 … c24 (live)
        # the lagging tool_result for a1's X, and its own follow-up
        entries.append(_user("u1", "a1", _tool_result("X"), "2025-01-01T00:02:00Z"))
        entries.append(
            _assistant(
                "a3",
                "u1",
                [{"type": "text", "text": "X done"}],
                "2025-01-01T00:02:01Z",
                "end_turn",
            )
        )
        return build_dag_from_entries(entries)

    def test_no_branch_created(self):
        tree = self._build()
        branches = [s for s in tree.sessions.values() if s.is_branch]
        assert branches == [], [s.session_id for s in branches]

    def test_continuation_and_result_both_preserved_and_ordered(self):
        tree = self._build()
        trunk = [s for s in tree.sessions.values() if not s.is_branch]
        assert len(trunk) == 1
        uuids = trunk[0].uuids
        # nothing dropped: the continuation chain AND the lagging result survive
        assert "a2" in uuids and "c24" in uuids and "u1" in uuids and "a3" in uuids
        # continuation (a2) inline after its tool_use (a1); lagging result after it
        assert uuids.index("a1") < uuids.index("a2") < uuids.index("u1")
        assert uuids.index("c24") < uuids.index("u1")
