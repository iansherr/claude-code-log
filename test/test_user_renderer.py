"""Tests for user message parsing and rendering.

Split into:
- Parsing tests: test parse_compacted_summary(), parse_user_memory()
- Content model tests: test parse_user_message_content()
- HTML rendering tests: test full pipeline from JSONL to HTML
"""

import json
import tempfile
from pathlib import Path

import pytest

from claude_code_log.converter import load_transcript
from claude_code_log.html.renderer import generate_html
from claude_code_log.html.user_formatters import (
    format_compacted_summary_content,
    format_user_memory_content,
    format_user_text_model_content,
)
from claude_code_log.models import (
    CompactedSummaryContent,
    TextContent,
    UserMemoryContent,
    UserTextContent,
)
from claude_code_log.parser import (
    COMPACTED_SUMMARY_PREFIX,
    parse_compacted_summary,
    parse_user_memory,
    parse_user_message_content,
)


# =============================================================================
# Parsing Tests - parse_compacted_summary()
# =============================================================================


class TestParseCompactedSummary:
    """Tests for parse_compacted_summary() parser function (takes content list)."""

    def test_parse_compacted_summary_detected(self):
        """Test that compacted summary is detected and content combined."""
        text = (
            f"{COMPACTED_SUMMARY_PREFIX}. The conversation is summarized below:\n"
            "Summary content here."
        )
        content_list = [TextContent(type="text", text=text)]

        result = parse_compacted_summary(content_list)

        assert result is not None
        assert isinstance(result, CompactedSummaryContent)
        assert result.summary_text == text

    def test_parse_compacted_summary_not_detected(self):
        """Test that regular text is not detected as compacted summary."""
        text = "This is a regular user message."
        content_list = [TextContent(type="text", text=text)]

        result = parse_compacted_summary(content_list)

        assert result is None

    def test_parse_compacted_summary_empty_list(self):
        """Test that empty content list returns None."""
        result = parse_compacted_summary([])
        assert result is None

    def test_parse_compacted_summary_combines_multiple_texts(self):
        """Test that multiple text items are combined with double newlines."""
        first_text = f"{COMPACTED_SUMMARY_PREFIX}. Part 1."
        second_text = "Part 2."
        third_text = "Part 3."
        content_list = [
            TextContent(type="text", text=first_text),
            TextContent(type="text", text=second_text),
            TextContent(type="text", text=third_text),
        ]

        result = parse_compacted_summary(content_list)

        assert result is not None
        expected = "\n\n".join([first_text, second_text, third_text])
        assert result.summary_text == expected


# =============================================================================
# Parsing Tests - parse_user_memory()
# =============================================================================


class TestParseUserMemory:
    """Tests for parse_user_memory() parser function."""

    def test_parse_user_memory_detected(self):
        """Test that user memory input tag is detected correctly."""
        text = "<user-memory-input>Memory content from CLAUDE.md</user-memory-input>"

        result = parse_user_memory(text)

        assert result is not None
        assert isinstance(result, UserMemoryContent)
        assert result.memory_text == "Memory content from CLAUDE.md"

    def test_parse_user_memory_with_surrounding_text(self):
        """Test memory tag extraction from mixed content."""
        text = "Some prefix <user-memory-input>The actual memory</user-memory-input> suffix"

        result = parse_user_memory(text)

        assert result is not None
        assert result.memory_text == "The actual memory"

    def test_parse_user_memory_multiline(self):
        """Test multiline memory content."""
        memory_content = "Line 1\nLine 2\nLine 3"
        text = f"<user-memory-input>{memory_content}</user-memory-input>"

        result = parse_user_memory(text)

        assert result is not None
        assert result.memory_text == memory_content

    def test_parse_user_memory_not_detected(self):
        """Test that regular text without tag returns None."""
        text = "Regular text without memory tag."

        result = parse_user_memory(text)

        assert result is None

    def test_parse_user_memory_strips_whitespace(self):
        """Test that memory content whitespace is stripped."""
        text = "<user-memory-input>  \n  Content with spaces  \n  </user-memory-input>"

        result = parse_user_memory(text)

        assert result is not None
        assert result.memory_text == "Content with spaces"


