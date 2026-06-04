"""Tests for Workflow tool-input + async-result-body rendering (#174 PR2).

D3: a ``Workflow`` tool_use renders a meta header (name/description/phase
pills) above its JavaScript orchestrator, syntax-highlighted — not the raw
ToolUseContent fallback.
D4: a JSON-shaped async-result body is pretty-printed + highlighted as JSON;
non-JSON (markdown) bodies fall back to the existing markdown rendering.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_log.converter import load_transcript
from claude_code_log.html.renderer import generate_html
from claude_code_log.html.utils import render_async_result_body
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.workflow import parse_workflow_meta

TRUNK = (
    Path(__file__).parent
    / "test_data"
    / "workflow_basic"
    / "11110000-0000-4000-8000-000000000001.jsonl"
)


class TestWorkflowToolInputRendering:
    """D3 — Workflow tool_use → meta header + highlighted JS."""

    def _html(self) -> str:
        return generate_html(load_transcript(TRUNK))

    def test_meta_header_from_script_meta_block(self) -> None:
        html = self._html()
        assert "workflow-meta" in html
        assert "workflow-name" in html and "demo-review" in html
        # description surfaced from the script's meta block
        assert "Review changed files across dimensions" in html

    def test_both_phase_pills_rendered(self) -> None:
        html = self._html()
        assert html.count("workflow-phase-pill") >= 2  # Map + Synthesize

    def test_script_highlighted_as_javascript(self) -> None:
        html = self._html()
        # Target the rendered div, not the `.workflow-script` CSS rule in <head>.
        idx = html.find("class='workflow-script'")
        assert idx != -1, "expected a rendered workflow-script block"
        segment = html[idx : idx + 600]
        # render_file_content_collapsible emits a Pygments highlight table
        assert "highlight" in segment

    def test_specialized_path_not_raw_fallback(self) -> None:
        # The generic fallback would dump input as a params table / raw JSON
        # with no meta header or highlighted script; the specialized renderer
        # produces both.
        html = self._html()
        assert "workflow-meta" in html and "workflow-script" in html


class TestAsyncResultBodyJson:
    """D4 — JSON-aware async-result body rendering."""

    def test_json_body_pretty_printed_and_highlighted(self) -> None:
        out = render_async_result_body(
            '{"plan": "Land parsing first.", "areaCount": 2}', "task-async-answer"
        )
        assert "highlight" in out  # Pygments-highlighted
        assert "task-async-answer" in out  # wrapper css class preserved
        assert out.count("\n") > 2  # pretty-printed (indented, multi-line)

    def test_truncated_json_highlights_without_crashing(self) -> None:
        # Real async previews are often truncated mid-value; still starts with
        # {" so it's treated as JSON and highlighted best-effort.
        out = render_async_result_body('{"plan": "Lan', "x")
        assert "highlight" in out

    def test_markdown_body_uses_markdown_path(self) -> None:
        out = render_async_result_body("## Plan\n\nLand it first.", "x")
        # markdown path → no Pygments code-highlight table
        assert 'class="highlight"' not in out
        assert "Plan" in out

    def test_non_json_brace_text_is_not_treated_as_json(self) -> None:
        # Heuristic is specifically `{"` — a lone "{" (e.g. prose) is markdown.
        out = render_async_result_body("{ not really json", "x")
        assert 'class="highlight"' not in out


class TestWorkflowToolInputMarkdown:
    """D3 parity for the Markdown renderer — registering Workflow in
    TOOL_INPUT_MODELS is format-neutral, so the script must NOT vanish from
    Markdown output (regression guard, monk PR2 review)."""

    def _md(self) -> str:
        return MarkdownRenderer().generate(load_transcript(TRUNK))

    def test_script_present_in_markdown(self) -> None:
        md = self._md()
        # The script body (and its meta) must render, not disappear.
        assert "demo-review" in md
        assert "export const meta" in md
        assert "```js" in md  # fenced as JavaScript

    def test_meta_header_in_markdown(self) -> None:
        md = self._md()
        assert "Review changed files across dimensions" in md  # description
        assert "Map" in md and "Synthesize" in md  # phase titles


class TestWorkflowMetaParsing:
    """Unit tests for the shared meta parser (issue #174)."""

    def test_name_description_phases(self) -> None:
        script = (
            "export const meta = {\n"
            "  name: 'demo',\n"
            "  description: 'A demo',\n"
            "  phases: [{ title: 'Map' }, { title: 'Synthesize' }],\n"
            "}\n"
        )
        assert parse_workflow_meta(script) == ("demo", "A demo", ["Map", "Synthesize"])

    def test_bracket_in_phase_detail_does_not_truncate_phases(self) -> None:
        # N1 regression: a ']' inside a phase detail must not cut the list short.
        script = (
            "export const meta = {\n"
            "  phases: [{ title: 'Scan', detail: 'grep [logs]' }, { title: 'Fix' }],\n"
            "}\n"
        )
        assert parse_workflow_meta(script)[2] == ["Scan", "Fix"]

    def test_no_meta_block_returns_empty(self) -> None:
        assert parse_workflow_meta("const x = 1\nawait agent('hi')\n") == ("", "", [])
