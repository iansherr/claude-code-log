"""Tests for the dedicated TaskStop renderer (PR #158 follow-up).

TaskStop was previously rendered as a generic tool block; this module
covers the typed-renderer migration:

- ``parse_taskstop_output`` handles both structured-dict and plain-string
  ``toolUseResult`` shapes, plus the fallback text-from-content path.
- ``format_TaskStopInput`` / ``format_TaskStopOutput`` produce the
  expected card body (badge + message).
- ``title_TaskStopInput`` produces a ``🛑 TaskStop #<id>`` title with
  a backlink to the originating spawn when the link pass has matched
  the id, and a Markdown-side title with plain inline-code id.
- End-to-end against the shared fixture (extends
  ``task_id_linking.jsonl``), checks both the success and not-found
  shapes and verifies the backlink + forward-link interaction with
  the Bash spawn card.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from claude_code_log.converter import load_transcript
from claude_code_log.factories.tool_factory import parse_taskstop_output
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.html.tool_formatters import (
    format_taskstop_input,
    format_taskstop_output,
)
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    TaskStopInput,
    TaskStopOutput,
    ToolResultContent,
)


FIXTURE = Path(__file__).parent / "test_data" / "task_id_linking.jsonl"


# -----------------------------------------------------------------------------
# Parser unit tests
# -----------------------------------------------------------------------------


class TestTaskStopParser:
    """``parse_taskstop_output`` covers three shapes: dict, str, fallback."""

    @staticmethod
    def _result(content: str = "", is_error: bool = False) -> ToolResultContent:
        return ToolResultContent(
            type="tool_result",
            tool_use_id="x",
            content=content,
            is_error=is_error,
        )

    def test_structured_dict_success(self) -> None:
        out = parse_taskstop_output(
            self._result(),
            None,
            tool_use_result={
                "message": "Successfully stopped task: b1bg01 (sleep 120 && echo done)"
            },
        )
        assert isinstance(out, TaskStopOutput)
        assert out.stopped is True
        assert "Successfully stopped task: b1bg01" in out.message

    def test_structured_dict_empty_message(self) -> None:
        """Empty ``message`` in the dict → not-stopped, no false positive."""
        out = parse_taskstop_output(
            self._result(), None, tool_use_result={"message": ""}
        )
        assert isinstance(out, TaskStopOutput)
        assert out.stopped is False
        assert out.message == ""

    def test_plain_string_error(self) -> None:
        """The common ``toolUseResult = "Error: No task found..."`` shape."""
        out = parse_taskstop_output(
            self._result(content="(unused)", is_error=True),
            None,
            tool_use_result="Error: No task found with ID: nope1234",
        )
        assert isinstance(out, TaskStopOutput)
        assert out.stopped is False
        assert "No task found" in out.message

    def test_plain_string_success(self) -> None:
        """Plain-string success — uncommon but support it for symmetry."""
        out = parse_taskstop_output(
            self._result(),
            None,
            tool_use_result="Successfully stopped task: abc",
        )
        assert isinstance(out, TaskStopOutput)
        assert out.stopped is True

    def test_fallback_to_text_content(self) -> None:
        """No ``toolUseResult`` → fall back to the tool_result text."""
        out = parse_taskstop_output(
            self._result(content="Successfully stopped task: xyz"),
            None,
            tool_use_result=None,
        )
        assert isinstance(out, TaskStopOutput)
        assert out.stopped is True
        assert "Successfully stopped task: xyz" in out.message

    def test_fallback_text_with_is_error(self) -> None:
        """Error flag from tool_result wins over text-match in fallback."""
        out = parse_taskstop_output(
            self._result(content="Successfully stopped task: xyz", is_error=True),
            None,
            tool_use_result=None,
        )
        assert isinstance(out, TaskStopOutput)
        # ``is_error=True`` defensively forces ``stopped=False`` in the
        # fallback path even when the text mentions success.
        assert out.stopped is False


# -----------------------------------------------------------------------------
# Formatter unit tests
# -----------------------------------------------------------------------------


class TestTaskStopFormatters:
    def test_input_format_is_empty(self) -> None:
        """``format_taskstop_input`` returns an empty string — id lives
        in the title; the card body has nothing else to show."""
        out = format_taskstop_input(TaskStopInput(task_id="abc"))
        assert out == ""

    def test_output_success_badge(self) -> None:
        html = format_taskstop_output(TaskStopOutput(stopped=True, message="msg"))
        assert "taskstop-ok" in html
        assert "Stopped" in html
        assert "msg" in html

    def test_output_error_badge(self) -> None:
        html = format_taskstop_output(
            TaskStopOutput(stopped=False, message="Error: No task found with ID: nope")
        )
        assert "taskstop-err" in html
        assert "Not stopped" in html
        assert "Error: No task found" in html

    def test_output_html_escapes_message(self) -> None:
        """Defensive: the harness message can contain ``<`` (shell
        redirection); the renderer must escape it (no XSS path)."""
        html = format_taskstop_output(
            TaskStopOutput(stopped=True, message="<script>alert(1)</script>")
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# -----------------------------------------------------------------------------
# End-to-end fixture tests
# -----------------------------------------------------------------------------


@pytest.mark.usefixtures("_ensure_fixture_present_taskstop")
class TestTaskStopFixture:
    """Drive the real renderers against ``task_id_linking.jsonl`` — the
    fixture's tail now exercises both TaskStop shapes (success on
    ``b1bg01``, error on ``nope1234``)."""

    @staticmethod
    def _html() -> str:
        return HtmlRenderer().generate(load_transcript(FIXTURE), "Test")

    @staticmethod
    def _md() -> str:
        return MarkdownRenderer().generate(load_transcript(FIXTURE), "Test")

    @staticmethod
    def _spawn_anchor(html: str, tool_use_id: str) -> str:
        match = re.search(
            r"id='(msg-d-\d+)'>"
            r"(?:(?!</div>).)*?"
            r'title="ID: ' + re.escape(tool_use_id) + r'"',
            html,
            re.DOTALL,
        )
        assert match, f"tool_use div for {tool_use_id} not found"
        return match.group(1)

    def test_taskstop_for_known_bash_id_backlinks_to_spawn(self) -> None:
        """``TaskStop #b1bg01`` backlinks to the Bash spawn — same
        machinery as TaskOutput; TaskStop just shares the consumer
        path (PR #158)."""
        html = self._html()
        bash_anchor = self._spawn_anchor(html, "toolu_154_bash_bg")
        stop_anchor = self._spawn_anchor(html, "toolu_154_stop_bash")
        # Extract only the stop card's region so we match its title's
        # backlink (NOT the spawn's forward-link, which targets the
        # first consumer = TaskOutput, not this TaskStop).
        card_re = re.compile(
            r"id='" + re.escape(stop_anchor) + r"'>(.+?)</div>",
            re.DOTALL,
        )
        card_match = card_re.search(html)
        assert card_match
        backlink_re = re.compile(
            r"<a class='task-id-backlink' href='#(msg-d-\d+)'>"
            r"<code>#b1bg01</code></a>"
        )
        match = backlink_re.search(card_match.group(1))
        assert match, "TaskStop #b1bg01 backlink not found inside stop card"
        assert match.group(1) == bash_anchor

    def test_taskstop_for_unknown_id_has_no_backlink(self) -> None:
        """When the id wasn't minted in the transcript (TaskStop arrives
        for ``nope1234``), the title still renders the id as inline
        code but with no anchor — the link pass found no spawn."""
        html = self._html()
        stop_anchor = self._spawn_anchor(html, "toolu_154_stop_missing")
        card_re = re.compile(
            r"id='" + re.escape(stop_anchor) + r"'>(.+?)</div>",
            re.DOTALL,
        )
        card_match = card_re.search(html)
        assert card_match
        card_html = card_match.group(1)
        # The id should appear inline-code'd in the title…
        assert "<code>#nope1234</code>" in card_html
        # …but never wrapped in any backlink anchor.
        assert "task-id-backlink' href='#" not in card_html or (
            "<a class='task-id-backlink'" not in card_html
        )

    def test_taskstop_success_badge_present_in_html(self) -> None:
        """The success-shape TaskStop renders a ``Stopped`` badge."""
        html = self._html()
        assert "taskstop-badge" in html
        assert "taskstop-ok" in html
        assert "Stopped" in html
        # And the not-found one renders the error badge.
        assert "taskstop-err" in html
        assert "Not stopped" in html

    def test_markdown_titles_carry_taskstop_id(self) -> None:
        """Markdown surfaces both TaskStop titles with inline-code ids
        (plain — no clickable anchor, per the #154 HTML-only convention)."""
        md = self._md()
        assert "🛑 TaskStop `#b1bg01`" in md
        assert "🛑 TaskStop `#nope1234`" in md
        # And the ``Stopped`` / ``Not stopped`` Markdown badges live
        # in the result bodies.
        assert "**Stopped**" in md
        assert "**Not stopped**" in md


# -----------------------------------------------------------------------------
# Module-level fixture
# -----------------------------------------------------------------------------


@pytest.fixture(scope="class")
def _ensure_fixture_present_taskstop() -> None:  # pyright: ignore[reportUnusedFunction]
    """Fail loudly when the shared JSONL fixture is missing (CodeRabbit
    #158 — silent skip masked fixture-deletion regressions).

    Distinct name from the sibling fixture in ``test_task_id_linking``
    so pytest can resolve it per class without ambiguity.
    """
    if not FIXTURE.exists():
        pytest.fail(f"Required fixture missing: {FIXTURE}")
