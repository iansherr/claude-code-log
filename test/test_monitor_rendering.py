"""Test cases for the built-in ``Monitor`` tool rendering (#142).

Covers four concerns:

1. Input/output factory — typed ``MonitorInput`` / ``MonitorOutput``
   are produced from raw tool_use / tool_result entries.
2. HTML rendering — title carries the description; body grid carries
   description / command (collapsible) / timeout_ms / persistent;
   result paragraph renders verbatim.
3. Markdown rendering — title + bullet list + fenced command;
   result text passes through.
4. Task-end backlink — when a ``<task-notification>`` carries
   ``<tool-use-id>`` matching the originating Monitor call's id, the
   rendered Task ID value is wrapped in an anchor pointing at the
   Monitor card's message div.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from claude_code_log.converter import load_transcript
from claude_code_log.factories.tool_factory import (
    parse_monitor_output,
)
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.html.tool_formatters import (
    format_monitor_input,
    format_monitor_output,
)
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    MonitorInput,
    MonitorOutput,
    ToolResultContent,
)


FIXTURE = Path(__file__).parent / "test_data" / "monitor_tool.jsonl"


# -----------------------------------------------------------------------------
# Direct factory tests
# -----------------------------------------------------------------------------


class TestMonitorInputModel:
    def test_required_fields(self) -> None:
        m = MonitorInput(
            description="watch logs",
            command="tail -f /var/log/app.log",
            timeout_ms=60000,
            persistent=True,
        )
        assert m.description == "watch logs"
        assert m.command == "tail -f /var/log/app.log"
        assert m.timeout_ms == 60000
        assert m.persistent is True

    def test_optional_fields_default_none(self) -> None:
        m = MonitorInput(description="d", command="c")
        assert m.timeout_ms is None
        assert m.persistent is None


class TestMonitorOutputParser:
    def test_parses_task_id_from_start_message(self) -> None:
        result = ToolResultContent(
            type="tool_result",
            tool_use_id="x",
            content=(
                "Monitor started (task b07h5t4ng, timeout 3600000ms). "
                "You will be notified on each event."
            ),
        )
        out = parse_monitor_output(result, file_path=None)
        assert isinstance(out, MonitorOutput)
        assert out.task_id == "b07h5t4ng"
        assert "Monitor started" in out.text

    def test_falls_back_to_text_when_format_unknown(self) -> None:
        result = ToolResultContent(
            type="tool_result",
            tool_use_id="x",
            content="Something else entirely.",
        )
        out = parse_monitor_output(result, file_path=None)
        assert out is not None
        assert out.task_id is None
        assert out.text == "Something else entirely."

    def test_empty_returns_none(self) -> None:
        result = ToolResultContent(type="tool_result", tool_use_id="x", content="")
        assert parse_monitor_output(result, file_path=None) is None


# -----------------------------------------------------------------------------
# HTML formatter tests (direct)
# -----------------------------------------------------------------------------


class TestMonitorHtmlFormatter:
    def test_input_grid_includes_all_fields(self) -> None:
        m = MonitorInput(
            description="watch", command="echo hi", timeout_ms=500, persistent=False
        )
        html = format_monitor_input(m)
        # Four labelled rows.
        assert "description" in html
        assert "command" in html
        assert "timeout_ms" in html
        assert "persistent" in html
        # Values present.
        assert "watch" in html
        assert "echo hi" in html
        assert "500" in html
        assert "False" in html

    def test_short_command_uses_pre_inline(self) -> None:
        """Short single-line command renders inside ``<pre class=monitor-command>``,
        no collapsible-code wrapper — chrome wouldn't pay for itself."""
        m = MonitorInput(description="d", command="echo hi")
        html = format_monitor_input(m)
        assert "<pre class='monitor-command'>" in html
        assert "collapsible-code" not in html

    def test_long_command_uses_collapsible_block(self) -> None:
        """Multi-line / long command uses ``render_collapsible_code`` —
        mirrors how other tool inputs render long bodies, with a line
        count badge so the reader knows the size before expanding."""
        long_command = "\n".join(f"line {i}" for i in range(20))
        m = MonitorInput(description="d", command=long_command)
        html = format_monitor_input(m)
        assert "collapsible-code" in html
        assert "20 lines" in html

    def test_optional_fields_omitted_when_none(self) -> None:
        m = MonitorInput(description="d", command="c")
        html = format_monitor_input(m)
        # No timeout_ms or persistent rows when both are None.
        assert "timeout_ms" not in html
        assert "persistent" not in html

    def test_output_paragraph_verbatim(self) -> None:
        out = MonitorOutput(text="Monitor started (task abc).")
        html = format_monitor_output(out)
        assert "<div class='monitor-output'>" in html
        assert "Monitor started (task abc)." in html


