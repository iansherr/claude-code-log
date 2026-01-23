#!/usr/bin/env python3
"""Tests for sidechain agent insertion and deduplication functionality."""

import tempfile
from pathlib import Path


from claude_code_log.converter import load_transcript
from claude_code_log.html.renderer import generate_html


def test_agent_insertion():
    """Test that agent messages are inserted after their referencing tool result."""
    # Create test data files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Write main transcript
        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            (Path(__file__).parent / "test_data" / "sidechain_main.jsonl").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        # Write agent transcript (must match agentId in main file)
        agent_file = tmpdir_path / "agent-e1c84ba5.jsonl"
        agent_file.write_text(
            (Path(__file__).parent / "test_data" / "sidechain_agent.jsonl").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        # Load transcript with agent insertion (agent files discovered automatically)
        messages = load_transcript(main_file)

        # Verify agent messages were inserted (3 main + 3 agent = 6 total)
        assert len(messages) == 6, f"Expected 6 messages, got {len(messages)}"

        # Find the tool result message (uuid 97965533-18f3-41b7-aa07-0c438f2a5893)
        tool_result_idx = next(
            i
            for i, msg in enumerate(messages)
            if getattr(msg, "uuid", None) == "97965533-18f3-41b7-aa07-0c438f2a5893"
        )

        # Verify agent messages come right after tool result
        # The agent messages should be inserted between tool_result and next main message
        assert tool_result_idx + 3 < len(messages), "Agent messages should be inserted"
        # Verify at least one message after tool result is from sidechain
        assert any(
            getattr(messages[i], "isSidechain", False)
            for i in range(tool_result_idx + 1, len(messages))
        )


def test_deduplication_task_result_vs_sidechain():
    """Test that sidechain assistant final message is dropped when it matches Task result."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Write deduplication test data
        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            (Path(__file__).parent / "test_data" / "dedup_main.jsonl").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        agent_file = tmpdir_path / "agent-e1c84ba5.jsonl"
        agent_file.write_text(
            (Path(__file__).parent / "test_data" / "dedup_agent.jsonl").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        # Load and render (agent files discovered automatically)
        messages = load_transcript(main_file)
        html = generate_html(messages, title="Test")

        # Verify deduplication occurred:
        # The sidechain assistant's final message should be dropped entirely
        # The actual content "I created the test file successfully" should only appear once
        # in the Task result, not in the sidechain assistant
        content_count = html.count("I created the test file successfully")
        assert content_count == 1, (
            f"Expected content to appear once, found {content_count} times"
        )


def test_no_deduplication_when_content_different():
    """Test that deduplication doesn't occur when Task result and sidechain differ."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create test data with different content
        # Note: agentId is just "ghi789", the filename is "agent-ghi789.jsonl"
        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            '{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"e:\\\\test","sessionId":"test-3","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"text","text":"Do something"}]},"uuid":"d-0","timestamp":"2025-01-15T12:00:00.000Z"}\n'
            '{"parentUuid":"d-0","isSidechain":false,"userType":"external","cwd":"e:\\\\test","sessionId":"test-3","version":"2.0.46","gitBranch":"main","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_01test1","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-3","name":"Task","input":{"prompt":"Do it"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":20}},"requestId":"req_01test1","type":"assistant","uuid":"d-1","timestamp":"2025-01-15T12:00:05.000Z"}\n'
            '{"parentUuid":"d-1","isSidechain":false,"userType":"external","cwd":"e:\\\\test","sessionId":"test-3","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-3","content":"Done A"}]},"uuid":"d-2","timestamp":"2025-01-15T12:00:15.000Z","toolUseResult":{"agentId":"ghi789","content":"Done A"},"agentId":"ghi789"}\n',
            encoding="utf-8",
        )

        agent_file = tmpdir_path / "agent-ghi789.jsonl"
        agent_file.write_text(
            '{"parentUuid":null,"isSidechain":true,"userType":"external","cwd":"e:\\\\test","sessionId":"test-3","version":"2.0.46","gitBranch":"main","agentId":"ghi789","type":"user","message":{"role":"user","content":[{"type":"text","text":"Do it"}]},"uuid":"agent-d-0","timestamp":"2025-01-15T12:00:06.000Z"}\n'
            '{"parentUuid":"agent-d-0","isSidechain":true,"userType":"external","cwd":"e:\\\\test","sessionId":"test-3","version":"2.0.46","gitBranch":"main","agentId":"ghi789","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_01testagent1","type":"message","role":"assistant","content":[{"type":"text","text":"Done B"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":5,"output_tokens":10}},"requestId":"req_01testagent1","type":"assistant","uuid":"agent-d-1","timestamp":"2025-01-15T12:00:14.000Z"}\n',
            encoding="utf-8",
        )

        messages = load_transcript(main_file)
        html = generate_html(messages, title="Test")

        # No deduplication should occur - both "Done A" and "Done B" should appear
        assert "Done A" in html
        assert "Done B" in html
        assert "Task summary" not in html


