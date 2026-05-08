"""Tests for ``type: "attachment"`` hook rendering (issue #128).

Covers:
- Pydantic parse of the issue's example payload (anchored on ``parentUuid``).
- Factory dispatch to ``HookAttachmentMessage`` for each hook flavour.
- Non-hook attachment types stay structural (factory returns ``None``).
- Full-detail HTML rendering surfaces the hook payload.
- HIGH and below detail levels drop hook attachments.
- Parent-anchor: the rendered attachment's parent_uuid matches the
  source's ``parentUuid`` (not ``toolUseID``).
"""

from __future__ import annotations

from typing import Any

from claude_code_log.factories.attachment_factory import create_attachment_message
from claude_code_log.factories.transcript_factory import create_transcript_entry
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.html.system_formatters import format_hook_attachment_content
from claude_code_log.models import (
    AttachmentTranscriptEntry,
    DetailLevel,
    HookAttachmentMessage,
    TranscriptEntry,
)


# Common scaffolding fields for synthetic attachment entries — these
# match the BaseTranscriptEntry contract so model_validate succeeds.
_BASE_FIELDS: dict[str, Any] = {
    "isSidechain": False,
    "userType": "external",
    "cwd": "/home/cboos/proj",
    "sessionId": "sess-test",
    "version": "2.1.0",
}


def _make_attachment(
    uuid: str, parent_uuid: str | None, payload: dict[str, Any]
) -> dict[str, Any]:
    return {
        **_BASE_FIELDS,
        "type": "attachment",
        "uuid": uuid,
        "parentUuid": parent_uuid,
        "timestamp": "2026-01-01T00:00:00.000Z",
        "attachment": payload,
    }


# ---------------------------------------------------------------------------
# Parsing & factory


class TestAttachmentParsing:
    def test_issue_example_payload(self) -> None:
        """The exact PostToolUse:TaskUpdate sample from issue #128."""
        raw = _make_attachment(
            uuid="f14b46f3-aaaa-4000-8000-000000000001",
            parent_uuid="792025a9-bf61-4d42-af9f-6fd9ccce4d96",
            payload={
                "type": "hook_success",
                "hookName": "PostToolUse:TaskUpdate",
                "toolUseID": "toolu_018zdqTEqBHAyFpgGNy1wo4S",
                "hookEvent": "PostToolUse",
                "content": "Status set to busy: ...",
                "stdout": "...\n",
                "stderr": "",
                "exitCode": 0,
                "command": "${CLAUDE_PLUGIN_ROOT}/scripts/hook.cmd",
                "durationMs": 98,
            },
        )

        entry = create_transcript_entry(raw)
        assert isinstance(entry, AttachmentTranscriptEntry)
        # Anchoring (issue's main point): parentUuid is the right anchor,
        # NOT toolUseID — the example confirmed UserPromptSubmit hooks
        # carry a toolUseID that doesn't match anything in the project.
        assert entry.parentUuid == "792025a9-bf61-4d42-af9f-6fd9ccce4d96"

        msg = create_attachment_message(entry)
        assert isinstance(msg, HookAttachmentMessage)
        assert msg.kind == "success"
        assert msg.hook_event == "PostToolUse"
        assert msg.hook_name == "PostToolUse:TaskUpdate"
        assert msg.tool_use_id == "toolu_018zdqTEqBHAyFpgGNy1wo4S"
        assert msg.exit_code == 0
        assert msg.duration_ms == 98
        assert msg.command == "${CLAUDE_PLUGIN_ROOT}/scripts/hook.cmd"
        assert "Status set to busy" in msg.content
        # The rendered attachment's parent_uuid must mirror the source.
        assert msg.meta.parent_uuid == "792025a9-bf61-4d42-af9f-6fd9ccce4d96"

    def test_additional_context_list_payload(self) -> None:
        """``hook_additional_context`` carries content as a list."""
        raw = _make_attachment(
            uuid="aaaa1111-aaaa-4000-8000-000000000001",
            parent_uuid="bbbb2222-bbbb-4000-8000-000000000001",
            payload={
                "type": "hook_additional_context",
                "hookEvent": "SessionStart",
                "hookName": "SessionStart",
                "toolUseID": "SessionStart",
                "content": [
                    "clmail 6.0.8 session registered",
                    "extra context line",
                ],
            },
        )
        entry = create_transcript_entry(raw)
        msg = create_attachment_message(entry)  # type: ignore[arg-type]
        assert isinstance(msg, HookAttachmentMessage)
        assert msg.kind == "additional_context"
        assert "clmail" in msg.content
        assert "extra context line" in msg.content

    def test_blocking_error_nested_payload(self) -> None:
        """``hook_blocking_error`` nests the message under ``blockingError``."""
        raw = _make_attachment(
            uuid="cccc3333-cccc-4000-8000-000000000001",
            parent_uuid="dddd4444-dddd-4000-8000-000000000001",
            payload={
                "type": "hook_blocking_error",
                "hookName": "PostToolUse:Edit",
                "toolUseID": "toolu_x",
                "hookEvent": "PostToolUse",
                "blockingError": {
                    "blockingError": "ruff format failed",
                    "command": "uv run ruff format",
                },
            },
        )
        entry = create_transcript_entry(raw)
        msg = create_attachment_message(entry)  # type: ignore[arg-type]
        assert isinstance(msg, HookAttachmentMessage)
        assert msg.kind == "blocking_error"
        assert msg.blocking_error == "ruff format failed"
        assert msg.command == "uv run ruff format"

    def test_non_blocking_error_payload(self) -> None:
        raw = _make_attachment(
            uuid="eeee5555-eeee-4000-8000-000000000001",
            parent_uuid="ffff6666-ffff-4000-8000-000000000001",
            payload={
                "type": "hook_non_blocking_error",
                "hookName": "Stop",
                "toolUseID": "tu",
                "hookEvent": "Stop",
                "stderr": "boom",
                "stdout": "",
                "exitCode": 1,
                "command": "do-thing",
                "durationMs": 75,
            },
        )
        entry = create_transcript_entry(raw)
        msg = create_attachment_message(entry)  # type: ignore[arg-type]
        assert isinstance(msg, HookAttachmentMessage)
        assert msg.kind == "non_blocking_error"
        assert msg.exit_code == 1
        assert msg.stderr == "boom"


