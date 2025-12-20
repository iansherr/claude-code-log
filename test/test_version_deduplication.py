#!/usr/bin/env python3
"""Tests for version-based deduplication during Claude Code upgrades."""

from datetime import datetime
from claude_code_log.models import (
    AssistantTranscriptEntry,
    AssistantMessageModel,
    UserTranscriptEntry,
    UserMessageModel,
    ToolUseContent,
    ToolResultContent,
)
from claude_code_log.converter import deduplicate_messages
from claude_code_log.html.renderer import generate_html


class TestVersionDeduplication:
    """Test that duplicate messages from version upgrades are deduplicated."""

    def test_assistant_message_deduplication(self):
        """Test deduplication of assistant messages by version."""
        timestamp = datetime.now().isoformat()

        # Same assistant message in two different Claude Code versions
        msg_v1 = AssistantTranscriptEntry(
            type="assistant",
            uuid="uuid-v1",
            parentUuid="parent-001",
            timestamp=timestamp,
            version="2.0.31",  # Older version
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=AssistantMessageModel(
                id="msg_duplicate",
                type="message",
                role="assistant",
                model="claude-sonnet-4-5",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="toolu_edit",
                        name="Edit",
                        input={
                            "file_path": "/test/file.py",
                            "old_string": "old",
                            "new_string": "new",
                        },
                    ),
                ],
                stop_reason="tool_use",
            ),
        )

        msg_v2 = AssistantTranscriptEntry(
            type="assistant",
            uuid="uuid-v2",
            parentUuid="parent-002",
            timestamp=timestamp,
            version="2.0.34",  # Newer version
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=AssistantMessageModel(
                id="msg_duplicate",  # SAME message.id
                type="message",
                role="assistant",
                model="claude-sonnet-4-5",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="toolu_edit",
                        name="Edit",
                        input={
                            "file_path": "/test/file.py",
                            "old_string": "old",
                            "new_string": "new",
                        },
                    ),
                ],
                stop_reason="tool_use",
            ),
        )

        # Test both orderings
        for messages in [[msg_v1, msg_v2], [msg_v2, msg_v1]]:
            deduped = deduplicate_messages(messages)
            html = generate_html(deduped, "Version Test")

            # Should appear only once
            tool_summary_count = html.count(
                "<span class='tool-summary'>/test/file.py</span>"
            )
            assert tool_summary_count == 1, (
                f"Expected 1 tool summary, got {tool_summary_count}"
            )

    def test_tool_result_deduplication(self):
        """Test deduplication of tool results by version."""
        timestamp = datetime.now().isoformat()

        # Same tool result in two different Claude Code versions
        result_v1 = UserTranscriptEntry(
            type="user",
            uuid="uuid-result-v1",
            parentUuid="parent-001",
            timestamp=timestamp,
            version="2.0.31",  # Older version
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    ToolResultContent(
                        type="tool_result",
                        tool_use_id="toolu_read_test",
                        content="File contents here",
                    )
                ],
            ),
        )

        result_v2 = UserTranscriptEntry(
            type="user",
            uuid="uuid-result-v2",
            parentUuid="parent-002",
            timestamp=timestamp,
            version="2.0.34",  # Newer version
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    ToolResultContent(
                        type="tool_result",
                        tool_use_id="toolu_read_test",  # SAME tool_use_id
                        content="File contents here",
                    )
                ],
            ),
        )

        # Test both orderings
        for messages in [[result_v1, result_v2], [result_v2, result_v1]]:
            deduped = deduplicate_messages(messages)
            html = generate_html(deduped, "Tool Result Test")

            # Should appear only once
            content_count = html.count("File contents here")
            assert content_count == 1, f"Expected 1 tool result, got {content_count}"

    def test_full_stutter_pair(self):
        """Test complete assistant+tool_result pair deduplication."""
        timestamp = datetime.now().isoformat()

        # Version 2.0.31 pair
        assist_v1 = AssistantTranscriptEntry(
            type="assistant",
            uuid="assist-v1",
            parentUuid="parent-001",
            timestamp=timestamp,
            version="2.0.31",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=AssistantMessageModel(
                id="msg_full_test",
                type="message",
                role="assistant",
                model="claude-sonnet-4-5",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="toolu_full_test",
                        name="Read",
                        input={"file_path": "/test/data.txt"},
                    ),
                ],
                stop_reason="tool_use",
            ),
        )

        result_v1 = UserTranscriptEntry(
            type="user",
            uuid="result-v1",
            parentUuid="assist-v1",
            timestamp=timestamp,
            version="2.0.31",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    ToolResultContent(
                        type="tool_result",
                        tool_use_id="toolu_full_test",
                        content="Data content",
                    )
                ],
            ),
        )

        # Version 2.0.34 pair (same IDs)
        assist_v2 = AssistantTranscriptEntry(
            type="assistant",
            uuid="assist-v2",
            parentUuid="parent-002",
            timestamp=timestamp,
            version="2.0.34",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=AssistantMessageModel(
                id="msg_full_test",  # SAME
                type="message",
                role="assistant",
                model="claude-sonnet-4-5",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="toolu_full_test",  # SAME
                        name="Read",
                        input={"file_path": "/test/data.txt"},
                    ),
                ],
                stop_reason="tool_use",
            ),
        )

        result_v2 = UserTranscriptEntry(
            type="user",
            uuid="result-v2",
            parentUuid="assist-v2",
            timestamp=timestamp,
            version="2.0.34",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    ToolResultContent(
                        type="tool_result",
                        tool_use_id="toolu_full_test",  # SAME
                        content="Data content",
                    )
                ],
            ),
        )

        # Combine: v1 pair, then v2 pair
        messages = [assist_v1, result_v1, assist_v2, result_v2]
        deduped = deduplicate_messages(messages)
        html = generate_html(deduped, "Full Pair Test")

        # Each should appear only once
        file_path_count = html.count("/test/data.txt")
        assert file_path_count == 1, f"Expected 1 file path, got {file_path_count}"

        content_count = html.count("Data content")
        assert content_count == 1, f"Expected 1 data content, got {content_count}"

    def test_user_text_message_deduplication(self):
        """Test deduplication of user text messages with same timestamp but different UUIDs.

        This can happen during git branch switches where Claude Code logs the same
        user input multiple times with content split across entries.
        """
        from claude_code_log.models import TextContent

        timestamp = "2025-11-13T11:44:08.771Z"

        # Message 1: Has both IDE tag and actual text (2 content items) - this is the "best"
        msg1 = UserTranscriptEntry(
            type="user",
            uuid="uuid-msg1",
            parentUuid="parent-001",
            timestamp=timestamp,
            version="2.0.37",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    TextContent(
                        type="text",
                        text="<ide_opened_file>User opened test.md</ide_opened_file>",
                    ),
                    TextContent(
                        type="text",
                        text="This is the actual user message content.",
                    ),
                ],
            ),
        )

        # Message 2: Only has the actual text (1 content item)
        msg2 = UserTranscriptEntry(
            type="user",
            uuid="uuid-msg2",
            parentUuid="parent-002",
            timestamp=timestamp,  # Same timestamp
            version="2.0.37",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    TextContent(
                        type="text",
                        text="This is the actual user message content.",
                    ),
                ],
            ),
        )

        # Message 3: Only has IDE tag (1 content item)
        msg3 = UserTranscriptEntry(
            type="user",
            uuid="uuid-msg3",
            parentUuid="parent-003",
            timestamp=timestamp,  # Same timestamp
            version="2.0.37",
            isSidechain=False,
            userType="external",
            cwd="/test",
            sessionId="session-test",
            message=UserMessageModel(
                role="user",
                content=[
                    TextContent(
                        type="text",
                        text="<ide_opened_file>User opened test.md</ide_opened_file>",
                    ),
                ],
            ),
        )

        # Test all orderings - should always keep msg1 (most content items)
        for messages in [
            [msg1, msg2, msg3],
            [msg2, msg1, msg3],
            [msg3, msg2, msg1],
        ]:
            deduped = deduplicate_messages(messages)
            html = generate_html(deduped, "User Text Dedup Test")

            # The actual message should appear only once
            content_count = html.count("This is the actual user message content.")
            assert content_count == 1, (
                f"Expected 1 message content, got {content_count}"
            )

            # Should have kept msg1 which has the IDE notification
            assert "test.md" in html, "Expected IDE notification to be present"