def test_agent_messages_marked_as_sidechain():
    """Test that agent messages are properly marked with sidechain class."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            (Path(__file__).parent / "test_data" / "sidechain_main.jsonl").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        agent_file = tmpdir_path / "agent-e1c84ba5.jsonl"
        agent_file.write_text(
            (Path(__file__).parent / "test_data" / "sidechain_agent.jsonl").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        messages = load_transcript(main_file)
        html = generate_html(messages, title="Test")

        # Agent messages should have sidechain class
        assert (
            "class='message assistant sidechain" in html
            or "class='message user sidechain" in html
        )

        # Verify sidechain messages are indented (have ancestry classes)
        assert (
            "d-2" in html
        )  # Tool result ID should appear in ancestry for sidechain messages


def test_sidechain_tool_results_rendered():
    """Test that tool results from sidechain agents are rendered (not skipped).

    Regression test for bug where all sidechain user messages were skipped,
    which incorrectly also skipped tool results that come in user messages.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a main file with Task tool use and result
        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            '{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"text","text":"Search for files"}]},"uuid":"u-0","timestamp":"2025-01-15T14:00:00.000Z"}\n'
            '{"parentUuid":"u-0","isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_main","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-glob","name":"Task","input":{"prompt":"Find test files"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":50}},"requestId":"req_main","type":"assistant","uuid":"a-0","timestamp":"2025-01-15T14:00:05.000Z"}\n'
            '{"parentUuid":"a-0","isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-glob","content":"Found files: test.py"}]},"uuid":"u-1","timestamp":"2025-01-15T14:00:20.000Z","toolUseResult":{"agentId":"glob-agent","content":"Found files: test.py"},"agentId":"glob-agent"}\n',
            encoding="utf-8",
        )

        # Create agent file with tool use (Glob) and its result
        agent_file = tmpdir_path / "agent-glob-agent.jsonl"
        agent_file.write_text(
            # Sidechain user prompt (should be skipped in output)
            '{"parentUuid":null,"isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","agentId":"glob-agent","type":"user","message":{"role":"user","content":[{"type":"text","text":"Find test files"}]},"uuid":"agent-u-0","timestamp":"2025-01-15T14:00:06.000Z"}\n'
            # Assistant uses Glob tool
            '{"parentUuid":"agent-u-0","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","agentId":"glob-agent","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_agent","type":"message","role":"assistant","content":[{"type":"tool_use","id":"glob-123","name":"Glob","input":{"pattern":"**/*.py"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":50,"output_tokens":25}},"requestId":"req_agent","type":"assistant","uuid":"agent-a-0","timestamp":"2025-01-15T14:00:08.000Z"}\n'
            # Tool result comes in a sidechain user message - THIS SHOULD BE RENDERED
            '{"parentUuid":"agent-a-0","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","agentId":"glob-agent","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"glob-123","content":"/workspace/test.py"}]},"uuid":"agent-u-1","timestamp":"2025-01-15T14:00:10.000Z"}\n'
            # Final assistant message
            '{"parentUuid":"agent-u-1","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-tool-results","version":"2.0.46","gitBranch":"main","agentId":"glob-agent","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_agent_final","type":"message","role":"assistant","content":[{"type":"text","text":"Found files: test.py"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":60,"output_tokens":15}},"requestId":"req_agent_final","type":"assistant","uuid":"agent-a-1","timestamp":"2025-01-15T14:00:15.000Z"}\n',
            encoding="utf-8",
        )

        messages = load_transcript(main_file)
        html = generate_html(messages, title="Test")

        # The sidechain Glob tool use should be rendered
        assert "🛠️ Glob" in html or "Glob" in html

        # The sidechain tool RESULT should be rendered (was the bug - it was skipped)
        # The content "/workspace/test.py" should appear in the output
        assert "/workspace/test.py" in html, (
            "Sidechain tool result content should be rendered"
        )

        # The sidechain prompt text "Find test files" should NOT be rendered
        # as a separate user message (only the tool results should come through)
        # No sidechain user messages with text content should be rendered
        # (tool results are rendered as tool_result, not as user messages)
        assert (
            "<div class='content'><p>Find test files</p></div>" not in html
            or "class='message user sidechain" not in html
        ), "Sidechain user prompts should be skipped"


