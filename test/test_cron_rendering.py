"""Test cases for ScheduleWakeup + Cron* tool rendering (#148).

Five concerns:

1. Input/output factories — typed models from raw tool_use /
   tool_result entries; output parsers extract the structured
   fields when the format matches and fall back to raw text
   otherwise.
2. HTML rendering — title carries the right summary per tool;
   body grids, collapsible prompts, and structured cron-list
   tables match the spec.
3. Markdown rendering — title format mirrors the HTML title's
   summary; body uses fenced prompts via the adaptive
   ``_code_fence`` helper.
4. End-to-end fixture — drives the full pipeline against a
   single JSONL with one call per tool in the family.
5. CronList output parser robustness — the harness's exact
   format isn't guaranteed, so the parser must fall back
   gracefully when the row regex doesn't match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_log.converter import load_transcript
from claude_code_log.factories.tool_factory import (
    parse_croncreate_output,
    parse_crondelete_output,
    parse_cronlist_output,
    parse_schedulewakeup_output,
)
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.html.tool_formatters import (
    format_croncreate_input,
    format_cronlist_output,
    format_schedulewakeup_input,
)
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    CronCreateInput,
    CronCreateOutput,
    CronDeleteInput,
    CronDeleteOutput,
    CronListInput,
    CronListItem,
    CronListOutput,
    ScheduleWakeupInput,
    ScheduleWakeupOutput,
    ToolResultContent,
)


FIXTURE = Path(__file__).parent / "test_data" / "cron_tools.jsonl"


# -----------------------------------------------------------------------------
# Input model tests
# -----------------------------------------------------------------------------


class TestSchedulingInputModels:
    def test_schedulewakeup_required_fields(self) -> None:
        m = ScheduleWakeupInput(
            delaySeconds=60, reason="check build", prompt="/loop foo"
        )
        assert m.delaySeconds == 60
        assert m.reason == "check build"
        assert m.prompt == "/loop foo"

    def test_croncreate_optional_flags_default_none(self) -> None:
        m = CronCreateInput(cron="0 9 * * *", prompt="/morning")
        assert m.recurring is None
        assert m.durable is None

    def test_cronlist_takes_no_inputs(self) -> None:
        # Tolerates the empty input dict the harness sends.
        m = CronListInput()
        assert m is not None

    def test_crondelete_requires_id(self) -> None:
        m = CronDeleteInput(id="cj_abc")
        assert m.id == "cj_abc"


# -----------------------------------------------------------------------------
# Output parser tests
# -----------------------------------------------------------------------------


def _result(text: str) -> ToolResultContent:
    return ToolResultContent(type="tool_result", tool_use_id="x", content=text)


class TestSchedulingOutputParsers:
    def test_schedulewakeup_parses_clock_and_delay(self) -> None:
        out = parse_schedulewakeup_output(
            _result("Next wakeup scheduled for 10:04:00 (in 240s)."), None
        )
        assert isinstance(out, ScheduleWakeupOutput)
        assert out.next_at == "10:04:00"
        assert out.in_seconds == 240
        assert "Next wakeup scheduled" in out.text

    def test_schedulewakeup_falls_back_to_text(self) -> None:
        out = parse_schedulewakeup_output(_result("Some other message."), None)
        assert out is not None
        assert out.next_at is None
        assert out.in_seconds is None
        assert out.text == "Some other message."

    def test_schedulewakeup_empty_returns_none(self) -> None:
        assert parse_schedulewakeup_output(_result(""), None) is None

    def test_croncreate_extracts_job_id_recurring(self) -> None:
        # Real harness output captured during the live #148 experiment.
        out = parse_croncreate_output(
            _result(
                "Scheduled recurring job 337e67de (Every 2 minutes). "
                "Session-only (not written to disk, dies when Claude exits). "
                "Auto-expires after 7 days. Use CronDelete to cancel sooner."
            ),
            None,
        )
        assert isinstance(out, CronCreateOutput)
        assert out.job_id == "337e67de"

    def test_croncreate_extracts_job_id_oneshot(self) -> None:
        out = parse_croncreate_output(
            _result("Scheduled one-shot job abc-123 (at 14:30 today)."), None
        )
        assert isinstance(out, CronCreateOutput)
        assert out.job_id == "abc-123"

    def test_croncreate_falls_back_when_format_unknown(self) -> None:
        out = parse_croncreate_output(_result("OK."), None)
        assert out is not None
        assert out.job_id is None
        assert out.text == "OK."

    def test_cronlist_parses_real_format(self) -> None:
        # Real harness output captured during the live experiment.
        text = (
            "337e67de — Every 2 minutes (recurring) [session-only]: "
            "Cron tick — fixture-generation experiment for issue #148."
        )
        out = parse_cronlist_output(_result(text), None)
        assert isinstance(out, CronListOutput)
        assert len(out.jobs) == 1
        job = out.jobs[0]
        assert job.id == "337e67de"
        assert job.description == "Every 2 minutes"
        assert job.prompt.startswith("Cron tick")
        assert job.recurring is True
        assert job.durable is None

    def test_cronlist_durable_scope_sets_durable_flag(self) -> None:
        text = "abc — Daily at 9am (recurring) [durable]: /morning-checkin"
        out = parse_cronlist_output(_result(text), None)
        assert out is not None
        assert len(out.jobs) == 1
        assert out.jobs[0].durable is True

    def test_cronlist_oneshot_kind_unsets_recurring(self) -> None:
        # One-shot jobs render as kind=one-shot, recurring=None.
        text = "xyz — at 14:30 (one-shot) [session-only]: /reminder"
        out = parse_cronlist_output(_result(text), None)
        assert out is not None
        assert len(out.jobs) == 1
        assert out.jobs[0].recurring is None

    def test_cronlist_falls_back_on_unrecognised_format(self) -> None:
        out = parse_cronlist_output(
            _result("Just a free-form summary, no rows here."), None
        )
        assert out is not None
        assert out.jobs == []
        assert "free-form summary" in out.text

    def test_crondelete_captures_text(self) -> None:
        # Real harness format captured during the live experiment.
        out = parse_crondelete_output(_result("Cancelled job 337e67de."), None)
        assert isinstance(out, CronDeleteOutput)
        assert "Cancelled" in out.text


# -----------------------------------------------------------------------------
# HTML formatter unit tests
# -----------------------------------------------------------------------------


class TestSchedulingHtmlFormatters:
    def test_schedulewakeup_input_renders_only_prompt(self) -> None:
        """Body is the collapsible prompt; ``delaySeconds`` and
        ``reason`` already live in the title and aren't repeated here.
        """
        m = ScheduleWakeupInput(
            delaySeconds=300, reason="watch deploy", prompt="/loop bar"
        )
        html = format_schedulewakeup_input(m)
        assert "/loop bar" in html
        # Scalar fields don't appear in the body — no labels, no values.
        assert "delaySeconds" not in html
        assert "300" not in html
        assert "reason" not in html
        assert "watch deploy" not in html

    def test_schedulewakeup_long_prompt_collapses(self) -> None:
        prompt = "\n".join(f"line {i}" for i in range(20))
        m = ScheduleWakeupInput(delaySeconds=60, reason="r", prompt=prompt)
        html = format_schedulewakeup_input(m)
        assert "collapsible-code" in html
        assert "20 lines" in html

    def test_croncreate_input_renders_only_prompt(self) -> None:
        """Body is the collapsible prompt; ``cron`` is in the title
        and the harness echoes back recurring/durable in human form.
        """
        m = CronCreateInput(
            cron="0 * * * *", prompt="/hourly", recurring=True, durable=True
        )
        html = format_croncreate_input(m)
        assert "/hourly" in html
        # Cron expression and flag scalars don't appear in the body.
        assert "0 * * * *" not in html
        assert "recurring" not in html
        assert "durable" not in html

    def test_cronlist_structured_jobs_render_as_table(self) -> None:
        out = CronListOutput(
            text="raw",
            jobs=[
                CronListItem(id="cj_a", description="Hourly", prompt="/a"),
                CronListItem(id="cj_b", description="Every 5 minutes", prompt="/b"),
            ],
        )
        html = format_cronlist_output(out)
        assert "<table class='cronlist-output-table'>" in html
        assert "cj_a" in html
        assert "Hourly" in html
        assert "Every 5 minutes" in html
        assert "/b" in html
        # Header reflects the new field name.
        assert "<th>schedule</th>" in html

    def test_cronlist_falls_back_to_raw_text_when_no_jobs(self) -> None:
        out = CronListOutput(text="No jobs scheduled.", jobs=[])
        html = format_cronlist_output(out)
        # Plain <pre> with the raw text, no table chrome.
        assert "<pre class='cronlist-output'>" in html
        assert "No jobs scheduled." in html
        assert "<table" not in html


# -----------------------------------------------------------------------------
# End-to-end fixture tests
# -----------------------------------------------------------------------------


@pytest.mark.usefixtures("_ensure_fixture_present")
class TestSchedulingFixtureRendering:
    """Drive the real renderers against ``test_data/cron_tools.jsonl``.

    The fixture has one call per tool in the family
    (ScheduleWakeup → CronCreate → CronList → CronDelete) so a single
    render exercises all four paths.
    """

    @staticmethod
    def _html() -> str:
        return HtmlRenderer().generate(load_transcript(FIXTURE), "Test")

    @staticmethod
    def _md() -> str:
        return MarkdownRenderer().generate(load_transcript(FIXTURE), "Test")

    def test_html_titles_present_for_all_four_tools(self) -> None:
        html = self._html()
        # Alarm-clock icon for the family + tool-specific summary.
        assert "⏰" in html
        # ScheduleWakeup title carries the +<delay>s shape.
        assert "+240s" in html
        # CronCreate title carries the cron expression.
        assert "*/2 * * * *" in html
        # CronList renders the static title literal — pinned to a
        # single occurrence so a regression of monk's #148 finding
        # (``_tool_title`` rendering both the tool name and a
        # tool-name-shaped summary) fails loudly.
        assert html.count("CronList") == 1, (
            f"Expected exactly one 'CronList' occurrence; got {html.count('CronList')}"
        )
        assert "<span class='tool-summary'>CronList" not in html
        # CronDelete title carries the id.
        assert "337e67de" in html

    def test_html_no_redundant_wrench_prefix_on_scheduling_titles(self) -> None:
        """Tool-use titles starting with ⏰ must NOT have the template's
        default 🛠️ prepended (regression for the ``starts_with_emoji``
        gap that missed the Misc Technical Unicode block).
        """
        html = self._html()
        # No ``🛠️ ⏰`` co-occurrence anywhere.
        assert "🛠️ ⏰" not in html, (
            "Found redundant 🛠️ prefix on a ⏰-prefixed title — "
            "starts_with_emoji needs to recognise the Misc Technical block."
        )

    def test_html_schedulewakeup_body_is_just_the_prompt(self) -> None:
        """Body shouldn't repeat ``delaySeconds`` / ``reason`` (already in
        the title). The prompt is the only content; result paragraph
        renders verbatim below.
        """
        html = self._html()
        # No labelled rows for the redundant scalars.
        assert ">delaySeconds<" not in html
        assert ">reason<" not in html
        # Prompt body present (collapsible-code wrapper or inline pre).
        assert "/loop Tick the experiment-supervision loop" in html
        # Result paragraph renders verbatim.
        assert "Next wakeup scheduled for 10:04:00" in html

    def test_html_cronlist_renders_structured_table(self) -> None:
        html = self._html()
        # Real-format job surfaced in the rendered table.
        assert "337e67de" in html
        # Human-readable schedule (not the cron expression — the
        # harness's CronList output uses the description form).
        assert "Every 2 minutes" in html

    def test_html_crondelete_result_paragraph(self) -> None:
        html = self._html()
        assert "Cancelled job 337e67de" in html

    def test_markdown_titles_use_inline_code_for_values(self) -> None:
        md = self._md()
        # Reason wrapped in inline code (markdown escape via _inline_code).
        assert (
            "⏰ ScheduleWakeup +240s — `First parent loop tick at +4min — by then alice should have committed iter 1.`"
            in md
        )
        # Cron expression wrapped in inline code.
        assert "⏰ CronCreate `*/2 * * * *`" in md
        # CronDelete id wrapped in inline code.
        assert "⏰ CronDelete `337e67de`" in md

    def test_markdown_schedulewakeup_body_is_just_fenced_prompt(self) -> None:
        md = self._md()
        # No bullet rows for the redundant scalars.
        assert "**delaySeconds:**" not in md
        assert "**reason:**" not in md
        # Prompt content present inside a fenced block.
        assert "/loop Tick the experiment-supervision loop" in md
        assert "```" in md


# -----------------------------------------------------------------------------
# Module-level fixture (skip end-to-end tests when JSONL is missing)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="class")
def _ensure_fixture_present() -> None:  # pyright: ignore[reportUnusedFunction]
    """Skip the end-to-end fixture-driven tests when the JSONL fixture
    is missing. Class-scoped + opt-in via ``@pytest.mark.usefixtures``
    so model / parser / formatter unit tests still run when the fixture
    file is absent.
    """
    if not FIXTURE.exists():
        pytest.skip(f"Fixture missing: {FIXTURE}")
