"""Unit tests for MarkdownRenderer helper methods.

Tests for the private utility methods that handle content escaping and formatting.
"""

import pytest

from claude_code_log.markdown.renderer import MarkdownRenderer


@pytest.fixture
def renderer():
    """Create a MarkdownRenderer instance for testing."""
    return MarkdownRenderer()


class TestCodeFence:
    """Tests for the _code_fence() method."""

    def test_simple_text(self, renderer):
        """Basic text gets wrapped in triple backticks."""
        result = renderer._code_fence("hello world")
        assert result == "```\nhello world\n```"

    def test_with_language(self, renderer):
        """Language hint is added after opening fence."""
        result = renderer._code_fence("print('hi')", "python")
        assert result == "```python\nprint('hi')\n```"

    def test_text_with_triple_backticks(self, renderer):
        """Text containing ``` gets wrapped with longer fence."""
        content = "```python\ncode\n```"
        result = renderer._code_fence(content)
        assert result == "````\n```python\ncode\n```\n````"

    def test_text_with_four_backticks(self, renderer):
        """Text containing ```` gets wrapped with even longer fence."""
        content = "````\ncode\n````"
        result = renderer._code_fence(content)
        assert result == "`````\n````\ncode\n````\n`````"

    def test_text_with_inline_backticks(self, renderer):
        """Single or double backticks don't trigger longer fence."""
        content = "use `code` or ``code``"
        result = renderer._code_fence(content)
        # Two backticks is max, so we use 3 (standard)
        assert result == "```\nuse `code` or ``code``\n```"

    def test_text_with_mixed_backticks(self, renderer):
        """Mixed backtick lengths uses fence longer than max."""
        content = "` `` ``` ```` `````"
        result = renderer._code_fence(content)
        # Max is 5, so fence should be 6
        assert result.startswith("``````\n")
        assert result.endswith("\n``````")

    def test_empty_text(self, renderer):
        """Empty text still gets wrapped."""
        result = renderer._code_fence("")
        assert result == "```\n\n```"


class TestEscapeHtmlTag:
    """Tests for the _escape_html_tag() method."""

    def test_escape_details_tag(self, renderer):
        """Escapes </details> to prevent closing outer details block."""
        text = "Some text with </details> in it"
        result = renderer._escape_html_tag(text, "details")
        assert result == "Some text with &lt;/details> in it"

    def test_escape_summary_tag(self, renderer):
        """Escapes </summary> to prevent closing summary block."""
        text = "Click </summary> here"
        result = renderer._escape_html_tag(text, "summary")
        assert result == "Click &lt;/summary> here"

    def test_no_tag_present(self, renderer):
        """Text without the tag remains unchanged."""
        text = "No tags here"
        result = renderer._escape_html_tag(text, "details")
        assert result == "No tags here"

    def test_multiple_occurrences(self, renderer):
        """All occurrences are escaped."""
        text = "</details>foo</details>bar</details>"
        result = renderer._escape_html_tag(text, "details")
        assert result == "&lt;/details>foo&lt;/details>bar&lt;/details>"

    def test_case_sensitive(self, renderer):
        """Tag matching is case-sensitive."""
        text = "</DETAILS> and </details>"
        result = renderer._escape_html_tag(text, "details")
        # Only lowercase is escaped
        assert result == "</DETAILS> and &lt;/details>"


class TestEscapeStars:
    """Tests for the _escape_stars() method."""

    def test_single_asterisk_escaped(self, renderer):
        """Single asterisk gets escaped."""
        text = "a * b = c"
        result = renderer._escape_stars(text)
        assert result == "a \\* b = c"

    def test_multiple_asterisks_escaped(self, renderer):
        """All asterisks get escaped."""
        text = "a * b * c *"
        result = renderer._escape_stars(text)
        assert result == "a \\* b \\* c \\*"

    def test_already_escaped_asterisk(self, renderer):
        """Already escaped \\* becomes \\\\\\*."""
        text = "show \\* files"
        result = renderer._escape_stars(text)
        assert result == "show \\\\\\* files"

    def test_mixed_escaped_and_bare(self, renderer):
        """Mix of escaped and bare asterisks."""
        text = "foo * bar \\* baz"
        result = renderer._escape_stars(text)
        assert result == "foo \\* bar \\\\\\* baz"

    def test_no_asterisks(self, renderer):
        """Text without asterisks is unchanged."""
        text = "no asterisks here"
        result = renderer._escape_stars(text)
        assert result == text

    def test_empty_text(self, renderer):
        """Empty text remains empty."""
        result = renderer._escape_stars("")
        assert result == ""


