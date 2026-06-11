"""Workflow side-channel user prompts: collapsible Markdown with embedded
JSON blocks extracted into params tables (#174 follow-up).

The prompts that drive workflow sub-agents are large prose+JSON hybrids.
``extract_embedded_json`` pulls out pretty-printed JSON blocks (a lone
``{``/``[`` line through a lone matching closer followed by a blank line),
substituting z-prefixed UUID placeholders that survive Markdown rendering;
``format_workflow_sidechannel_user_text`` renders the remainder as escaping
collapsible Markdown and swaps each placeholder for a params-table rendering.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_log.html.user_formatters import (
    extract_embedded_json,
    format_workflow_sidechannel_user_text,
)

TRUNK = (
    Path(__file__).parent
    / "test_data"
    / "workflow_basic"
    / "11110000-0000-4000-8000-000000000001.jsonl"
)

_OBJECT_BLOCK = '{\n  "id": "x",\n  "touches": ["a.py"]\n}'
_ARRAY_BLOCK = '[\n  {"area": "loader"},\n  {"area": "tree"}\n]'


class TestExtractEmbeddedJson:
    def test_object_block_extracted(self) -> None:
        text = f"Intro prose.\n\n{_OBJECT_BLOCK}\n\nOutro."
        substituted, blocks = extract_embedded_json(text)
        assert len(blocks) == 1
        placeholder, parsed = next(iter(blocks.items()))
        assert parsed == {"id": "x", "touches": ["a.py"]}
        assert placeholder in substituted
        assert '"touches"' not in substituted

    def test_array_block_extracted(self) -> None:
        substituted, blocks = extract_embedded_json(f"Head:\n\n{_ARRAY_BLOCK}\n\nTail.")
        assert list(blocks.values()) == [[{"area": "loader"}, {"area": "tree"}]]
        assert "loader" not in substituted

    def test_no_blank_line_before_opener_still_matches(self) -> None:
        # The opener needs no preceding blank line — only the closer needs a
        # following one.
        text = f"INPUT (maps):\n{_ARRAY_BLOCK}\n\nDone."
        _substituted, blocks = extract_embedded_json(text)
        assert len(blocks) == 1

    def test_block_at_eof_matches(self) -> None:
        # EOF counts as the blank line after the closer.
        _substituted, blocks = extract_embedded_json(f"Intro.\n\n{_OBJECT_BLOCK}")
        assert len(blocks) == 1

    def test_multiple_blocks(self) -> None:
        text = f"A:\n\n{_OBJECT_BLOCK}\n\nB:\n\n{_ARRAY_BLOCK}\n\nC."
        substituted, blocks = extract_embedded_json(text)
        assert len(blocks) == 2
        for placeholder in blocks:
            assert placeholder in substituted

    def test_invalid_json_left_untouched(self) -> None:
        bad = "{\nnot json at all\n}"
        substituted, blocks = extract_embedded_json(f"X.\n\n{bad}\n\nY.")
        assert blocks == {}
        assert "not json at all" in substituted

    def test_blank_line_inside_block_breaks_the_candidate(self) -> None:
        # The scan stops at the first blank line; the truncated candidate
        # fails to parse and the text stays untouched.
        gappy = '{\n  "a": 1,\n\n  "b": 2\n}'
        substituted, blocks = extract_embedded_json(f"X.\n\n{gappy}\n\nY.")
        assert blocks == {}
        assert '"b": 2' in substituted

    def test_closer_not_alone_left_untouched(self) -> None:
        inline_close = '{\n  "a": 1 }'
        _substituted, blocks = extract_embedded_json(f"X.\n\n{inline_close}\n\nY.")
        assert blocks == {}

    def test_prose_brace_paragraph_left_untouched(self) -> None:
        # A lone `{` opening a prose paragraph (no matching lone closer
        # before the next blank line) must not be eaten.
        text = "X.\n\n{\nthis is prose, not JSON\n\nY."
        substituted, blocks = extract_embedded_json(text)
        assert blocks == {}
        assert "this is prose" in substituted

    def test_placeholder_has_no_bare_hex_run(self) -> None:
        # Every uuid group is z-prefixed so the SHA→commit-URL linkifier
        # (\b[0-9a-f]{7,40}\b) can never match inside a placeholder.
        import re

        substituted, blocks = extract_embedded_json(f"P.\n\n{_OBJECT_BLOCK}\n\nQ.")
        placeholder = next(iter(blocks))
        assert re.search(r"\b[0-9a-f]{7,40}\b", placeholder) is None
        assert placeholder in substituted


class TestSidechannelUserRendering:
    def test_json_block_renders_as_params_table(self) -> None:
        html = format_workflow_sidechannel_user_text(
            f"Check this:\n\n{_OBJECT_BLOCK}\n\nThanks."
        )
        assert "tool-params-table" in html
        assert "embedded-json" in html
        # Raw JSON text is gone; values appear in table cells.
        assert '"touches"' not in html
        assert "a.py" in html

    def test_long_prompt_is_collapsible(self) -> None:
        filler = "\n".join(f"Line {i} of prose." for i in range(20))
        html = format_workflow_sidechannel_user_text(
            f"{filler}\n\n{_OBJECT_BLOCK}\n\nEnd."
        )
        assert "<details" in html
        assert "workflow-sidechannel-user" in html

    def test_placeholder_in_preview_becomes_hint(self) -> None:
        # Block within the first preview lines of a long prompt: the summary
        # shows the compact hint; the table renders once, in the body.
        filler = "\n".join(f"Line {i}." for i in range(20))
        html = format_workflow_sidechannel_user_text(
            f"Top:\n\n{_OBJECT_BLOCK}\n\n{filler}"
        )
        head, tail = html.split("</summary>", 1)
        assert "embedded-json-hint" in head
        assert "tool-params-table" not in head
        assert "tool-params-table" in tail

    def test_markdown_prose_still_renders(self) -> None:
        html = format_workflow_sidechannel_user_text(
            f"You are an **ADVERSARIAL** verifier.\n\n{_OBJECT_BLOCK}\n\nGo."
        )
        assert "<strong>ADVERSARIAL</strong>" in html

    def test_breadth_cap_boundary(self) -> None:
        # Generation-side discipline (CodeRabbit, PR #216): at the cap the
        # block tabulates; past it, an escaped JSON fold — no one-<tr>-per-
        # element generation for huge embedded arrays.
        import json as _json

        def prompt(n: int) -> str:
            block = _json.dumps(list(range(n)), indent=2)
            return f"Data:\n\n{block}\n\nEnd."

        at_cap = format_workflow_sidechannel_user_text(prompt(200))
        assert "tool-params-table" in at_cap

        past_cap = format_workflow_sidechannel_user_text(prompt(201))
        assert "tool-params-table" not in past_cap
        assert "201 items (JSON)" in past_cap
        assert "embedded-json" in past_cap

    def test_user_content_stays_escaped(self) -> None:
        html = format_workflow_sidechannel_user_text(
            'Try <script>alert(1)</script>:\n\n{\n  "x": "<img src=y onerror=z>"\n}\n\nEnd.'
        )
        assert "<script>" not in html
        assert "<img" not in html


class TestWorkflowFixtureIntegration:
    def test_sidechannel_prompt_renders_table_in_directory_load(self) -> None:
        from claude_code_log.converter import load_directory_transcripts
        from claude_code_log.html.renderer import generate_html

        msgs, tree = load_directory_transcripts(TRUNK.parent, silent=True)
        html = generate_html(msgs, session_tree=tree)
        # The fixture's first agent prompt embeds a JSON opportunity block.
        assert "workflow-sidechannel-user" in html
        assert "loader-glob" in html  # value surfaced in the table
        i = html.find("<div class='embedded-json'>")
        assert i != -1
        assert "tool-params-table" in html[i : i + 200]

    def test_trunk_user_messages_unaffected(self) -> None:
        from claude_code_log.converter import load_transcript
        from claude_code_log.html.renderer import generate_html
        from claude_code_log.renderer import generate_template_messages

        # Single-file load of the trunk only: no runs linked → no splice →
        # no tagged nodes → the standard user rendering path.
        entries = load_transcript(TRUNK)
        _roots, _nav, ctx = generate_template_messages(entries)
        assert all(
            not tm.in_workflow_sidechannel for tm in ctx.messages if tm is not None
        )
        html = generate_html(entries)
        assert "workflow-sidechannel-user" not in html