# -----------------------------------------------------------------------------
# End-to-end fixture tests
# -----------------------------------------------------------------------------


class TestMonitorFixtureRendering:
    """Drive the real HTML/Markdown renderers against ``test_data/monitor_tool.jsonl``.

    Covers the full pipeline: tool_use → MonitorInput, tool_result →
    MonitorOutput, and the ``<task-notification>`` → backlink wiring.
    """

    @staticmethod
    def _html() -> str:
        msgs = load_transcript(FIXTURE)
        return HtmlRenderer().generate(msgs, "Test")

    @staticmethod
    def _md() -> str:
        msgs = load_transcript(FIXTURE)
        return MarkdownRenderer().generate(msgs, "Test")

    def test_html_title_contains_monitor_icon_and_description(self) -> None:
        html = self._html()
        # Title includes the telescope icon and the description.
        assert "🔭" in html
        assert "PR #140 check transitions" in html

    def test_html_grid_has_four_rows(self) -> None:
        html = self._html()
        # Each labelled row appears once for the Monitor card.
        for label in ("description", "command", "timeout_ms", "persistent"):
            assert label in html
        assert "3600000" in html  # timeout_ms value
        assert "False" in html  # persistent value

    def test_html_command_is_collapsible(self) -> None:
        """The fixture's command is multi-line bash — should land in
        the collapsible-code wrapper with a line-count badge.

        The fixture command has 7 lines (six explicit newlines + one
        terminal line); ``line_count = command.count('\\n') + 1``.
        """
        html = self._html()
        assert "collapsible-code" in html
        assert "7 lines" in html

    def test_html_result_paragraph_present(self) -> None:
        html = self._html()
        assert "Monitor started (task b07h5t4ng" in html
        assert "monitor-output" in html

    def test_html_task_notification_task_id_links_to_monitor(self) -> None:
        """The Task ID value in the notification card is wrapped in an
        anchor pointing at the Monitor tool_use card's message div."""
        html = self._html()

        # Locate the Monitor tool_use's message div id (msg-d-N).
        monitor_div_match = re.search(
            r"<div class='message[^']*tool_use[^']*'[^>]*id='(msg-d-\d+)'",
            html,
        )
        assert monitor_div_match, "Monitor tool_use div not found"
        monitor_anchor_id = monitor_div_match.group(1)

        # Find the Task ID row's anchor — should reference the Monitor's
        # anchor id.
        task_id_link = re.search(
            r"<a class='task-notification-backlink' href='#(msg-d-\d+)'>"
            r"<code>b07h5t4ng</code></a>",
            html,
        )
        assert task_id_link, "Task ID link to Monitor not found"
        assert task_id_link.group(1) == monitor_anchor_id, (
            f"Task ID link points at {task_id_link.group(1)}, "
            f"expected {monitor_anchor_id}"
        )

    def test_markdown_title_and_command_fence(self) -> None:
        md = self._md()
        # Title with telescope and description.
        assert "🔭 Monitor PR #140 check transitions" in md
        # Bullet list with all four fields.
        assert "- **description:** PR #140 check transitions" in md
        assert "- **timeout_ms:** 3600000" in md
        assert "- **persistent:** False" in md
        # Command in a fenced bash block.
        assert "```bash" in md
        assert "deadline" in md or "while true" in md

    def test_markdown_output_text_present(self) -> None:
        md = self._md()
        assert "Monitor started (task b07h5t4ng" in md


