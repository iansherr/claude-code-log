"""Tests for hook summary (stop_hook_summary) parsing and rendering."""

from claude_code_log.models import SystemTranscriptEntry
from claude_code_log.parser import parse_transcript_entry
from claude_code_log.html.renderer import generate_html


class TestHookSummaryParsing:
    """Test parsing of stop_hook_summary system entries."""

    def test_parse_hook_summary_without_content(self):
        """Test that hook summary without content field parses successfully."""
        data = {
            "parentUuid": "test-parent",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/home/user",
            "sessionId": "test-session",
            "version": "2.0.56",
            "type": "system",
            "subtype": "stop_hook_summary",
            "hookCount": 1,
            "hookInfos": [{"command": "uv run ruff format && uv run ruff check"}],
            "hookErrors": [],
            "preventedContinuation": False,
            "stopReason": "",
            "hasOutput": False,
            "level": "suggestion",
            "timestamp": "2025-12-02T23:05:58.427Z",
            "uuid": "test-uuid",
        }

        entry = parse_transcript_entry(data)

        assert isinstance(entry, SystemTranscriptEntry)
        assert entry.subtype == "stop_hook_summary"
        assert entry.content is None
        assert entry.hasOutput is False
        assert entry.hookErrors == []
        assert entry.hookInfos == [
            {"command": "uv run ruff format && uv run ruff check"}
        ]
        assert entry.preventedContinuation is False

    def test_parse_hook_summary_with_errors(self):
        """Test that hook summary with errors parses successfully."""
        data = {
            "parentUuid": "test-parent",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/home/user",
            "sessionId": "test-session",
            "version": "2.0.56",
            "type": "system",
            "subtype": "stop_hook_summary",
            "hookCount": 1,
            "hookInfos": [{"command": "pnpm lint"}],
            "hookErrors": [
                "Error: TypeScript compilation failed\nTS2307: Cannot find module"
            ],
            "preventedContinuation": False,
            "stopReason": "",
            "hasOutput": True,
            "level": "suggestion",
            "timestamp": "2025-12-02T23:05:58.427Z",
            "uuid": "test-uuid",
        }

        entry = parse_transcript_entry(data)

        assert isinstance(entry, SystemTranscriptEntry)
        assert entry.subtype == "stop_hook_summary"
        assert entry.hasOutput is True
        assert entry.hookErrors is not None
        assert len(entry.hookErrors) == 1
        assert "TypeScript compilation failed" in entry.hookErrors[0]

    def test_parse_system_message_with_content_still_works(self):
        """Test that regular system messages with content still parse correctly."""
        data = {
            "parentUuid": "test-parent",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/home/user",
            "sessionId": "test-session",
            "version": "2.0.56",
            "type": "system",
            "content": "<command-name>init</command-name>",
            "level": "info",
            "timestamp": "2025-12-02T23:05:58.427Z",
            "uuid": "test-uuid",
        }

        entry = parse_transcript_entry(data)

        assert isinstance(entry, SystemTranscriptEntry)
        assert entry.content == "<command-name>init</command-name>"
        assert entry.subtype is None


class TestHookSummaryRendering:
    """Test rendering of stop_hook_summary system entries."""

    def test_silent_hook_success_not_rendered(self):
        """Test that silent hook successes (no output, no errors) are not rendered."""
        messages = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/home/user",
                "sessionId": "test-session",
                "version": "2.0.56",
                "type": "system",
                "subtype": "stop_hook_summary",
                "hookCount": 1,
                "hookInfos": [{"command": "uv run ruff format"}],
                "hookErrors": [],
                "preventedContinuation": False,
                "hasOutput": False,
                "level": "suggestion",
                "timestamp": "2025-12-02T23:05:58.427Z",
                "uuid": "test-uuid",
            }
        ]

        parsed_messages = [parse_transcript_entry(msg) for msg in messages]
        html = generate_html(parsed_messages)

        # Should not contain actual hook content (skipped)
        # Note: CSS class definitions for .hook-summary will still be in the HTML
        assert "Hook failed" not in html
        assert "Hook output" not in html
        assert "uv run ruff format" not in html  # The hook command should not appear

    def test_hook_with_errors_rendered(self):
        """Test that hooks with errors are rendered as collapsible details."""
        messages = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/home/user",
                "sessionId": "test-session",
                "version": "2.0.56",
                "type": "system",
                "subtype": "stop_hook_summary",
                "hookCount": 1,
                "hookInfos": [{"command": "pnpm lint"}],
                "hookErrors": ["Error: lint failed"],
                "preventedContinuation": False,
                "hasOutput": True,
                "level": "suggestion",
                "timestamp": "2025-12-02T23:05:58.427Z",
                "uuid": "test-uuid",
            }
        ]

        parsed_messages = [parse_transcript_entry(msg) for msg in messages]
        html = generate_html(parsed_messages)

        # Should contain hook summary elements
        assert "hook-summary" in html
        assert "Hook failed" in html
        assert "pnpm lint" in html
        assert "Error: lint failed" in html

    def test_hook_with_output_but_no_errors_rendered(self):
        """Test that hooks with output but no errors are rendered."""
        messages = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/home/user",
                "sessionId": "test-session",
                "version": "2.0.56",
                "type": "system",
                "subtype": "stop_hook_summary",
                "hookCount": 1,
                "hookInfos": [{"command": "echo 'formatted'"}],
                "hookErrors": [],
                "preventedContinuation": False,
                "hasOutput": True,
                "level": "suggestion",
                "timestamp": "2025-12-02T23:05:58.427Z",
                "uuid": "test-uuid",
            }
        ]

        parsed_messages = [parse_transcript_entry(msg) for msg in messages]
        html = generate_html(parsed_messages)

        # Should contain hook summary elements
        assert "hook-summary" in html
        assert "Hook output" in html  # Not "Hook failed" since no errors

    def test_hook_with_ansi_errors_rendered(self):
        """Test that ANSI codes in hook errors are converted to HTML."""
        messages = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/home/user",
                "sessionId": "test-session",
                "version": "2.0.56",
                "type": "system",
                "subtype": "stop_hook_summary",
                "hookCount": 1,
                "hookInfos": [{"command": "pnpm lint"}],
                "hookErrors": ["\x1b[31mError:\x1b[0m Something went wrong"],
                "preventedContinuation": False,
                "hasOutput": True,
                "level": "suggestion",
                "timestamp": "2025-12-02T23:05:58.427Z",
                "uuid": "test-uuid",
            }
        ]

        parsed_messages = [parse_transcript_entry(msg) for msg in messages]
        html = generate_html(parsed_messages)

        # ANSI codes should be converted, not present raw
        assert "\x1b[" not in html
        assert "Something went wrong" in html

    def test_regular_system_message_still_renders(self):
        """Test that regular system messages with content still render correctly."""
        messages = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/home/user",
                "sessionId": "test-session",
                "version": "2.0.56",
                "type": "system",
                "content": "<command-name>init</command-name>",
                "level": "info",
                "timestamp": "2025-12-02T23:05:58.427Z",
                "uuid": "test-uuid",
            }
        ]

        parsed_messages = [parse_transcript_entry(msg) for msg in messages]
        html = generate_html(parsed_messages)

        # Should render the command name
        assert "init" in html