def test_explore_agent_no_user_prompt():
    """Test Explore-type agents that start with assistant (no user prompt).

    Real-world pattern from -src-deep-manifest project: Explore agents don't have
    a user prompt message at the start - they begin directly with assistant messages.
    This differs from Plan-type agents (like in coderabbit) which start with a user
    prompt that duplicates the Task input.

    The cleanup should handle both patterns correctly:
    - Plan agents: Remove first UserTextMessage (duplicate prompt)
    - Explore agents: No UserTextMessage to remove, just process normally
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create main session with Task tool_use (Explore type)
        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            # User request
            '{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"text","text":"How does the ZIP processing work?"}]},"uuid":"u-0","timestamp":"2025-01-15T15:24:45.000Z"}\n'
            # Assistant invokes Task (Explore subagent)
            '{"parentUuid":"u-0","isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_main","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-explore","name":"Task","input":{"prompt":"Explore ZIP processing","subagent_type":"Explore"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":50}},"requestId":"req_main","type":"assistant","uuid":"a-0","timestamp":"2025-01-15T15:24:47.000Z"}\n'
            # Task result (triggers agent loading)
            '{"parentUuid":"a-0","isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-explore","content":"ZIP files are processed recursively..."}]},"uuid":"u-1","timestamp":"2025-01-15T15:25:00.000Z","toolUseResult":{"agentId":"explore-agent","content":"ZIP files are processed recursively..."},"agentId":"explore-agent"}\n'
        )

        # Create Explore-type agent file: starts with ASSISTANT (no user prompt)
        # This matches real data from -src-deep-manifest/agent-c8d9b115.jsonl
        agent_file = tmpdir_path / "agent-explore-agent.jsonl"
        agent_file.write_text(
            # First message is assistant (NOT user) - Explore agents don't echo the prompt
            '{"parentUuid":null,"isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","agentId":"explore-agent","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_explore_1","type":"message","role":"assistant","content":[{"type":"text","text":"Let me search for ZIP-related code."},{"type":"tool_use","id":"grep-1","name":"Grep","input":{"pattern":"zip"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":50,"output_tokens":30}},"requestId":"req_explore_1","type":"assistant","uuid":"explore-a-0","timestamp":"2025-01-15T15:24:51.000Z"}\n'
            # User provides Grep result
            '{"parentUuid":"explore-a-0","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","agentId":"explore-agent","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"grep-1","content":"deep_manifest.py:42: def process_zip()"}]},"uuid":"explore-u-0","timestamp":"2025-01-15T15:24:52.000Z"}\n'
            # Assistant continues with another tool
            '{"parentUuid":"explore-u-0","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","agentId":"explore-agent","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_explore_2","type":"message","role":"assistant","content":[{"type":"text","text":"Found it. Let me read the file."},{"type":"tool_use","id":"read-1","name":"Read","input":{"file_path":"deep_manifest.py"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":80,"output_tokens":40}},"requestId":"req_explore_2","type":"assistant","uuid":"explore-a-1","timestamp":"2025-01-15T15:24:53.000Z"}\n'
            # User provides Read result
            '{"parentUuid":"explore-a-1","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","agentId":"explore-agent","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"read-1","content":"def process_zip(path):\\n    with zipfile.ZipFile(path) as zf:\\n        ..."}]},"uuid":"explore-u-1","timestamp":"2025-01-15T15:24:54.000Z"}\n'
            # Final assistant summary
            '{"parentUuid":"explore-u-1","isSidechain":true,"userType":"external","cwd":"/workspace","sessionId":"test-explore","version":"2.0.46","gitBranch":"main","agentId":"explore-agent","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_explore_final","type":"message","role":"assistant","content":[{"type":"text","text":"ZIP files are processed recursively..."}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":50}},"requestId":"req_explore_final","type":"assistant","uuid":"explore-a-2","timestamp":"2025-01-15T15:24:58.000Z"}\n'
        )

        messages = load_transcript(main_file)
        html = generate_html(messages, title="Test Explore Agent")

        # Verify sidechain tool results are rendered (Grep and Read results)
        assert "deep_manifest.py:42" in html, "Grep tool result should be rendered"
        assert "def process_zip" in html, "Read tool result should be rendered"

        # Verify assistant messages are rendered
        assert "Let me search for ZIP-related code" in html
        assert "Found it. Let me read the file" in html

        # The final assistant message matches Task result, should become dedup notice
        # (but the intermediate messages should still be visible)


def test_multiple_agent_invocations():
    """Test handling of multiple Task invocations in same session."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create scenario with two Task invocations
        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            '{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"text","text":"Do two things"}]},"uuid":"d-0","timestamp":"2025-01-15T13:00:00.000Z"}\n'
            '{"parentUuid":"d-0","isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_01","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-4a","name":"Task","input":{"prompt":"First task"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":50}},"requestId":"req_01","type":"assistant","uuid":"d-1","timestamp":"2025-01-15T13:00:05.000Z"}\n'
            '{"parentUuid":"d-1","isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-4a","content":"First done"}]},"uuid":"d-2","timestamp":"2025-01-15T13:00:15.000Z","toolUseResult":{"status":"completed","agentId":"first","content":[{"type":"text","text":"First done"}]},"agentId":"first"}\n'
            '{"parentUuid":"d-2","isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_02","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-4b","name":"Task","input":{"prompt":"Second task"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":150,"output_tokens":60}},"requestId":"req_02","type":"assistant","uuid":"d-3","timestamp":"2025-01-15T13:00:20.000Z"}\n'
            '{"parentUuid":"d-3","isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-4b","content":"Second done"}]},"uuid":"d-4","timestamp":"2025-01-15T13:00:30.000Z","toolUseResult":{"status":"completed","agentId":"second","content":[{"type":"text","text":"Second done"}]},"agentId":"second"}\n',
            encoding="utf-8",
        )

        (tmpdir_path / "agent-first.jsonl").write_text(
            '{"parentUuid":null,"isSidechain":true,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","agentId":"first","type":"user","message":{"role":"user","content":[{"type":"text","text":"First task"}]},"uuid":"agent-d-0","timestamp":"2025-01-15T13:00:06.000Z"}\n'
            '{"parentUuid":"agent-d-0","isSidechain":true,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","agentId":"first","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_agent_01","type":"message","role":"assistant","content":[{"type":"text","text":"First done"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":50,"output_tokens":25}},"requestId":"req_agent_01","type":"assistant","uuid":"agent-d-1","timestamp":"2025-01-15T13:00:14.000Z"}\n',
            encoding="utf-8",
        )

        (tmpdir_path / "agent-second.jsonl").write_text(
            '{"parentUuid":null,"isSidechain":true,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","agentId":"second","type":"user","message":{"role":"user","content":[{"type":"text","text":"Second task"}]},"uuid":"agent2-d-0","timestamp":"2025-01-15T13:00:21.000Z"}\n'
            '{"parentUuid":"agent2-d-0","isSidechain":true,"userType":"external","cwd":"/workspace/test","sessionId":"test-4","version":"2.0.46","gitBranch":"main","agentId":"second","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_agent_02","type":"message","role":"assistant","content":[{"type":"text","text":"Second done"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":55,"output_tokens":30}},"requestId":"req_agent_02","type":"assistant","uuid":"agent2-d-1","timestamp":"2025-01-15T13:00:29.000Z"}\n',
            encoding="utf-8",
        )

        messages = load_transcript(main_file)

        # Should have 5 main + 2 + 2 agent messages = 9 total
        assert len(messages) == 9

        # Verify both agent sequences are inserted correctly
        html = generate_html(messages, title="Test")
        assert "First done" in html
        assert "Second done" in html