# -----------------------------------------------------------------------------
# Standalone backlink tests — TaskNotificationMessage in isolation
# -----------------------------------------------------------------------------


class TestTaskNotificationToolUseIdBacklink:
    """The backlink wiring is independent of the Monitor flow.

    Any TaskNotificationMessage carrying ``tool_use_id`` should have
    its Task ID rendered as an anchor when the matching tool_use is
    present in the same transcript. These tests pin the contract via
    the same fixture so a future refactor of either side stays linked.
    """

    def test_task_notification_carries_tool_use_id_after_parse(self) -> None:
        """Sanity: the factory now extracts ``<tool-use-id>``."""
        from claude_code_log.factories.task_notification_factory import (
            create_task_notification_message,
        )
        from claude_code_log.models import MessageMeta

        meta = MessageMeta(session_id="s", timestamp="2026-05-08T10:00:00Z", uuid="u")
        text = (
            "<task-notification>\n"
            "<task-id>abc123</task-id>\n"
            "<tool-use-id>toolu_xyz</tool-use-id>\n"
            "<status>completed</status>\n"
            "<summary>Monitor stream ended</summary>\n"
            "</task-notification>"
        )
        notification = create_task_notification_message(meta, text)
        assert notification is not None
        assert notification.task_id == "abc123"
        assert notification.tool_use_id == "toolu_xyz"

    def test_legacy_notification_without_tool_use_id_still_parses(self) -> None:
        """Notifications predating the ``<tool-use-id>`` field must
        keep working — ``tool_use_id`` is just None in that case."""
        from claude_code_log.factories.task_notification_factory import (
            create_task_notification_message,
        )
        from claude_code_log.models import MessageMeta

        meta = MessageMeta(session_id="s", timestamp="2026-05-08T10:00:00Z", uuid="u")
        text = (
            "<task-notification>\n"
            "<task-id>abc123</task-id>\n"
            "<status>completed</status>\n"
            "<summary>Old notification</summary>\n"
            "</task-notification>"
        )
        notification = create_task_notification_message(meta, text)
        assert notification is not None
        assert notification.task_id == "abc123"
        assert notification.tool_use_id is None

    def test_no_backlink_when_tool_use_id_does_not_match(self, tmp_path) -> None:
        """When the notification's tool_use_id doesn't match any
        tool_use in the transcript, the Task ID renders as plain
        ``<code>`` — no orphan anchor pointing at nothing."""
        # Build a fixture where the notification references a tool_use_id
        # that doesn't exist.
        import json

        ts = "2026-05-08T10:00:00.000Z"
        lines: list[dict[str, object]] = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": ts,
                "sessionId": "s1",
                "version": "2.1.128",
                "cwd": "/tmp",
                "userType": "external",
                "isSidechain": False,
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "user",
                "uuid": "u2",
                "parentUuid": "u1",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "2.1.128",
                "cwd": "/tmp",
                "userType": "external",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": (
                        "<task-notification>\n"
                        "<task-id>orphan_task</task-id>\n"
                        "<tool-use-id>toolu_does_not_exist</tool-use-id>\n"
                        "<status>completed</status>\n"
                        "<summary>orphan</summary>\n"
                        "</task-notification>"
                    ),
                },
            },
        ]
        fn = tmp_path / "orphan.jsonl"
        fn.write_text("\n".join(json.dumps(line) for line in lines))
        msgs = load_transcript(fn)
        html = HtmlRenderer().generate(msgs, "Test")
        # Task ID row is present but NOT wrapped in a backlink anchor.
        # Check for the rendered usage (``<a class='task-notification-backlink'``)
        # rather than the bare class name — the latter is also defined in
        # the embedded CSS, so it's always present in the document.
        assert "orphan_task" in html
        assert "<a class='task-notification-backlink'" not in html


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixture_present() -> None:
    """The fixture file must exist for the end-to-end tests to run."""
    if not FIXTURE.exists():
        pytest.skip(f"Fixture missing: {FIXTURE}")
