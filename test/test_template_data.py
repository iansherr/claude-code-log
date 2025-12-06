#!/usr/bin/env python3
"""Tests for template data structures and generation using existing test data."""

import pytest
import re
from pathlib import Path
from claude_code_log.parser import load_transcript, load_directory_transcripts
from claude_code_log.renderer import (
    generate_html,
    TemplateMessage,
    TemplateProject,
    TemplateSummary,
    generate_projects_index_html,
)


class TestTemplateMessage:
    """Test TemplateMessage data structure."""

    def test_template_message_creation(self):
        """Test creating a TemplateMessage with all fields."""
        msg = TemplateMessage(
            message_type="user",
            content_html="<p>Test content</p>",
            formatted_timestamp="2025-06-14 10:00:00",
            css_class="user",
            raw_timestamp=None,
        )

        assert msg.type == "user"
        assert msg.content_html == "<p>Test content</p>"
        assert msg.formatted_timestamp == "2025-06-14 10:00:00"
        assert msg.css_class == "user"
        assert msg.message_title == "User"

    def test_template_message_title_capitalization(self):
        """Test that message_title properly capitalizes message types."""
        test_cases = [
            ("user", "User"),
            ("assistant", "Assistant"),
            ("system", "System"),
            ("summary", "Summary"),
        ]

        for msg_type, expected_display in test_cases:
            msg = TemplateMessage(
                message_type=msg_type,
                content_html="content",
                formatted_timestamp="time",
                css_class="class",
                raw_timestamp=None,
            )
            assert msg.message_title == expected_display


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
    """Test TemplateMessage tree building and flatten functionality."""

    def _create_message(
        self, msg_type: str, msg_id: str | None = None, ancestry: list | None = None
    ) -> TemplateMessage:
        """Helper to create a minimal TemplateMessage for testing."""
        msg = TemplateMessage(
            message_type=msg_type,
            content_html=f"<p>{msg_type} content</p>",
            formatted_timestamp="2025-06-14 10:00:00",
            css_class=msg_type,
            raw_timestamp=None,
        )
        if msg_id:
            msg.message_id = msg_id
        if ancestry:
            msg.ancestry = ancestry
        return msg

    def test_flatten_single_message(self):
        """Test flattening a single message with no children."""
        msg = self._create_message("user", "m1", [])

        result = msg.flatten()

        assert len(result) == 1
        assert result[0] is msg

    def test_flatten_with_children(self):
        """Test flattening a message with children."""
        parent = self._create_message("user", "m1", [])
        child1 = self._create_message("assistant", "m2", ["m1"])
        child2 = self._create_message("tool_use", "m3", ["m1"])

        parent.children = [child1, child2]

        result = parent.flatten()

        assert len(result) == 3
        assert result[0] is parent
        assert result[1] is child1
        assert result[2] is child2

    def test_flatten_nested_children(self):
        """Test flattening with nested children (depth-first order)."""
        root = self._create_message("user", "m1", [])
        child = self._create_message("assistant", "m2", ["m1"])
        grandchild = self._create_message("tool_use", "m3", ["m1", "m2"])

        child.children = [grandchild]
        root.children = [child]

        result = root.flatten()

        assert len(result) == 3
        # Depth-first order: root, child, grandchild
        assert result[0] is root
        assert result[1] is child
        assert result[2] is grandchild

    def test_flatten_multiple_branches(self):
        """Test flattening with multiple branches (depth-first order)."""
        root = self._create_message("user", "m1", [])
        branch1 = self._create_message("assistant", "m2", ["m1"])
        branch2 = self._create_message("assistant", "m3", ["m1"])
        leaf1 = self._create_message("tool_use", "m4", ["m1", "m2"])
        leaf2 = self._create_message("tool_use", "m5", ["m1", "m3"])

        branch1.children = [leaf1]
        branch2.children = [leaf2]
        root.children = [branch1, branch2]

        result = root.flatten()

        # Depth-first: root -> branch1 -> leaf1 -> branch2 -> leaf2
        assert len(result) == 5
        assert result[0] is root
        assert result[1] is branch1
        assert result[2] is leaf1
        assert result[3] is branch2
        assert result[4] is leaf2

    def test_flatten_all_single_root(self):
        """Test flatten_all with a single root message."""
        root = self._create_message("user", "m1", [])
        child = self._create_message("assistant", "m2", ["m1"])
        root.children = [child]

        result = TemplateMessage.flatten_all([root])

        assert len(result) == 2
        assert result[0] is root
        assert result[1] is child

    def test_flatten_all_multiple_roots(self):
        """Test flatten_all with multiple root messages."""
        root1 = self._create_message("user", "m1", [])
        child1 = self._create_message("assistant", "m2", ["m1"])
        root1.children = [child1]

        root2 = self._create_message("user", "m3", [])
        child2 = self._create_message("assistant", "m4", ["m3"])
        root2.children = [child2]

        result = TemplateMessage.flatten_all([root1, root2])

        assert len(result) == 4
        assert result[0] is root1
        assert result[1] is child1
        assert result[2] is root2
        assert result[3] is child2

    def test_flatten_all_empty_list(self):
        """Test flatten_all with an empty list."""
        result = TemplateMessage.flatten_all([])

        assert result == []

    def test_children_field_default_empty(self):
        """Test that children field defaults to empty list."""
        msg = self._create_message("user")

        assert msg.children == []

    def test_flatten_preserves_order(self):
        """Test that flatten preserves insertion order of children."""
        root = self._create_message("user", "m1", [])
        children = [
            self._create_message("assistant", f"m{i}", ["m1"]) for i in range(2, 7)
        ]
        root.children = children

        result = root.flatten()

        # First element is root, rest are children in order
        assert result[0] is root
        for i, child in enumerate(children):
            assert result[i + 1] is child


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

    def test_flatten_roundtrip_preserves_count(self):
        """Test that flatten of built tree gives same count as input."""
        # Create a manual tree and verify flatten returns all messages
        root = TemplateMessage(
            message_type="session",
            content_html="<p>Session</p>",
            formatted_timestamp="2025-06-14 10:00:00",
            css_class="session",
            raw_timestamp=None,
        )
        root.message_id = "session-1"
        root.ancestry = []

        user = TemplateMessage(
            message_type="user",
            content_html="<p>User</p>",
            formatted_timestamp="2025-06-14 10:00:01",
            css_class="user",
            raw_timestamp=None,
        )
        user.message_id = "d-1"
        user.ancestry = ["session-1"]

        assistant = TemplateMessage(
            message_type="assistant",
            content_html="<p>Assistant</p>",
            formatted_timestamp="2025-06-14 10:00:02",
            css_class="assistant",
            raw_timestamp=None,
        )
        assistant.message_id = "d-2"
        assistant.ancestry = ["session-1", "d-1"]

        tool = TemplateMessage(
            message_type="tool_use",
            content_html="<p>Tool</p>",
            formatted_timestamp="2025-06-14 10:00:03",
            css_class="tool_use",
            raw_timestamp=None,
        )
        tool.message_id = "d-3"
        tool.ancestry = ["session-1", "d-1", "d-2"]

        # Build tree manually
        assistant.children = [tool]
        user.children = [assistant]
        root.children = [user]

        # Flatten and verify
        flat = TemplateMessage.flatten_all([root])
        assert len(flat) == 4
        assert flat[0].message_id == "session-1"
        assert flat[1].message_id == "d-1"
        assert flat[2].message_id == "d-2"
        assert flat[3].message_id == "d-3"


if __name__ == "__main__":
    pytest.main([__file__])