class TestNonHookAttachments:
    """Non-hook attachment flavours stay structural — factory → None."""

    def test_deferred_tools_delta_returns_none(self) -> None:
        raw = _make_attachment(
            uuid="aaaaaaaa-0000-4000-8000-000000000001",
            parent_uuid="bbbbbbbb-0000-4000-8000-000000000001",
            payload={
                "type": "deferred_tools_delta",
                "addedNames": ["TodoWrite"],
                "addedLines": ["TodoWrite"],
                "removedNames": [],
            },
        )
        entry = create_transcript_entry(raw)
        assert isinstance(entry, AttachmentTranscriptEntry)
        assert create_attachment_message(entry) is None

    def test_queued_command_returns_none(self) -> None:
        raw = _make_attachment(
            uuid="aaaaaaaa-0000-4000-8000-000000000002",
            parent_uuid=None,
            payload={
                "type": "queued_command",
                "prompt": "follow-up",
                "commandMode": "prompt",
            },
        )
        entry = create_transcript_entry(raw)
        assert isinstance(entry, AttachmentTranscriptEntry)
        assert create_attachment_message(entry) is None

    def test_skill_listing_returns_none(self) -> None:
        raw = _make_attachment(
            uuid="aaaaaaaa-0000-4000-8000-000000000003",
            parent_uuid=None,
            payload={
                "type": "skill_listing",
                "content": "...",
                "skillCount": 1,
                "isInitial": True,
            },
        )
        entry = create_transcript_entry(raw)
        assert isinstance(entry, AttachmentTranscriptEntry)
        assert create_attachment_message(entry) is None


# ---------------------------------------------------------------------------
# HTML formatter (unit-level, no full pipeline)


class TestHookAttachmentFormatter:
    def _meta_only(self, **kw: Any) -> HookAttachmentMessage:
        from claude_code_log.models import MessageMeta

        return HookAttachmentMessage(meta=MessageMeta.empty(), **kw)

    def test_format_success_includes_command_and_streams(self) -> None:
        msg = self._meta_only(
            kind="success",
            hook_event="PostToolUse",
            hook_name="PostToolUse:Bash",
            command="ls -la",
            stdout="file.txt\n",
            stderr="",
            exit_code=0,
            duration_ms=42,
        )
        html = format_hook_attachment_content(msg)
        assert "Hook output" in html
        assert "PostToolUse:Bash" in html
        assert "ls -la" in html
        assert "file.txt" in html
        assert "exit 0" in html
        assert "42 ms" in html

    def test_format_blocking_error_uses_alert_label(self) -> None:
        msg = self._meta_only(
            kind="blocking_error",
            hook_event="PostToolUse",
            hook_name="PostToolUse:Edit",
            command="uv run ruff format",
            blocking_error="ruff format failed",
        )
        html = format_hook_attachment_content(msg)
        assert "Hook blocked" in html
        assert "ruff format failed" in html
        # The body deliberately omits an icon — the message header
        # already shows 🪝 / 🚨 via title_HookAttachmentMessage +
        # get_message_emoji, and doubling it inside <summary> reads as
        # visual noise.
        assert "🚨" not in html
        assert "🪝" not in html

    def test_format_additional_context_label(self) -> None:
        msg = self._meta_only(
            kind="additional_context",
            hook_event="UserPromptSubmit",
            hook_name="UserPromptSubmit",
            content="extra context",
        )
        html = format_hook_attachment_content(msg)
        assert "Hook added context" in html
        assert "extra context" in html