# =============================================================================
# Content Model Tests - parse_user_message_content()
# =============================================================================


class TestParseUserMessageContentCompacted:
    """Tests for parse_user_message_content() handling compacted summaries."""

    def test_compacted_summary_single_text_item(self):
        """Test compacted summary with single text content item."""
        text = f"{COMPACTED_SUMMARY_PREFIX}. The conversation summary."
        content_list = [TextContent(type="text", text=text)]

        content_model = parse_user_message_content(content_list)

        assert content_model is not None
        assert isinstance(content_model, CompactedSummaryContent)
        assert content_model.summary_text == text

    def test_compacted_summary_multiple_text_items(self):
        """Test compacted summary with multiple text content items combines all."""
        first_text = f"{COMPACTED_SUMMARY_PREFIX}. Summary part 1."
        second_text = "Summary part 2."
        third_text = "Summary part 3."
        content_list = [
            TextContent(type="text", text=first_text),
            TextContent(type="text", text=second_text),
            TextContent(type="text", text=third_text),
        ]

        content_model = parse_user_message_content(content_list)

        assert content_model is not None
        assert isinstance(content_model, CompactedSummaryContent)
        # All text items should be combined with double newlines
        expected = "\n\n".join([first_text, second_text, third_text])
        assert content_model.summary_text == expected


class TestParseUserMessageContentMemory:
    """Tests for parse_user_message_content() handling user memory."""

    def test_user_memory_detected(self):
        """Test user memory content is detected and returned."""
        text = "<user-memory-input>CLAUDE.md content here</user-memory-input>"
        content_list = [TextContent(type="text", text=text)]

        content_model = parse_user_message_content(content_list)

        assert content_model is not None
        assert isinstance(content_model, UserMemoryContent)
        assert content_model.memory_text == "CLAUDE.md content here"


class TestParseUserMessageContentRegular:
    """Tests for parse_user_message_content() handling regular user text."""

    def test_regular_text(self):
        """Test regular user text without special markers."""
        text = "Hello, please help me with this code."
        content_list = [TextContent(type="text", text=text)]

        content_model = parse_user_message_content(content_list)

        assert content_model is not None
        assert isinstance(content_model, UserTextContent)
        assert content_model.text == text
        assert content_model.ide_notifications is None

    def test_empty_content_list(self):
        """Test empty content list returns None."""
        content_list = []

        content_model = parse_user_message_content(content_list)

        assert content_model is None


# =============================================================================
# Formatting Tests - format_compacted_summary_content()
# =============================================================================


class TestFormatCompactedSummaryContent:
    """Tests for format_compacted_summary_content() formatter function."""

    def test_format_compacted_summary_basic(self):
        """Test basic compacted summary formatting."""
        content = CompactedSummaryContent(summary_text="Summary:\n- Point 1\n- Point 2")

        html = format_compacted_summary_content(content)

        # Should render as markdown (not preformatted)
        assert "<ul>" in html or "<li>" in html  # Markdown list rendering
        assert "Point 1" in html
        assert "Point 2" in html

    def test_format_compacted_summary_collapsible(self):
        """Test that long compacted summaries are collapsible."""
        # Create long content that exceeds threshold
        long_summary = "Summary:\n" + "\n".join([f"- Point {i}" for i in range(50)])
        content = CompactedSummaryContent(summary_text=long_summary)

        html = format_compacted_summary_content(content)

        # Should be collapsible for long content
        assert "<details" in html
        assert "<summary" in html


# =============================================================================
# Formatting Tests - format_user_memory_content()
# =============================================================================


