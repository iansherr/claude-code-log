#!/usr/bin/env python3
"""Tests for template data structures and generation using existing test data."""

import pytest
import re
from pathlib import Path
from claude_code_log.converter import load_transcript, load_directory_transcripts
from claude_code_log.html.renderer import generate_html, generate_projects_index_html
from claude_code_log.renderer import (
    Renderer,
    TemplateMessage,
    TemplateProject,
    TemplateSummary,
)
from claude_code_log.models import (
    MessageMeta,
    UserTextMessage,
    AssistantTextMessage,
    SessionHeaderMessage,
    ToolUseMessage,
    ToolUseContent,
)


class TestTemplateMessage:
    """Test TemplateMessage data structure."""

    def test_template_message_creation(self):
        """Test creating a TemplateMessage with all fields."""
        meta = MessageMeta(
            session_id="test-session",
            timestamp="2025-06-14T10:00:00Z",
            uuid="test-uuid",
        )
        content = UserTextMessage(meta=meta)
        msg = TemplateMessage(content)
        renderer = Renderer()

        assert msg.type == "user"
        assert msg.meta.timestamp == "2025-06-14T10:00:00Z"
        assert renderer.title_content(msg) == "User"

    def test_template_message_title_generation(self):
        """Test that Renderer.title_content generates correct titles."""
        meta = MessageMeta.empty()
        renderer = Renderer()

        # Test UserTextMessage
        user_content = UserTextMessage(meta=meta)
        user_msg = TemplateMessage(user_content)
        assert renderer.title_content(user_msg) == "User"

        # Test AssistantTextMessage
        assistant_content = AssistantTextMessage(meta=meta)
        assistant_msg = TemplateMessage(assistant_content)
        assert renderer.title_content(assistant_msg) == "Assistant"

        # Test SessionHeaderMessage - fallback to type-based title
        session_content = SessionHeaderMessage(
            meta=meta, title="Test Session", session_id="test-id"
        )
        session_msg = TemplateMessage(session_content)
        assert renderer.title_content(session_msg) == "Session Header"


class TestTemplateProject:
    """Test TemplateProject data structure."""

    def test_template_project_basic(self):
        """Test creating a TemplateProject with basic data."""
        project_data = {
            "name": "test-project",
            "html_file": "test-project/combined_transcripts.html",
            "jsonl_count": 3,
            "message_count": 15,
            "last_modified": 1700000000.0,
        }

        project = TemplateProject(project_data)

        assert project.name == "test-project"
        assert project.html_file == "test-project/combined_transcripts.html"
        assert project.jsonl_count == 3
        assert project.message_count == 15
        assert project.display_name == "test-project"
        # Check date format: "2023-11-1[45] HH:MM:SS" where day (14-15) and hour can vary by timezone
        assert re.match(r"2023-11-1[45] \d{2}:\d{2}:20", project.formatted_date)

    def test_template_project_dash_formatting(self):
        """Test TemplateProject display name formatting for dashed names."""
        project_data = {
            "name": "-user-workspace-my-app",
            "html_file": "-user-workspace-my-app/combined_transcripts.html",
            "jsonl_count": 2,
            "message_count": 8,
            "last_modified": 1700000100.0,
        }

        project = TemplateProject(project_data)

        assert project.name == "-user-workspace-my-app"
        assert project.display_name == "user/workspace/my/app"
        # Check date format: "2023-11-1[45] HH:15:00" where day (14-15) and hour can vary by timezone
        assert re.match(r"2023-11-1[45] \d{2}:15:00", project.formatted_date)

    def test_template_project_no_leading_dash(self):
        """Test TemplateProject display name when no leading dash."""
        project_data = {
            "name": "simple-project-name",
            "html_file": "simple-project-name/combined_transcripts.html",
            "jsonl_count": 1,
            "message_count": 5,
            "last_modified": 1700000200.0,
        }

        project = TemplateProject(project_data)

        assert project.display_name == "simple-project-name"

    def test_template_project_time_range(self):
        """Test TemplateProject time range formatting."""
        # Test with both earliest and latest timestamps
        project_data = {
            "name": "time-range-project",
            "html_file": "time-range-project/combined_transcripts.html",
            "jsonl_count": 1,
            "message_count": 5,
            "last_modified": 1700000000.0,
            "earliest_timestamp": "2025-06-14T08:00:00Z",
            "latest_timestamp": "2025-06-14T10:00:00Z",
        }

        project = TemplateProject(project_data)
        assert (
            project.formatted_time_range == "2025-06-14 08:00:00 to 2025-06-14 10:00:00"
        )

    def test_template_project_single_timestamp(self):
        """Test TemplateProject with single timestamp (same earliest and latest)."""
        project_data = {
            "name": "single-time-project",
            "html_file": "single-time-project/combined_transcripts.html",
            "jsonl_count": 1,
            "message_count": 1,
            "last_modified": 1700000000.0,
            "earliest_timestamp": "2025-06-14T08:00:00Z",
            "latest_timestamp": "2025-06-14T08:00:00Z",
        }

        project = TemplateProject(project_data)
        assert project.formatted_time_range == "2025-06-14 08:00:00"

    def test_template_project_no_timestamps(self):
        """Test TemplateProject with no timestamps."""
        project_data = {
            "name": "no-time-project",
            "html_file": "no-time-project/combined_transcripts.html",
            "jsonl_count": 1,
            "message_count": 1,
            "last_modified": 1700000000.0,
        }

        project = TemplateProject(project_data)
        assert project.formatted_time_range == ""


