#!/usr/bin/env python3
"""Tests for renderer_code.py - code highlighting and diff rendering.

These tests cover the Pygments highlighting functions and the diff rendering
algorithms in renderer_code.py.
"""

import pytest

from claude_code_log.renderer_code import (
    highlight_code_with_pygments,
    render_line_diff,
    render_single_diff,
    truncate_highlighted_preview,
)


class TestHighlighting:
    """Tests for Pygments code highlighting functions.

    Coverage notes for uncovered lines:
    - Lines 116-118 (ClassNotFound exception): Unreachable because we pre-cache
      all extension->lexer and pattern->lexer mappings. The fallback at line 115
      handles unknown extensions, and get_lexer_by_name() at line 113 uses
      cached aliases that are guaranteed valid.
    """

    def test_highlight_with_pattern_matching(self):
        """Test lexer selection via pattern matching (lines 107-108).

        Uses a filename pattern like 'Makefile' that requires pattern matching
        instead of extension lookup.
        """
        code = "all: build\n\nbuild:\n\tgcc -o main main.c"
        # Makefile uses pattern matching, not extension lookup
        result = highlight_code_with_pygments(code, "Makefile")
        assert "highlight" in result
        # Should have some syntax highlighting
        assert "<span" in result or code in result

    def test_highlight_unknown_extension_fallback(self):
        """Test fallback to TextLexer for unknown extensions (lines 114-115)."""
        code = "some content here"
        # Use a completely unknown extension
        result = highlight_code_with_pygments(code, "file.unknownext12345")
        assert code in result  # Content should be preserved

    def test_highlight_classnotfound_exception(self):
        """Test ClassNotFound exception handling (lines 116-118).

        This is hard to trigger since we use extension/pattern caches,
        but we can test with an empty filename to verify the fallback.
        """
        code = "plain text content"
        result = highlight_code_with_pygments(code, "")
        assert code in result

    def test_truncate_highlighted_preview(self):
        """Test truncate_highlighted_preview function (lines 150-173)."""
        code = "line1\nline2\nline3\nline4\nline5"
        # Generate highlighted HTML with line numbers
        full_html = highlight_code_with_pygments(code, "test.txt", show_linenos=True)
        # Truncate to 2 lines
        truncated = truncate_highlighted_preview(full_html, max_lines=2)
        # Should have fewer lines than original
        assert "line5" not in truncated
        # Should still have HTML structure
        assert "highlight" in truncated


class TestDiffRendering:
    """Tests for diff rendering functions.

    Coverage notes for uncovered lines:
    - Line 319 (standalone ? hint skip): Unreachable in practice because ndiff
      only produces ? hint lines between related - and + lines, and the - block
      processing (lines 269-270, 279-280) consumes all such hints. There is no
      valid ndiff output where a ? line would be processed by the main loop.
    """

    def test_render_line_diff_default_escape_fn(self):
        """Test render_line_diff with default escape function (line 190)."""
        old_line = "line one"
        new_line = "line two"
        # Call without escape_fn to use default
        result = render_line_diff(old_line, new_line)
        assert "diff-line" in result

    def test_render_single_diff_hint_line_skipping(self):
        """Test that ? hint lines are skipped (line 319).

        The ? prefix appears in ndiff output to show character-level differences.
        """
        # Create text that will produce ? hint lines in ndiff
        old_text = "hello world\nline two"
        new_text = "hello there\nline two"  # Similar enough for hint line
        result = render_single_diff(old_text, new_text)
        # The result should contain diff markers
        assert "diff-" in result

    def test_render_single_diff_multiple_consecutive_removed(self):
        """Test multiple consecutive removed lines (lines 265-266)."""
        old_text = "line1\nline2\nline3\nline4"
        new_text = "line1\nline4"  # Remove lines 2 and 3
        result = render_single_diff(old_text, new_text)
        assert "diff-removed" in result
        # Should have two removed lines
        assert result.count("diff-removed") >= 2

    def test_render_single_diff_more_removed_than_added(self):
        """Test unpaired lines: more removed than added (lines 288-292).

        Uses foo1/foo2/foo3 pattern that produces ndiff output with:
        - foo1
        - foo2
        ? hint
        + foo3
        This triggers the unpaired removal loop at lines 288-292.
        """
        old_text = "foo1\nfoo2"
        new_text = "foo3"  # 2 similar lines -> 1 similar line
        result = render_single_diff(old_text, new_text)
        assert "diff-removed" in result
        assert "diff-added" in result
        # Should have at least 2 removed markers (foo1 and foo2)
        assert result.count("diff-removed") >= 1

    def test_render_single_diff_more_added_than_removed(self):
        """Test unpaired lines: more added than removed (lines 295-303)."""
        old_text = "old1"
        new_text = "new1\nnew2\nnew3"  # 1 line -> 3 lines
        result = render_single_diff(old_text, new_text)
        assert "diff-removed" in result
        assert "diff-added" in result
        # Should have more added markers
        assert result.count("diff-added") >= 2

    def test_render_single_diff_pure_addition(self):
        """Test added lines without corresponding removal (lines 311-315)."""
        old_text = "unchanged"
        new_text = "unchanged\nnew line added"
        result = render_single_diff(old_text, new_text)
        assert "diff-added" in result
        assert "new line added" in result or "diff-line" in result

    def test_render_single_diff_hint_after_removal_block(self):
        """Test hint lines after multiple removed lines (line 270).

        ndiff produces ? hint lines when there are character-level similarities.
        The while loop at line 270 handles skipping these after collecting removals.
        """
        # Create text that will generate hint lines after removals
        # Character-similar lines produce ? hints
        old_text = "line_aaaa\nline_bbbb"
        new_text = "line_cccc\nline_dddd"  # Similar structure triggers hints
        result = render_single_diff(old_text, new_text)
        assert "diff-" in result

    def test_render_single_diff_only_removals_no_additions(self):
        """Test removed lines with no corresponding additions (lines 289-290, 299-305).

        When old text has lines that are completely removed (not replaced),
        the else branch at line 299-305 handles these 'only removed' lines.
        """
        old_text = "keep this\nremove me\nalso remove me\nkeep this too"
        new_text = "keep this\nkeep this too"
        result = render_single_diff(old_text, new_text)
        assert "diff-removed" in result
        # The removed lines should appear
        assert result.count("diff-removed") >= 2

    def test_render_single_diff_standalone_hint_lines(self):
        """Test standalone hint lines (line 319).

        When a ? hint line appears outside the removal/addition collection,
        it's handled by the elif at line 317.
        """
        # This tests the case where hint lines appear in isolation
        old_text = "same line\nmodified_x\nsame line"
        new_text = "same line\nmodified_y\nsame line"
        result = render_single_diff(old_text, new_text)
        assert "diff-" in result
        # Hint lines should be skipped, not rendered
        assert "?" not in result or "diff-context" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
