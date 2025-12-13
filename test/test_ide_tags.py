"""Tests for IDE tag parsing and formatting in user messages.

Split into:
- Parsing tests: test parse_ide_notifications() from parser.py
- Formatting tests: test format_ide_notification_content() from user_formatters.py
- User message tests: test parse_user_message_content() and formatters
- Assistant text tests: test format_assistant_text_content()
"""

from claude_code_log.parser import parse_ide_notifications, parse_user_message_content
from claude_code_log.html.user_formatters import (
    format_ide_notification_content,
    format_user_text_content,
)
from claude_code_log.html.assistant_formatters import format_assistant_text_content
from claude_code_log.models import (
    AssistantTextContent,
    TextContent,
    ImageContent,
    ImageSource,
)


# =============================================================================
# Parsing Tests - parse_ide_notifications()
# =============================================================================


class TestParseIdeNotifications:
    """Tests for parse_ide_notifications() parser function."""

    def test_parse_ide_opened_file_tag(self):
        """Test that <ide_opened_file> tags are parsed correctly."""
        text = (
            "<ide_opened_file>The user opened the file "
            "e:\\Workspace\\test.py in the IDE. This may or may not be related to the current task."
            "</ide_opened_file>\n"
            "Here is my actual question."
        )

        result = parse_ide_notifications(text)

        assert result is not None
        assert len(result.opened_files) == 1
        assert "e:\\Workspace\\test.py" in result.opened_files[0].content
        assert result.remaining_text == "Here is my actual question."

    def test_parse_multiple_ide_tags(self):
        """Test handling multiple IDE tags in one message."""
        text = (
            "<ide_opened_file>First file opened.</ide_opened_file>\n"
            "Some text in between.\n"
            "<ide_opened_file>Second file opened.</ide_opened_file>"
        )

        result = parse_ide_notifications(text)

        assert result is not None
        assert len(result.opened_files) == 2
        assert "First file opened" in result.opened_files[0].content
        assert "Second file opened" in result.opened_files[1].content
        assert "Some text in between." in result.remaining_text
        assert "<ide_opened_file>" not in result.remaining_text

    def test_parse_no_ide_tags(self):
        """Test that messages without IDE tags return None."""
        text = "This is a regular user message without any IDE tags."

        result = parse_ide_notifications(text)

        assert result is None

    def test_parse_multiline_ide_tag(self):
        """Test IDE tags with multiline content."""
        text = (
            "<ide_opened_file>The user opened the file\n"
            "e:\\Workspace\\test.py in the IDE.\n"
            "This may or may not be related.</ide_opened_file>\n"
            "User question follows."
        )

        result = parse_ide_notifications(text)

        assert result is not None
        assert len(result.opened_files) == 1
        assert "e:\\Workspace\\test.py" in result.opened_files[0].content
        assert result.remaining_text == "User question follows."

    def test_parse_ide_diagnostics(self):
        """Test parsing of IDE diagnostics from post-tool-use-hook."""
        text = (
            "<post-tool-use-hook><ide_diagnostics>["
            '{"filePath": "/e:/Workspace/test.py", "line": 12, "column": 6, '
            '"message": "Package not installed", "code": "[object Object]", "severity": "Hint"},'
            '{"filePath": "/e:/Workspace/other.py", "line": 5, "column": 1, '
            '"message": "Unused import", "severity": "Warning"}'
            "]</ide_diagnostics></post-tool-use-hook>\n"
            "Here is my question."
        )

        result = parse_ide_notifications(text)

        assert result is not None
        assert len(result.diagnostics) == 1
        # Diagnostics are parsed as a single entry with a list of diagnostic items
        assert result.diagnostics[0].diagnostics is not None
        assert len(result.diagnostics[0].diagnostics) == 2
        assert (
            result.diagnostics[0].diagnostics[0]["filePath"] == "/e:/Workspace/test.py"
        )
        assert result.diagnostics[0].diagnostics[1]["message"] == "Unused import"
        assert result.remaining_text == "Here is my question."

    def test_parse_ide_selection_short(self):
        """Test parsing of short IDE selection."""
        text = (
            "<ide_selection>The user selected the lines 7 to 7 from file.py:\n"
            "nx_utils\n\n"
            "This may or may not be related to the current task.</ide_selection>\n"
            "Can you explain this?"
        )

        result = parse_ide_notifications(text)

        assert result is not None
        assert len(result.selections) == 1
        assert "nx_utils" in result.selections[0].content
        assert "lines 7 to 7" in result.selections[0].content
        assert result.remaining_text == "Can you explain this?"

    def test_parse_all_ide_tag_types(self):
        """Test parsing all IDE tag types together."""
        text = (
            "<ide_opened_file>User opened main.py</ide_opened_file>\n"
            "<ide_selection>selected_variable</ide_selection>\n"
            "<post-tool-use-hook><ide_diagnostics>["
            '{"line": 5, "message": "Unused variable"}'
            "]</ide_diagnostics></post-tool-use-hook>\n"
            "Please help."
        )

        result = parse_ide_notifications(text)

        assert result is not None
        assert len(result.opened_files) == 1
        assert len(result.selections) == 1
        assert len(result.diagnostics) == 1
        assert "User opened main.py" in result.opened_files[0].content
        assert "selected_variable" in result.selections[0].content
        assert result.diagnostics[0].diagnostics[0]["message"] == "Unused variable"
        assert result.remaining_text == "Please help."


# =============================================================================
# Formatting Tests - format_ide_notification_content()
# =============================================================================