def test_task_output_structured_content_markdown():
    """Test that Task results with structured content render as markdown.

    Real-world Task tool results have structured content like:
    [{"type": "text", "text": "## Summary\n\nMarkdown content..."}]

    This tests that:
    1. Structured content is extracted and parsed into TaskOutput
    2. has_markdown returns True for TaskOutput
    3. HTML output includes task-result class with rendered markdown headers
    """
    import json
    from claude_code_log.renderer import generate_template_messages
    from claude_code_log.models import ToolResultMessage, TaskOutput

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Task result with structured content containing markdown
        markdown_content = (
            "## Summary\n\n"
            "This is the agent's response with **bold** and `code`.\n\n"
            "### Section 1\n\n"
            "Some content here."
        )

        main_file = tmpdir_path / "main.jsonl"
        main_file.write_text(
            # User request
            '{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-md","version":"2.0.55","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"text","text":"Explore the codebase"}]},"uuid":"u-0","timestamp":"2025-01-15T15:00:00.000Z"}\n'
            # Assistant invokes Task
            '{"parentUuid":"u-0","isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-md","version":"2.0.55","gitBranch":"main","message":{"model":"claude-opus-4-5-20251101","id":"msg_main","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-md","name":"Task","input":{"prompt":"Explore this","subagent_type":"Explore"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":50}},"requestId":"req_main","type":"assistant","uuid":"a-0","timestamp":"2025-01-15T15:00:05.000Z"}\n'
            # Task result with STRUCTURED content (list of text items)
            + '{"parentUuid":"a-0","isSidechain":false,"userType":"external","cwd":"/workspace","sessionId":"test-md","version":"2.0.55","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-md","content":'
            + json.dumps([{"type": "text", "text": markdown_content}])
            + '}]},"uuid":"u-1","timestamp":"2025-01-15T15:00:30.000Z"}\n'
        )

        messages = load_transcript(main_file)

        # Generate template messages to check TaskOutput parsing
        root_messages, _, _ = generate_template_messages(messages)

        # Flatten to find all messages
        def flatten(msg):
            yield msg
            for child in msg.children:
                yield from flatten(child)

        # Find the Task tool result
        task_result = None
        for root in root_messages:
            for msg in flatten(root):
                if (
                    isinstance(msg.content, ToolResultMessage)
                    and msg.content.tool_name == "Task"
                ):
                    task_result = msg.content
                    break

        assert task_result is not None, "Task tool result should be found"

        # Verify it's parsed as TaskOutput (not raw ToolResultContent)
        assert isinstance(task_result.output, TaskOutput), (
            f"Expected TaskOutput, got {type(task_result.output).__name__}"
        )

        # Verify has_markdown returns True
        assert task_result.has_markdown, "has_markdown should be True for TaskOutput"

        # Verify the result text is preserved
        assert "## Summary" in task_result.output.result
        assert "### Section 1" in task_result.output.result

        # Generate HTML and verify markdown rendering
        html = generate_html(messages, title="Test Markdown")

        # Should have task-result CSS class
        assert "task-result" in html, "HTML should include task-result class"

        # Should have rendered markdown headers (h2, h3)
        assert "<h2>" in html, "Markdown ## should render as <h2>"
        assert "<h3>" in html, "Markdown ### should render as <h3>"

        # Should include the rendered content
        assert "<strong>bold</strong>" in html or "<b>bold</b>" in html
        assert "<code>code</code>" in html