class TestCollapsible:
    """Tests for the _collapsible() method."""

    def test_basic_collapsible(self, renderer):
        """Basic collapsible block structure."""
        result = renderer._collapsible("Click me", "Hidden content")
        expected = (
            "<details>\n<summary>Click me</summary>\n\nHidden content\n</details>"
        )
        assert result == expected

    def test_escapes_details_in_content(self, renderer):
        """Content with </details> is escaped."""
        result = renderer._collapsible("Title", "Has </details> tag")
        assert "&lt;/details>" in result
        assert "</details> tag" not in result

    def test_escapes_summary_in_summary(self, renderer):
        """Summary with </summary> is escaped."""
        result = renderer._collapsible("Title </summary> here", "Content")
        assert "&lt;/summary>" in result
        assert "</summary> here" not in result

    def test_escapes_details_in_summary(self, renderer):
        """Summary with </details> is also escaped."""
        result = renderer._collapsible("Bad </details> title", "Content")
        assert "&lt;/details>" in result

    def test_preserves_other_html(self, renderer):
        """Other HTML tags are not escaped."""
        result = renderer._collapsible("Title", "Has <code>code</code> here")
        assert "<code>code</code>" in result


class TestQuote:
    """Tests for the _quote() method."""

    def test_single_line(self, renderer):
        """Single line gets prefixed with '> '."""
        result = renderer._quote("hello")
        assert result == "> hello"

    def test_multiline(self, renderer):
        """Each line gets prefixed."""
        result = renderer._quote("line1\nline2\nline3")
        assert result == "> line1\n> line2\n> line3"

    def test_empty_lines(self, renderer):
        """Empty lines still get the prefix."""
        result = renderer._quote("line1\n\nline3")
        assert result == "> line1\n> \n> line3"

    def test_empty_text(self, renderer):
        """Empty string gets single prefix."""
        result = renderer._quote("")
        assert result == "> "


class TestStripErrorTags:
    """Tests for the strip_error_tags() utility function."""

    def test_simple_error(self):
        """Single error tag is stripped, content preserved."""
        from claude_code_log.utils import strip_error_tags

        text = "<tool_use_error>File not found</tool_use_error>"
        result = strip_error_tags(text)
        assert result == "File not found"

    def test_multiline_error(self):
        """Multiline error content is preserved."""
        from claude_code_log.utils import strip_error_tags

        text = (
            "<tool_use_error>String to replace not found.\nString: foo</tool_use_error>"
        )
        result = strip_error_tags(text)
        assert result == "String to replace not found.\nString: foo"

    def test_no_error_tag(self):
        """Text without error tags is unchanged."""
        from claude_code_log.utils import strip_error_tags

        text = "Normal tool output"
        result = strip_error_tags(text)
        assert result == "Normal tool output"

    def test_nested_content(self):
        """Error with complex content is handled."""
        from claude_code_log.utils import strip_error_tags

        text = "<tool_use_error>Error: Code has <angle> brackets</tool_use_error>"
        result = strip_error_tags(text)
        assert result == "Error: Code has <angle> brackets"

    def test_empty_error(self):
        """Empty error tag produces empty string."""
        from claude_code_log.utils import strip_error_tags

        text = "<tool_use_error></tool_use_error>"
        result = strip_error_tags(text)
        assert result == ""


class TestExcerpt:
    """Tests for the _excerpt() method."""

    def test_short_text(self, renderer):
        """Short text is returned as-is."""
        result = renderer._excerpt("Hello world")
        assert result == "Hello world"

    def test_stops_at_question(self, renderer):
        """Stops at question mark followed by space."""
        result = renderer._excerpt("Is this working? I hope so.")
        assert result == "Is this working?"

    def test_stops_at_exclamation(self, renderer):
        """Stops at exclamation mark followed by space."""
        result = renderer._excerpt("Great success! This is a test.")
        assert result == "Great success!"

    def test_ignores_short_sentence(self, renderer):
        """Does not stop at sentence ending if too short (min 12 chars)."""
        result = renderer._excerpt("Hello! This is a test.", max_len=30)
        # "Hello!" is only 6 chars, so continues to next sentence
        assert result == "Hello! This is a test."

    def test_stops_at_period(self, renderer):
        """Stops at period followed by space."""
        result = renderer._excerpt("First sentence. Second sentence.")
        assert result == "First sentence."

    def test_no_stop_at_lone_period(self, renderer):
        """Does not stop at period without space (like in .gitignore)."""
        result = renderer._excerpt("Check the .gitignore file", max_len=30)
        # Should continue past the lone period
        assert ".gitignore" in result

    def test_word_boundary(self, renderer):
        """Truncation continues to end of current word."""
        # "This is a longer sentence..." - max_len 20 hits mid-word in "sentence"
        result = renderer._excerpt("This is a longer sentence that goes on", max_len=20)
        # Should not cut "sentence" mid-word
        assert result == "This is a longer sentence…"

    def test_empty_text(self, renderer):
        """Empty text returns empty string."""
        result = renderer._excerpt("")
        assert result == ""

    def test_multiline_takes_first(self, renderer):
        """Takes first non-empty line."""
        result = renderer._excerpt("\n\nFirst line\nSecond line")
        assert result == "First line"

    def test_ellipsis_when_truncated(self, renderer):
        """Adds ellipsis when truncated."""
        result = renderer._excerpt("A very long text that exceeds limit", max_len=15)
        assert result.endswith("…")