class TestFormatIdeNotificationContent:
    """Tests for format_ide_notification_content() formatter function."""

    def test_format_ide_opened_file_tag(self):
        """Test that <ide_opened_file> tags are formatted correctly."""
        text = (
            "<ide_opened_file>The user opened the file "
            "e:\\Workspace\\test.py in the IDE.</ide_opened_file>\n"
            "Question here."
        )

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        assert len(notifications) == 1
        assert "<div class='ide-notification'>" in notifications[0]
        assert "e:\\Workspace\\test.py" in notifications[0]

    def test_format_multiple_ide_tags(self):
        """Test formatting multiple IDE tags."""
        text = (
            "<ide_opened_file>First file opened.</ide_opened_file>\n"
            "Some text.\n"
            "<ide_opened_file>Second file opened.</ide_opened_file>"
        )

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        assert len(notifications) == 2
        assert all("<div class='ide-notification'>" in n for n in notifications)

    def test_format_special_chars_escaped(self):
        """Test that special HTML characters are escaped in IDE tag content."""
        text = '<ide_opened_file>File with <special> & "characters" in path.</ide_opened_file>'

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        assert len(notifications) == 1
        assert "&lt;special&gt;" in notifications[0]
        assert "&amp;" in notifications[0]

    def test_format_ide_diagnostics(self):
        """Test formatting of IDE diagnostics."""
        text = (
            "<post-tool-use-hook><ide_diagnostics>["
            '{"filePath": "/e:/Workspace/test.py", "line": 12, '
            '"message": "Package not installed", "severity": "Hint"},'
            '{"filePath": "/e:/Workspace/other.py", "line": 5, '
            '"message": "Unused import", "severity": "Warning"}'
            "]</ide_diagnostics></post-tool-use-hook>\n"
            "Question."
        )

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        # Should have two diagnostic notifications (one per diagnostic object)
        assert len(notifications) == 2
        assert all("IDE Diagnostic" in n for n in notifications)
        assert all("<table class='tool-params-table'>" in n for n in notifications)
        assert "Package not installed" in notifications[0]
        assert "Unused import" in notifications[1]

    def test_format_ide_selection_short(self):
        """Test formatting of short IDE selection (not collapsible)."""
        text = (
            "<ide_selection>The user selected the lines 7 to 7:\n"
            "nx_utils</ide_selection>\n"
            "Question."
        )

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        assert len(notifications) == 1
        assert "nx_utils" in notifications[0]
        # Short selections should not be in a collapsible details element
        assert "<details" not in notifications[0]

    def test_format_ide_selection_long(self):
        """Test formatting of long IDE selection (collapsible)."""
        long_selection = "The user selected lines 1 to 50:\n" + ("line content\n" * 30)
        text = f"<ide_selection>{long_selection}</ide_selection>\nQuestion."

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        assert len(notifications) == 1
        # Long selections should be in a collapsible details element
        assert "<details class='ide-selection-collapsible'>" in notifications[0]
        assert "<summary>" in notifications[0]
        assert "..." in notifications[0]  # Preview indicator

    def test_format_mixed_ide_tags(self):
        """Test formatting all IDE tag types together."""
        text = (
            "<ide_opened_file>User opened config.json</ide_opened_file>\n"
            "<post-tool-use-hook><ide_diagnostics>["
            '{"line": 10, "message": "Syntax error"}'
            "]</ide_diagnostics></post-tool-use-hook>\n"
            "Please review."
        )

        result = parse_ide_notifications(text)
        notifications = format_ide_notification_content(result)

        # Should have 2 notifications total: 1 file open + 1 diagnostic
        assert len(notifications) == 2
        assert "User opened config.json" in notifications[0]
        assert "IDE Diagnostic" in notifications[1]
        assert "Syntax error" in notifications[1]


# =============================================================================
# User Message Content Tests - parse_user_message_content()
# =============================================================================


class TestParseUserMessageContent:
    """Tests for parse_user_message_content() function."""

    def test_parse_user_message_with_multi_item_content(self):
        """Test parsing user message with multiple content items (text + image)."""
        text_with_tag = (
            "<ide_opened_file>User opened example.py</ide_opened_file>\n"
            "Please review this code and this screenshot:"
        )
        image_item = ImageContent(
            type="image",
            source=ImageSource(
                type="base64",
                media_type="image/png",
                data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            ),
        )

        content_list = [
            TextContent(type="text", text=text_with_tag),
            image_item,
        ]

        content_model = parse_user_message_content(content_list)

        # Should return UserTextContent with IDE notifications
        assert content_model is not None
        assert content_model.ide_notifications is not None
        assert len(content_model.ide_notifications.opened_files) == 1
        assert (
            "User opened example.py"
            in content_model.ide_notifications.opened_files[0].content
        )

        # Remaining text should be preserved
        assert "Please review this code" in content_model.text


# =============================================================================
# Content Formatter Tests
# =============================================================================


class TestContentFormatters:
    """Tests for content formatter functions (user and assistant text)."""

    def test_format_user_text_content(self):
        """Test that user text is formatted as preformatted HTML."""
        html = format_user_text_content("Simple user message")

        # Should be wrapped in <pre> for user messages
        assert html.startswith("<pre>")
        assert html.endswith("</pre>")
        assert "Simple user message" in html

    def test_format_assistant_text_content(self):
        """Test that assistant text is formatted as markdown."""
        content = AssistantTextContent(text="**Bold** response")

        html = format_assistant_text_content(content)

        # Should be rendered as markdown (no <pre>)
        assert "<pre>" not in html
        # Markdown should be processed
        assert "<strong>Bold</strong>" in html or "<b>Bold</b>" in html