class TestTemplateSummary:
    """Test TemplateSummary data structure."""

    def test_template_summary_calculation(self):
        """Test TemplateSummary calculations."""
        project_summaries = [
            {
                "name": "project1",
                "jsonl_count": 3,
                "message_count": 15,
                "last_modified": 1700000000.0,
            },
            {
                "name": "project2",
                "jsonl_count": 2,
                "message_count": 8,
                "last_modified": 1700000100.0,
            },
            {
                "name": "project3",
                "jsonl_count": 1,
                "message_count": 12,
                "last_modified": 1700000200.0,
            },
        ]

        summary = TemplateSummary(project_summaries)

        assert summary.total_projects == 3
        assert summary.total_jsonl == 6  # 3 + 2 + 1
        assert summary.total_messages == 35  # 15 + 8 + 12

    def test_template_summary_empty_list(self):
        """Test TemplateSummary with empty project list."""
        summary = TemplateSummary([])

        assert summary.total_projects == 0
        assert summary.total_jsonl == 0
        assert summary.total_messages == 0


class TestDataWithTestFiles:
    """Test template generation using actual test data files."""

    def test_representative_messages_data_structure(self):
        """Test that representative messages generate proper template data."""
        test_data_path = (
            Path(__file__).parent / "test_data" / "representative_messages.jsonl"
        )

        messages = load_transcript(test_data_path)
        html = generate_html(messages, "Test Transcript")

        # Verify the data loaded correctly
        assert len(messages) > 0

        # Check that different message types are present
        message_types = {msg.type for msg in messages}
        assert "user" in message_types
        assert "assistant" in message_types
        assert "summary" in message_types

        # Verify HTML structure
        assert "<!DOCTYPE html>" in html
        assert "<title>Test Transcript</title>" in html
        assert "message user" in html
        assert "message assistant" in html
        # Summary messages are now integrated into session headers
        assert "session-summary" in html or "Summary:" in html

    def test_edge_cases_data_structure(self):
        """Test that edge cases data generates proper template data."""
        test_data_path = Path(__file__).parent / "test_data" / "edge_cases.jsonl"

        messages = load_transcript(test_data_path)
        html = generate_html(messages, "Edge Cases")

        # Verify the data loaded correctly
        assert len(messages) > 0

        # Check that HTML handles edge cases properly
        assert "<!DOCTYPE html>" in html
        assert "<title>Edge Cases</title>" in html

        # Check that special characters are handled
        assert "café" in html or "caf&eacute;" in html
        assert "🎉" in html  # Emoji should be preserved

        # Check that tool content is rendered
        assert "tool-use" in html or "tool-result" in html

    def test_multi_session_data_structure(self):
        """Test that multiple sessions generate proper session dividers."""
        test_data_dir = Path(__file__).parent / "test_data"

        # Load from directory to get multiple sessions
        messages = load_directory_transcripts(test_data_dir)
        html = generate_html(messages, "Multi Session Test")

        # Verify session dividers are present
        session_divider_count = html.count("session-divider")
        assert session_divider_count > 0, "Should have at least one session divider"

        # Check that messages from different files are included
        assert len(messages) > 0

        # Verify HTML structure for multi-session
        assert "<!DOCTYPE html>" in html
        assert "Multi Session Test" in html

    def test_empty_directory_handling(self):
        """Test handling of directories with no JSONL files."""
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Should return empty list for directory with no JSONL files
            messages = load_directory_transcripts(temp_path)
            assert messages == []

            # Should generate minimal HTML for empty message list
            html = generate_html(messages, "Empty Test")
            assert "<!DOCTYPE html>" in html
            assert "<title>Empty Test</title>" in html

    def test_projects_index_generation(self):
        """Test generating index HTML with test project data."""
        project_summaries = [
            {
                "name": "test-project-1",
                "path": Path("/tmp/project1"),
                "html_file": "test-project-1/combined_transcripts.html",
                "jsonl_count": 3,
                "message_count": 15,
                "last_modified": 1700000000.0,
            },
            {
                "name": "-user-workspace-my-app",
                "path": Path("/tmp/project2"),
                "html_file": "-user-workspace-my-app/combined_transcripts.html",
                "jsonl_count": 2,
                "message_count": 8,
                "last_modified": 1700000100.0,
            },
        ]

        index_html = generate_projects_index_html(project_summaries)

        # Check basic structure
        assert "<!DOCTYPE html>" in index_html
        assert "<title>Claude Code Projects</title>" in index_html

        # Check that both projects are listed
        assert "test-project-1" in index_html
        assert "user/workspace/my/app" in index_html  # Formatted name

        # Check summary stats
        assert "23" in index_html  # Total messages (15 + 8)
        assert "5" in index_html  # Total jsonl files (3 + 2)
        assert "2" in index_html  # Total projects

    def test_projects_index_with_date_range(self):
        """Test generating index HTML with date range in title."""
        project_summaries = [
            {
                "name": "test-project",
                "path": Path("/tmp/project"),
                "html_file": "test-project/combined_transcripts.html",
                "jsonl_count": 1,
                "message_count": 5,
                "last_modified": 1700000000.0,
            }
        ]

        index_html = generate_projects_index_html(
            project_summaries, from_date="yesterday", to_date="today"
        )

        # Check that date range appears in title
        assert "Claude Code Projects (from yesterday to today)" in index_html