class TestFormatUserMemoryContent:
    """Tests for format_user_memory_content() formatter function."""

    def test_format_user_memory_basic(self):
        """Test basic user memory formatting."""
        content = UserMemoryContent(memory_text="CLAUDE.md content")

        html = format_user_memory_content(content)

        assert "<pre>" in html
        assert "</pre>" in html
        assert "CLAUDE.md content" in html

    def test_format_user_memory_escapes_html(self):
        """Test that HTML characters are escaped."""
        content = UserMemoryContent(memory_text="<script>alert('xss')</script>")

        html = format_user_memory_content(content)

        assert "&lt;script&gt;" in html
        assert "<script>" not in html


# =============================================================================
# Formatting Tests - format_user_text_model_content()
# =============================================================================


class TestFormatUserTextModelContent:
    """Tests for format_user_text_model_content() formatter function."""

    def test_format_user_text_basic(self):
        """Test basic user text formatting."""
        content = UserTextContent(text="User question here")

        html = format_user_text_model_content(content)

        assert "<pre>" in html
        assert "User question here" in html

    def test_format_user_text_escapes_html(self):
        """Test that HTML characters are escaped."""
        content = UserTextContent(text='Test <b>bold</b> & "quotes"')

        html = format_user_text_model_content(content)

        assert "&lt;b&gt;" in html
        assert "&amp;" in html
        assert "&quot;" in html


# =============================================================================
# HTML Rendering Tests - Full Pipeline
# =============================================================================


def _create_user_message(text: str, session_id: str = "test_session") -> dict:
    """Helper to create a user message dict for testing."""
    return {
        "type": "user",
        "timestamp": "2025-06-11T22:45:17.436Z",
        "parentUuid": None,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "1.0.0",
        "uuid": "user_001",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


class TestCompactedSummaryRendering:
    """Tests for compacted summary rendering through full pipeline."""

    def test_compacted_summary_renders_with_title(self):
        """Test compacted summary shows appropriate title in HTML."""
        text = f"{COMPACTED_SUMMARY_PREFIX}. The previous session summary."
        message = _create_user_message(text)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Compacted")

            # Should have compacted in title or class
            assert "compacted" in html.lower()
            # Content should be present (the prefix text should be rendered)
            assert "This session is being continued" in html
        finally:
            test_file_path.unlink()

    def test_compacted_summary_css_class(self):
        """Test compacted summary has appropriate CSS class."""
        text = f"{COMPACTED_SUMMARY_PREFIX}. Summary content."
        message = _create_user_message(text)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Compacted CSS")

            # Check for compacted CSS class
            assert (
                'class="message user compacted' in html
                or "class='message user compacted" in html
            )
        finally:
            test_file_path.unlink()


class TestUserMemoryRendering:
    """Tests for user memory rendering through full pipeline."""

    def test_user_memory_renders_with_title(self):
        """Test user memory shows Memory title in HTML."""
        text = "<user-memory-input>Contents of CLAUDE.md</user-memory-input>"
        message = _create_user_message(text)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Memory")

            # Should have Memory in title
            assert "Memory" in html
            # Content should be present
            assert "Contents of CLAUDE.md" in html
        finally:
            test_file_path.unlink()

    def test_user_memory_preserves_content(self):
        """Test user memory content is properly preserved."""
        memory_text = "# CLAUDE.md\n\n- Rule 1\n- Rule 2"
        text = f"<user-memory-input>{memory_text}</user-memory-input>"
        message = _create_user_message(text)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Memory Content")

            # Content should be preserved (HTML escaped)
            assert "# CLAUDE.md" in html
            assert "Rule 1" in html
            assert "Rule 2" in html
        finally:
            test_file_path.unlink()


class TestRegularUserMessageRendering:
    """Tests for regular user message rendering through full pipeline."""

    def test_regular_user_message(self):
        """Test regular user message renders correctly."""
        text = "Hello, can you help me with Python?"
        message = _create_user_message(text)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Regular")

            # Should have user class without special modifiers (class includes session ID)
            assert "Hello, can you help me with Python?" in html
            # Check that it's a user message (class includes session ID suffix)
            assert "class='message user " in html or 'class="message user ' in html
        finally:
            test_file_path.unlink()
