"""Tests for Read tool rendering (issue #170).

The Read tool's ``tool_result.content`` is ``cat -n`` formatted (each line
prefixed with ``<line_number><TAB>``); the structured ``toolUseResult.file``
field carries the clean content plus accurate ``filePath`` / ``startLine`` /
``numLines`` / ``totalLines`` metadata. The renderer should prefer the
structured payload and fall back to parsing the cat-n text only when
``toolUseResult.file`` is absent (older transcripts).
"""

from pathlib import Path

from claude_code_log.converter import load_transcript
from claude_code_log.factories.tool_factory import (
    PARSERS_WITH_TOOL_USE_RESULT,
    create_tool_output,
    parse_read_output,
)
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.models import ReadOutput, ToolResultContent, UserTranscriptEntry


FIXTURE = Path(__file__).parent / "test_data" / "read_tool_pygments.jsonl"


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


def test_read_registered_for_tool_use_result_signature():
    """Read must be in PARSERS_WITH_TOOL_USE_RESULT so create_tool_output
    passes the structured payload through to the parser."""
    assert "Read" in PARSERS_WITH_TOOL_USE_RESULT


# -----------------------------------------------------------------------------
# Preferred path: structured toolUseResult.file
# -----------------------------------------------------------------------------


class TestStructuredPath:
    """When ``toolUseResult.file`` is present, the parser uses it directly."""

    def test_fixture_uses_structured_payload(self):
        entries = load_transcript(FIXTURE)
        tool_result_entry = entries[1]
        assert isinstance(tool_result_entry, UserTranscriptEntry)
        result_item = tool_result_entry.message.content[0]
        assert isinstance(result_item, ToolResultContent)
        out = create_tool_output(
            "Read",
            result_item,
            file_path="/anything/will/be/overridden.py",
            tool_use_result=tool_result_entry.toolUseResult,
        )
        assert isinstance(out, ReadOutput)
        # All metadata from the structured payload survives.
        assert out.start_line == 775
        assert out.num_lines == 20
        assert out.total_lines == 2953
        assert out.is_truncated is True  # 20 < 2953
        # filePath from the structured payload wins over the argument.
        assert out.file_path.endswith("converter.py")
        # Content has NO ``<num>\t`` prefix — that's the whole point.
        assert "\t" not in out.content.split("\n")[0]
        assert out.content.startswith("    with _dag_warnings_suppressed(silent):")

    def test_structured_minimal_fields(self):
        tr = ToolResultContent(tool_use_id="t1", type="tool_result", content="ignored")
        out = parse_read_output(
            tr,
            file_path="/fallback/path.py",
            tool_use_result={
                "type": "text",
                "file": {
                    "filePath": "/real/path.py",
                    "content": "x = 1\ny = 2\n",
                    "numLines": 2,
                    "startLine": 1,
                    "totalLines": 2,
                },
            },
        )
        assert isinstance(out, ReadOutput)
        assert out.file_path == "/real/path.py"
        assert out.start_line == 1
        assert out.is_truncated is False  # numLines == totalLines

    def test_structured_missing_file_falls_through(self):
        """``toolUseResult`` present but no ``file`` key: fall through to text
        parsing. Without parseable cat-n content the parser returns None."""
        tr = ToolResultContent(
            tool_use_id="t1", type="tool_result", content="just some text"
        )
        out = parse_read_output(tr, file_path="/p.py", tool_use_result={"type": "text"})
        assert out is None


# -----------------------------------------------------------------------------
# Fallback path: cat-n text parsing
# -----------------------------------------------------------------------------


class TestCatNFallback:
    """Older transcripts without ``toolUseResult.file`` fall back to parsing
    the ``cat -n`` text. Two separator variants are accepted: tab (current
    Read result format) and arrow (Edit/Write result snippet format)."""

    def test_tab_separator_parsed(self):
        # Format actually emitted by Read in Claude Code 2.1.x+ (issue #170)
        content = "775\t    with foo:\n776\t        bar()"
        tr = ToolResultContent(tool_use_id="t1", type="tool_result", content=content)
        out = parse_read_output(tr, file_path="/x.py")
        assert isinstance(out, ReadOutput)
        assert out.start_line == 775
        assert out.num_lines == 2
        assert out.content == "    with foo:\n        bar()"

    def test_arrow_separator_still_works(self):
        # Format used by Edit/Write result snippets — must not regress.
        content = "   100→def foo():\n   101→    pass"
        tr = ToolResultContent(tool_use_id="t2", type="tool_result", content=content)
        out = parse_read_output(tr, file_path="/x.py")
        assert isinstance(out, ReadOutput)
        assert out.start_line == 100
        assert out.content == "def foo():\n    pass"

    def test_non_catn_returns_none(self):
        tr = ToolResultContent(
            tool_use_id="t3", type="tool_result", content="not a cat-n line"
        )
        assert parse_read_output(tr, file_path="/x.py") is None

    def test_missing_file_path_returns_none(self):
        tr = ToolResultContent(
            tool_use_id="t4", type="tool_result", content="775\tcode"
        )
        assert parse_read_output(tr, file_path=None) is None