class TestErrorHandling:
    """Test error handling in template generation."""

    def test_malformed_message_handling(self):
        """Test that malformed messages are skipped gracefully."""
        import tempfile

        # Create a JSONL file with mix of valid and invalid entries
        malformed_data = [
            '{"type": "user", "timestamp": "2025-06-14T10:00:00Z", "parentUuid": null, "isSidechain": false, "userType": "human", "cwd": "/tmp", "sessionId": "test", "version": "1.0.0", "uuid": "test_000", "message": {"role": "user", "content": [{"type": "text", "text": "Valid message"}]}}',
            '{"type": "invalid_type", "malformed": true}',  # Invalid type
            '{"incomplete": "message"}',  # Missing required fields
            '{"type": "user", "timestamp": "2025-06-14T10:01:00Z", "parentUuid": null, "isSidechain": false, "userType": "human", "cwd": "/tmp", "sessionId": "test", "version": "1.0.0", "uuid": "test_001", "message": {"role": "user", "content": [{"type": "text", "text": "Another valid message"}]}}',
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in malformed_data:
                f.write(line + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            # Should load only valid messages, skipping malformed ones
            messages = load_transcript(test_file_path)

            # Should have loaded 2 valid messages, skipped 2 malformed ones
            assert len(messages) == 2

            # Should generate HTML without errors
            html = generate_html(messages, "Malformed Test")
            assert "<!DOCTYPE html>" in html
            assert "Valid message" in html
            assert "Another valid message" in html

        finally:
            test_file_path.unlink()


class TestTemplateMessageTree:
    """Test TemplateMessage tree building."""

    def _create_message(
        self,
        msg_type: str,
        msg_id: str | None = None,
        ancestry: list[str] | None = None,
    ) -> TemplateMessage:
        """Helper to create a minimal TemplateMessage for testing."""
        meta = MessageMeta(
            session_id="test-session",
            timestamp="2025-06-14T10:00:00Z",
            uuid=msg_id or "test-uuid",
        )

        # Create appropriate content based on message type
        if msg_type == "user":
            content = UserTextMessage(meta=meta)
        elif msg_type == "assistant":
            content = AssistantTextMessage(meta=meta)
        elif msg_type == "tool_use":
            content = ToolUseMessage(
                meta=meta,
                input=ToolUseContent(
                    type="tool_use", id="test-id", name="TestTool", input={}
                ),
                tool_use_id="test-id",
                tool_name="TestTool",
            )
        elif msg_type == "session":
            content = SessionHeaderMessage(
                meta=meta, title="Test Session", session_id="test-session"
            )
        else:
            # Fallback to UserTextMessage for unknown types
            content = UserTextMessage(meta=meta)

        msg = TemplateMessage(content, message_id=msg_id, ancestry=ancestry)
        return msg

    def test_children_field_default_empty(self):
        """Test that children field defaults to empty list."""
        msg = self._create_message("user")

        assert msg.children == []


class TestTreeBuildingIntegration:
    """Integration tests for tree building with real transcript data."""

    def test_tree_built_from_representative_messages(self):
        """Test that tree structure is built when rendering real messages."""
        test_data_path = (
            Path(__file__).parent / "test_data" / "representative_messages.jsonl"
        )

        messages = load_transcript(test_data_path)
        # Generate HTML (this builds the tree internally)
        generate_html(messages, "Test Transcript")

        # Note: We can't easily access the internal tree structure since
        # _build_message_tree is private. This test just verifies the
        # tree building doesn't break normal HTML generation.


if __name__ == "__main__":
    pytest.main([__file__])
