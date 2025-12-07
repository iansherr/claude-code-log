#!/usr/bin/env python3
"""Tests for renderer.py - HTML generation and message rendering.

These tests cover edge cases in the main renderer module including
tool formatting, system messages, timestamp handling, and message
rendering variations.
"""

import json
import tempfile
from pathlib import Path

import pytest

from claude_code_log.converter import load_transcript, generate_html


class TestRendererEdgeCases:
    """Tests for renderer.py edge cases."""

    def test_empty_messages_label(self):
        """Test format_children_label with 0 messages (line 1471)."""
        # Create a transcript with no renderable content
        empty_message = {
            "type": "queue-operation",
            "operation": "enqueue",  # Not rendered
            "timestamp": "2025-06-11T22:45:17.436Z",
            "sessionId": "test_session",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(empty_message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            # This should not raise even with no renderable messages
            html = generate_html(messages, "Test Empty")
            assert html  # Should still produce valid HTML
        finally:
            test_file_path.unlink()

    def test_single_timestamp_range(self):
        """Test timestamp range formatting with single timestamp (lines 3064-3065)."""
        # Session with only one message should show single timestamp, not range
        single_message = {
            "type": "user",
            "timestamp": "2025-06-11T22:45:17.436Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "human",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "user_001",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Single message"}],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(single_message) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Single")
            assert "Single message" in html
        finally:
            test_file_path.unlink()

    def test_tool_use_with_complex_params(self):
        """Test tool parameter rendering with complex nested values (lines 692-697)."""
        assistant_with_tool = {
            "type": "assistant",
            "timestamp": "2025-06-11T22:45:17.000Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "assistant_001",
            "message": {
                "id": "msg_001",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "TodoWrite",
                        "input": {
                            "todos": [
                                {"content": "Task 1", "status": "pending"},
                                {"content": "Task 2", "status": "completed"},
                            ]
                        },
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(assistant_with_tool) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test TodoWrite")
            assert "TodoWrite" in html
        finally:
            test_file_path.unlink()

    def test_multiedit_tool_rendering(self):
        """Test MultiEdit tool formatting (line 841)."""
        assistant_with_multiedit = {
            "type": "assistant",
            "timestamp": "2025-06-11T22:45:17.000Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "assistant_001",
            "message": {
                "id": "msg_001",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "MultiEdit",
                        "input": {
                            "file_path": "/tmp/test.py",
                            "edits": [
                                {"old_string": "foo", "new_string": "bar"},
                                {"old_string": "baz", "new_string": "qux"},
                            ],
                        },
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(assistant_with_multiedit) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test MultiEdit")
            assert "MultiEdit" in html
        finally:
            test_file_path.unlink()

    def test_ls_tool_rendering(self):
        """Test LS tool formatting (line 727-750)."""
        assistant_with_ls = {
            "type": "assistant",
            "timestamp": "2025-06-11T22:45:17.000Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "assistant_001",
            "message": {
                "id": "msg_001",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "LS",
                        "input": {"file_path": "/tmp"},
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(assistant_with_ls) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test LS")
            assert "LS" in html or "/tmp" in html
        finally:
            test_file_path.unlink()

    def test_tool_use_with_sidechain_agent(self):
        """Test tool use with sub-agent information (line 2233)."""
        assistant_with_task = {
            "type": "assistant",
            "timestamp": "2025-06-11T22:45:17.000Z",
            "parentUuid": None,
            "isSidechain": True,
            "agentId": "agent_12345",
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "assistant_001",
            "message": {
                "id": "msg_001",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test.txt"},
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(assistant_with_task) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Sidechain Tool")
            assert "Read" in html
        finally:
            test_file_path.unlink()


class TestSystemMessageEdgeCases:
    """Tests for system message edge cases."""

    def test_system_command_output(self):
        """Test system message with command output (line 2140-2142)."""
        system_command = {
            "type": "system",
            "timestamp": "2025-06-11T22:45:17.436Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "system_001",
            "level": "info",  # Required to avoid AttributeError
            "content": "<local-command-stdout>Command executed successfully</local-command-stdout>",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(system_command) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Command Output")
            # Should render the command output content
            assert "Command executed" in html or "command" in html.lower()
        finally:
            test_file_path.unlink()


class TestWriteAndEditToolRendering:
    """Tests for Write and Edit tool special formatting."""

    def test_write_tool_rendering(self):
        """Test Write tool formatting (line 2253)."""
        assistant_with_write = {
            "type": "assistant",
            "timestamp": "2025-06-11T22:45:17.000Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "assistant_001",
            "message": {
                "id": "msg_001",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "Write",
                        "input": {
                            "file_path": "/tmp/new_file.py",
                            "content": "print('Hello, World!')",
                        },
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(assistant_with_write) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Write")
            assert "Write" in html or "new_file" in html
        finally:
            test_file_path.unlink()

    def test_edit_tool_rendering(self):
        """Test Edit tool formatting (line 2244)."""
        assistant_with_edit = {
            "type": "assistant",
            "timestamp": "2025-06-11T22:45:17.000Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "1.0.0",
            "uuid": "assistant_001",
            "message": {
                "id": "msg_001",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "Edit",
                        "input": {
                            "file_path": "/tmp/test.py",
                            "old_string": "old code",
                            "new_string": "new code",
                        },
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(assistant_with_edit) + "\n")
            f.flush()
            test_file_path = Path(f.name)

        try:
            messages = load_transcript(test_file_path)
            html = generate_html(messages, "Test Edit")
            assert "Edit" in html
        finally:
            test_file_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