def test_subagents_directory_structure():
    """Test that agent files are loaded from {session}/subagents/ directory (Claude Code 2.1.17+).

    The new directory structure is:
      session-uuid.jsonl
      session-uuid/subagents/agent-*.jsonl

    This test verifies the new structure works while maintaining backward compatibility.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create session file with UUID-style name (mimics real Claude Code structure)
        session_uuid = "29ccd257-68b1-427f-ae5f-6524b7cb6f20"
        main_file = tmpdir_path / f"{session_uuid}.jsonl"

        # Create the new subagents directory structure
        subagents_dir = tmpdir_path / session_uuid / "subagents"
        subagents_dir.mkdir(parents=True)

        # Write main transcript referencing an agent
        main_file.write_text(
            '{"parentUuid":null,"isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-subagents","version":"2.1.17","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"text","text":"Do something with an agent"}]},"uuid":"s-0","timestamp":"2025-01-15T13:00:00.000Z"}\n'
            '{"parentUuid":"s-0","isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-subagents","version":"2.1.17","gitBranch":"main","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_01","type":"message","role":"assistant","content":[{"type":"tool_use","id":"task-sub","name":"Task","input":{"prompt":"Subagent task"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":100,"output_tokens":50}},"requestId":"req_01","type":"assistant","uuid":"s-1","timestamp":"2025-01-15T13:00:05.000Z"}\n'
            '{"parentUuid":"s-1","isSidechain":false,"userType":"external","cwd":"/workspace/test","sessionId":"test-subagents","version":"2.1.17","gitBranch":"main","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"task-sub","content":"Subagent completed the task"}]},"uuid":"s-2","timestamp":"2025-01-15T13:00:15.000Z","toolUseResult":{"status":"completed","agentId":"a2271d1","content":[{"type":"text","text":"Subagent completed the task"}]},"agentId":"a2271d1"}\n',
            encoding="utf-8",
        )

        # Write agent file in the NEW subagents directory (not in parent dir)
        agent_file = subagents_dir / "agent-a2271d1.jsonl"
        agent_file.write_text(
            '{"parentUuid":null,"isSidechain":true,"userType":"external","cwd":"/workspace/test","sessionId":"test-subagents","version":"2.1.17","gitBranch":"main","agentId":"a2271d1","type":"user","message":{"role":"user","content":[{"type":"text","text":"Subagent task"}]},"uuid":"agent-s-0","timestamp":"2025-01-15T13:00:06.000Z"}\n'
            '{"parentUuid":"agent-s-0","isSidechain":true,"userType":"external","cwd":"/workspace/test","sessionId":"test-subagents","version":"2.1.17","gitBranch":"main","agentId":"a2271d1","message":{"model":"claude-sonnet-4-5-20250929","id":"msg_agent_01","type":"message","role":"assistant","content":[{"type":"text","text":"Subagent completed the task"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":50,"output_tokens":25}},"requestId":"req_agent_01","type":"assistant","uuid":"agent-s-1","timestamp":"2025-01-15T13:00:14.000Z"}\n',
            encoding="utf-8",
        )

        # Verify agent file is NOT in the legacy location
        legacy_agent = tmpdir_path / "agent-a2271d1.jsonl"
        assert not legacy_agent.exists(), (
            "Agent should NOT be in legacy location for this test"
        )

        # Load transcript - should find agent in subagents directory
        messages = load_transcript(main_file)

        # Should have 3 main + 2 agent messages = 5 total
        assert len(messages) == 5, f"Expected 5 messages, got {len(messages)}"

        # Verify agent messages were loaded and marked as sidechain
        sidechain_count = sum(1 for m in messages if getattr(m, "isSidechain", False))
        assert sidechain_count == 2, (
            f"Expected 2 sidechain messages, got {sidechain_count}"
        )

        # Verify HTML generation works with subagent messages
        html = generate_html(messages, title="Test Subagents Directory")
        assert "Subagent completed the task" in html