# ---------------------------------------------------------------------------
# End-to-end: full pipeline through generate_template_messages


def _make_user(uuid: str, parent_uuid: str | None, text: str) -> dict[str, Any]:
    return {
        **_BASE_FIELDS,
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent_uuid,
        "timestamp": "2026-01-01T00:00:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _hook_messages() -> list[TranscriptEntry]:
    """Tiny fixture: user → user (anchor) → hook attachment.

    The attachment's parentUuid points at the second user entry, which
    is the right anchor per issue #128.
    """
    return [
        create_transcript_entry(_make_user("u-1", None, "first prompt")),
        create_transcript_entry(_make_user("u-2", "u-1", "second prompt")),
        create_transcript_entry(
            _make_attachment(
                uuid="a-1",
                parent_uuid="u-2",
                payload={
                    "type": "hook_success",
                    "hookEvent": "UserPromptSubmit",
                    "hookName": "UserPromptSubmit",
                    "toolUseID": "irrelevant-tool-use",
                    "content": "",
                    "stdout": "registered prompt",
                    "stderr": "",
                    "exitCode": 0,
                    "command": "echo registered",
                    "durationMs": 5,
                },
            )
        ),
    ]


class TestEndToEndDetailLevels:
    def test_full_detail_renders_hook_attachment(self) -> None:
        from claude_code_log.renderer import generate_template_messages

        roots, _nav, ctx = generate_template_messages(
            _hook_messages(), detail=DetailLevel.FULL
        )
        del roots, _nav  # only inspect ctx.messages

        attachments = [
            m for m in ctx.messages if isinstance(m.content, HookAttachmentMessage)
        ]
        assert len(attachments) == 1
        msg = attachments[0]
        content = msg.content
        assert isinstance(content, HookAttachmentMessage)
        # Parent-uuid anchoring: rendered attachment carries the source's
        # parentUuid, not its toolUseID.
        assert msg.meta.parent_uuid == "u-2"
        assert msg.meta.uuid == "a-1"
        assert content.kind == "success"

    def test_full_html_includes_hook_payload(self) -> None:
        renderer = HtmlRenderer()
        renderer.detail = DetailLevel.FULL
        html = renderer.generate(_hook_messages(), "Hook Attachment Smoke")
        # Hook content surfaces in the rendered HTML at full detail.
        assert "registered prompt" in html
        assert "UserPromptSubmit" in html

    def test_high_detail_drops_hook_attachment(self) -> None:
        renderer = HtmlRenderer()
        renderer.detail = DetailLevel.HIGH
        html = renderer.generate(_hook_messages(), "Hook Attachment HIGH")
        # At HIGH, hook attachments are dropped along with other hook noise.
        assert "registered prompt" not in html
        # Sanity: user content still renders.
        assert "first prompt" in html

    def test_low_detail_drops_hook_attachment(self) -> None:
        renderer = HtmlRenderer()
        renderer.detail = DetailLevel.LOW
        html = renderer.generate(_hook_messages(), "Hook Attachment LOW")
        assert "registered prompt" not in html


class TestHookAttachmentsFixture:
    """End-to-end check against ``test/test_data/hook_attachments.jsonl``.

    Exercises the full parser → DAG → renderer pipeline against a
    fixture covering all four hook flavours plus a non-hook attachment
    (deferred_tools_delta) that must remain structural.
    """

    def test_full_detail_renders_each_flavour(self) -> None:
        from pathlib import Path

        from claude_code_log.converter import load_transcript

        jsonl = Path(__file__).parent / "test_data" / "hook_attachments.jsonl"
        messages = load_transcript(jsonl)

        renderer = HtmlRenderer()
        renderer.detail = DetailLevel.FULL
        html = renderer.generate(messages, "Hook Attachments Fixture")

        # Each hook flavour surfaces its key payload.
        assert "prompt registered" in html  # hook_success.stdout
        assert "clmail status" in html  # hook_additional_context.content[0]
        assert "ruff check failed" in html  # hook_blocking_error.blockingError
        assert "Failed with non-blocking status code" in html  # non_blocking.stderr

        # Non-hook attachment (deferred_tools_delta) stays structural —
        # no rendered output for it.
        assert "deferred_tools_delta" not in html

    def test_high_detail_drops_all_hook_flavours(self) -> None:
        from pathlib import Path

        from claude_code_log.converter import load_transcript

        jsonl = Path(__file__).parent / "test_data" / "hook_attachments.jsonl"
        messages = load_transcript(jsonl)

        renderer = HtmlRenderer()
        renderer.detail = DetailLevel.HIGH
        html = renderer.generate(messages, "Hook Attachments HIGH")

        assert "prompt registered" not in html
        assert "clmail status" not in html
        assert "ruff check failed" not in html
        assert "Failed with non-blocking status code" not in html
        # Sanity: real conversation survives.
        assert "Run the test suite" in html
        assert "Running tests" in html
