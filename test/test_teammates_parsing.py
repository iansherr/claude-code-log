"""Parser tests for the teammates feature (issue #91, PR #117)."""

from __future__ import annotations

from claude_code_log.factories.agent_metadata_factory import (
    parse_agent_result_metadata,
)
from claude_code_log.factories.teammate_factory import (
    create_teammate_message,
    find_team_lead_body,
    has_teammate_message,
    iter_teammate_blocks,
)
from claude_code_log.models import AgentResultMetadata, MessageMeta


class TestAgentResultMetadata:
    def test_returns_none_for_plain_text(self) -> None:
        body, meta = parse_agent_result_metadata("Hello, world.")
        assert body == "Hello, world."
        assert meta is None

    def test_returns_none_for_empty(self) -> None:
        body, meta = parse_agent_result_metadata("")
        assert body == ""
        assert meta is None

    def test_parses_agent_id_only(self) -> None:
        text = "Done.\n\nagentId: abc123\n"
        body, meta = parse_agent_result_metadata(text)
        assert body == "Done."
        assert meta is not None
        assert meta.agent_id == "abc123"
        assert meta.worktree_path is None
        assert meta.total_tokens is None

    def test_parses_agent_id_with_trailing_sendmessage_hint(self) -> None:
        text = (
            "Work complete.\n"
            "agentId: a4ca7529 (use SendMessage with to: 'x' to continue this agent)\n"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "Work complete."
        assert meta is not None
        # Hint in parens must not be absorbed into the id
        assert meta.agent_id == "a4ca7529"

    def test_parses_worktree_fields(self) -> None:
        text = (
            "Body text.\n"
            "agentId: xyz\n"
            "worktreePath: /home/user/worktrees/agent-xyz\n"
            "worktreeBranch: worktree-agent-xyz\n"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "Body text."
        assert meta is not None
        assert meta.agent_id == "xyz"
        assert meta.worktree_path == "/home/user/worktrees/agent-xyz"
        assert meta.worktree_branch == "worktree-agent-xyz"

    def test_parses_usage_block(self) -> None:
        text = (
            "agent response\n"
            "agentId: a\n"
            "worktreePath: /tmp/a\n"
            "worktreeBranch: b-a\n"
            "<usage>total_tokens: 48421\n"
            "tool_uses: 24\n"
            "duration_ms: 802753</usage>"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "agent response"
        assert meta is not None
        assert meta.total_tokens == 48421
        assert meta.tool_uses == 24
        assert meta.duration_ms == 802753

    def test_usage_block_only(self) -> None:
        """Pre-teammates transcripts may have <usage> alone."""
        text = (
            "Answer.\n<usage>total_tokens: 10\ntool_uses: 1\nduration_ms: 200</usage>"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "Answer."
        assert meta is not None
        assert meta.agent_id is None
        assert meta.total_tokens == 10
        assert meta.tool_uses == 1
        assert meta.duration_ms == 200

    def test_metadata_tail_is_stripped_idempotently(self) -> None:
        text = "Body\n\n\nagentId: x\nworktreePath: /p\n"
        body, meta = parse_agent_result_metadata(text)
        assert body == "Body"
        # Feeding the stripped body back yields None (no tail left).
        _, second = parse_agent_result_metadata(body)
        assert second is None

    def test_result_object_type(self) -> None:
        _, meta = parse_agent_result_metadata("agentId: abc\n")
        assert isinstance(meta, AgentResultMetadata)


def _meta() -> MessageMeta:
    return MessageMeta(session_id="s", timestamp="t", uuid="u")


SINGLE_BLOCK = (
    '<teammate-message teammate_id="alice" color="blue" '
    'summary="relay tests complete">\n'
    "Relay coverage is now 96%.\n"
    "</teammate-message>"
)

MULTI_BLOCK = (
    '<teammate-message teammate_id="alice" color="blue">\n'
    "alice heartbeat: still here.\n"
    "</teammate-message>\n\n"
    '<teammate-message teammate_id="bob" color="green" summary="done">\n'
    "All server tests pass.\n"
    "</teammate-message>\n\n"
    '<teammate-message teammate_id="system">\n'
    "teammate_terminated: alice exited cleanly\n"
    "</teammate-message>"
)


class TestTeammateMessageParser:
    def test_has_teammate_message_detects(self) -> None:
        assert has_teammate_message(SINGLE_BLOCK) is True
        assert has_teammate_message("no tags here") is False
        assert has_teammate_message("<teammate-message") is False  # no close tag

    def test_iter_returns_blocks_in_order(self) -> None:
        ids = [b.teammate_id for b in iter_teammate_blocks(MULTI_BLOCK)]
        assert ids == ["alice", "bob", "system"]

    def test_single_block_attributes_and_body(self) -> None:
        blocks = list(iter_teammate_blocks(SINGLE_BLOCK))
        assert len(blocks) == 1
        b = blocks[0]
        assert b.teammate_id == "alice"
        assert b.color == "blue"
        assert b.summary == "relay tests complete"
        assert b.body == "Relay coverage is now 96%."
        assert b.is_system is False

    def test_block_without_summary(self) -> None:
        text = (
            '<teammate-message teammate_id="alice" color="blue">\n'
            "plain body\n"
            "</teammate-message>"
        )
        b = next(iter_teammate_blocks(text))
        assert b.summary is None
        assert b.color == "blue"

    def test_system_block_flagged(self) -> None:
        blocks = list(iter_teammate_blocks(MULTI_BLOCK))
        system_block = blocks[-1]
        assert system_block.is_system is True
        assert "teammate_terminated" in system_block.body

    def test_create_returns_none_without_block(self) -> None:
        assert create_teammate_message(_meta(), "just some text") is None

    def test_create_batch_single_block(self) -> None:
        content = create_teammate_message(_meta(), SINGLE_BLOCK)
        assert content is not None
        assert len(content.blocks) == 1
        assert content.blocks[0].teammate_id == "alice"
        assert content.leading_text is None
        assert content.trailing_text is None
        assert content.message_type == "teammate"
        assert content.has_markdown is True

    def test_create_batch_mixed_teammates(self) -> None:
        content = create_teammate_message(_meta(), MULTI_BLOCK)
        assert content is not None
        assert [b.teammate_id for b in content.blocks] == ["alice", "bob", "system"]

    def test_leading_and_trailing_text_preserved(self) -> None:
        text = f"Before text\n\n{SINGLE_BLOCK}\n\nAfter text"
        content = create_teammate_message(_meta(), text)
        assert content is not None
        assert content.leading_text == "Before text"
        assert content.trailing_text == "After text"

    def test_find_team_lead_body(self) -> None:
        wrapped = (
            '<teammate-message teammate_id="team-lead" color="cyan">\n'
            "do the thing\n"
            "</teammate-message>"
        )
        assert find_team_lead_body(wrapped) == "do the thing"
        assert find_team_lead_body(SINGLE_BLOCK) is None
        assert find_team_lead_body("") is None