# -----------------------------------------------------------------------------
# Rendering: pygments highlighting + line-number alignment
# -----------------------------------------------------------------------------


class TestHtmlRendering:
    """End-to-end: fixture renders with pygments lexer detection + correct
    starting line number; output is collapsible and carries the file path."""

    def test_fixture_renders_with_python_lexer(self):
        entries = load_transcript(FIXTURE)
        renderer = HtmlRenderer()
        html = renderer.generate(entries, title="Read Tool Fixture")
        # Pygments python lexer fires (function/class keywords highlight).
        assert "highlight" in html
        # Starting line number from startLine=775 appears in the rendered table.
        assert ">775<" in html or "775" in html
        # File path surfaces in the heading / collapsible summary.
        assert "converter.py" in html
        # No raw "<num>\t" prefixes leak into the rendered output — the whole
        # point of using the structured payload.
        assert "775\t" not in html

    def test_title_line_range_matches_rendered_content(self):
        # Regression for the off-by-one surfaced in PR #172 manual testing:
        # the fixture's input is ``offset=775, limit=20`` and the cat-n
        # output renders lines 775–794. The title must agree on those
        # numbers — previously emitted "lines 776-795" (start shifted by
        # +1, end computed as offset+limit instead of offset+limit-1).
        entries = load_transcript(FIXTURE)
        renderer = HtmlRenderer()
        html = renderer.generate(entries, title="Read Tool Fixture")
        assert "lines 775-794" in html
        assert "lines 776-795" not in html  # the old wrong rendering

    def test_unknown_extension_falls_back_to_textlexer(self):
        # Construct a Read output for a file with no extension. Pygments
        # should fall back to TextLexer; rendering must not raise.
        from claude_code_log.html.utils import render_file_content_collapsible

        html = render_file_content_collapsible(
            "line one\nline two\n",
            "/no/extension/file",
            "read-tool-result",
            linenostart=1,
        )
        assert "read-tool-result" in html
        assert "line one" in html

    def test_single_line_read(self):
        tr = ToolResultContent(tool_use_id="s1", type="tool_result", content="ignored")
        out = parse_read_output(
            tr,
            file_path="/x.py",
            tool_use_result={
                "file": {
                    "filePath": "/x.py",
                    "content": "only_one_line = True\n",
                    "numLines": 1,
                    "startLine": 42,
                    "totalLines": 100,
                }
            },
        )
        assert isinstance(out, ReadOutput)
        assert out.start_line == 42
        assert out.is_truncated is True

    def test_empty_file(self):
        # Regression: ``numLines == 0`` and ``totalLines == 0`` must NOT be
        # promoted to the absent-fallback by a ``... or default`` shortcut.
        # The previous draft used ``int(file_info.get("numLines") or len(...))``
        # which silently turned 0 into ``len("".split("\n")) == 1`` —
        # rendering an empty file as 1 line.
        tr = ToolResultContent(tool_use_id="e1", type="tool_result", content="ignored")
        out = parse_read_output(
            tr,
            file_path="/empty.py",
            tool_use_result={
                "file": {
                    "filePath": "/empty.py",
                    "content": "",
                    "numLines": 0,
                    "startLine": 1,
                    "totalLines": 0,
                }
            },
        )
        assert isinstance(out, ReadOutput)
        assert out.content == ""
        assert out.num_lines == 0  # not 1 — see comment above
        assert out.total_lines == 0
        assert out.is_truncated is False  # 0 == 0

    def test_absent_numlines_uses_splitlines(self):
        # Regression: the absent-numLines fallback must use ``splitlines()``,
        # not ``split("\n")``. Content ending in ``\n`` (most file content)
        # would otherwise overcount by one phantom trailing element.
        tr = ToolResultContent(tool_use_id="s2", type="tool_result", content="ignored")
        out = parse_read_output(
            tr,
            file_path="/x.py",
            tool_use_result={
                "file": {
                    "filePath": "/x.py",
                    "content": "x = 1\ny = 2\n",  # 2 real lines, trailing \n
                    # numLines deliberately absent — exercises fallback
                    "startLine": 1,
                }
            },
        )
        assert isinstance(out, ReadOutput)
        assert out.num_lines == 2  # not 3
