#!/usr/bin/env python3
"""Tests for --compact rendering mode.

Compact mode filters out everything except user and assistant text messages:
no tools, no thinking, no system messages.
"""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_log.cli import main
from claude_code_log.converter import convert_jsonl_to, load_transcript
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    AssistantTranscriptEntry,
    SystemTranscriptEntry,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserTranscriptEntry,
)
from claude_code_log.renderer import _filter_compact, generate_template_messages


# -- Test data helpers --------------------------------------------------------


def _user_entry(
    text: str,
    session_id: str = "sess-001",
    timestamp: str = "2025-01-01T10:00:00Z",
    extra_content: list | None = None,
) -> dict:
    content: list = [{"type": "text", "text": text}]
    if extra_content:
        content.extend(extra_content)
    return {
        "type": "user",
        "timestamp": timestamp,
        "sessionId": session_id,
        "uuid": f"u-{uuid.uuid4().hex[:8]}",
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/tmp",
        "version": "1.0.0",
        "message": {"role": "user", "content": content},
    }


def _assistant_entry(
    text: str,
    session_id: str = "sess-001",
    timestamp: str = "2025-01-01T10:00:01Z",
    extra_content: list | None = None,
) -> dict:
    content: list = [{"type": "text", "text": text}]
    if extra_content:
        content.extend(extra_content)
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "sessionId": session_id,
        "uuid": f"a-{uuid.uuid4().hex[:8]}",
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/tmp",
        "version": "1.0.0",
        "message": {
            "id": f"msg_{uuid.uuid4().hex[:16]}",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "content": content,
        },
    }


def _system_entry(
    text: str,
    session_id: str = "sess-001",
    timestamp: str = "2025-01-01T10:00:02Z",
) -> dict:
    return {
        "type": "system",
        "timestamp": timestamp,
        "sessionId": session_id,
        "message": text,
    }


def _tool_use_item(name: str = "Bash", tool_id: str = "tool_001") -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": {"command": "echo hello"},
    }


def _tool_result_item(tool_id: str = "tool_001") -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": "hello",
        "is_error": False,
    }


def _thinking_item(text: str = "Let me think...") -> dict:
    return {"type": "thinking", "thinking": text}


def _write_jsonl(entries: list[dict], path: Path) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


# -- Unit tests for _filter_compact ------------------------------------------


class TestFilterCompact:
    """Test the _filter_compact function directly on parsed TranscriptEntry lists."""

    def test_keeps_user_and_assistant_text(self, tmp_path):
        """Plain user and assistant messages pass through."""
        entries = [
            _user_entry("Hello"),
            _assistant_entry("Hi there!"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 2
        assert isinstance(result[0], UserTranscriptEntry)
        assert isinstance(result[1], AssistantTranscriptEntry)

    def test_removes_system_entries(self, tmp_path):
        """System entries are dropped entirely."""
        entries = [
            _user_entry("Hello"),
            _system_entry("model changed"),
            _assistant_entry("Hi"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 2
        assert all(not isinstance(m, SystemTranscriptEntry) for m in result)

    def test_strips_tool_use_from_assistant(self, tmp_path):
        """Tool use items within assistant entries are stripped."""
        entries = [
            _user_entry("Do something"),
            _assistant_entry(
                "I'll run a command.",
                extra_content=[_tool_use_item()],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 2
        # Assistant entry should have text but no tool_use
        assistant = result[1]
        assert isinstance(assistant, AssistantTranscriptEntry)
        for item in assistant.message.content:
            assert not isinstance(item, ToolUseContent)

    def test_strips_tool_result_from_user(self, tmp_path):
        """Tool result items within user entries are stripped."""
        entries = [
            _user_entry(
                "Here's the result",
                extra_content=[_tool_result_item()],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 1
        user = result[0]
        assert isinstance(user, UserTranscriptEntry)
        for item in user.message.content:
            assert not isinstance(item, ToolResultContent)

    def test_strips_thinking_from_assistant(self, tmp_path):
        """Thinking items within assistant entries are stripped."""
        entries = [
            _assistant_entry(
                "Here's my answer.",
                extra_content=[_thinking_item()],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 1
        assistant = result[0]
        assert isinstance(assistant, AssistantTranscriptEntry)
        for item in assistant.message.content:
            assert not isinstance(item, ThinkingContent)

    def test_drops_assistant_with_only_tool_use(self, tmp_path):
        """Assistant entries with only tool_use (no text) are dropped entirely."""
        # Build an entry where the only content is a tool_use (no text at all)
        entry = _assistant_entry("placeholder", extra_content=[_tool_use_item()])
        # Remove the text item, keeping only tool_use
        entry["message"]["content"] = [_tool_use_item()]
        messages = load_transcript(_write_jsonl([entry], tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 0

    def test_removes_sidechain_entries(self, tmp_path):
        """Sidechain (subagent) entries are dropped."""
        sidechain_user = _user_entry("Subagent prompt")
        sidechain_user["isSidechain"] = True
        sidechain_assistant = _assistant_entry("Subagent reply")
        sidechain_assistant["isSidechain"] = True
        entries = [
            _user_entry("Main prompt"),
            sidechain_user,
            sidechain_assistant,
            _assistant_entry("Main reply"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        result = _filter_compact(messages)
        assert len(result) == 2
        for m in result:
            assert isinstance(m, (UserTranscriptEntry, AssistantTranscriptEntry))
            assert not m.isSidechain

    def test_does_not_mutate_original(self, tmp_path):
        """Filtering creates copies, not mutations of the original."""
        entries = [
            _assistant_entry(
                "Some text",
                extra_content=[_tool_use_item()],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))
        first = messages[0]
        assert isinstance(first, AssistantTranscriptEntry)
        original_content_count = len(first.message.content)
        _filter_compact(messages)
        assert len(first.message.content) == original_content_count


# -- Integration tests: generate_template_messages with compact ---------------


class TestCompactTemplateMessages:
    """Test compact mode through the full generate_template_messages pipeline."""

    def test_compact_removes_tool_messages(self, tmp_path):
        """Compact mode should not produce tool_use or tool_result TemplateMessages."""
        entries = [
            _user_entry("Run something"),
            _assistant_entry(
                "Running it.",
                extra_content=[_tool_use_item()],
                timestamp="2025-01-01T10:00:01Z",
            ),
            _user_entry(
                "",
                extra_content=[_tool_result_item()],
                timestamp="2025-01-01T10:00:02Z",
            ),
            _assistant_entry("Done!", timestamp="2025-01-01T10:00:03Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        root_messages, _, _ = generate_template_messages(messages, compact=True)
        # Flatten tree
        all_types = set()
        _collect_types(root_messages, all_types)
        assert "tool_use" not in all_types
        assert "tool_result" not in all_types
        assert "user" in all_types
        assert "assistant" in all_types

    def test_compact_removes_thinking_messages(self, tmp_path):
        """Compact mode should not produce thinking TemplateMessages."""
        entries = [
            _user_entry("Think about this"),
            _assistant_entry(
                "Here's my answer.",
                extra_content=[_thinking_item("deep thoughts")],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        root_messages, _, _ = generate_template_messages(messages, compact=True)
        all_types = set()
        _collect_types(root_messages, all_types)
        assert "thinking" not in all_types
        assert "assistant" in all_types

    def test_compact_preserves_session_headers(self, tmp_path):
        """Session headers are still generated in compact mode."""
        entries = [
            _user_entry("Hello"),
            _assistant_entry("Hi"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        root_messages, session_nav, _ = generate_template_messages(
            messages, compact=True
        )
        assert len(root_messages) >= 1
        assert root_messages[0].is_session_header
        assert len(session_nav) >= 1

    def test_compact_removes_bash_messages(self, tmp_path):
        """Compact mode removes bash-input and bash-output messages."""
        entries = [
            _user_entry("Check the directory"),
            # bash-input is parsed from user text containing <bash-input> tags
            _user_entry(
                "<bash-input>ls -la</bash-input>", timestamp="2025-01-01T10:00:01Z"
            ),
            _user_entry(
                "<bash-stdout>total 42\ndrwxr-xr-x</bash-stdout>",
                timestamp="2025-01-01T10:00:02Z",
            ),
            _assistant_entry("Here are the files.", timestamp="2025-01-01T10:00:03Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        root_messages, _, _ = generate_template_messages(messages, compact=True)
        all_types = set()
        _collect_types(root_messages, all_types)
        assert "bash-input" not in all_types
        assert "bash-output" not in all_types

    def test_compact_removes_slash_command_messages(self, tmp_path):
        """Compact mode removes slash command messages (e.g. /exit)."""
        entries = [
            _user_entry("Hello"),
            _assistant_entry("Hi", timestamp="2025-01-01T10:00:01Z"),
            # Slash command entries are user entries whose text matches /command
            _user_entry("/exit", timestamp="2025-01-01T10:00:02Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        root_messages, _, ctx = generate_template_messages(messages, compact=True)
        all_types = set()
        _collect_types(root_messages, all_types)
        # /exit should not appear as any type
        for msg in ctx.messages:
            assert "/exit" not in getattr(msg.content, "text", ""), (
                f"Slash command '/exit' found in compact output as {msg.type}"
            )

    def test_compact_removes_sidechain_messages(self, tmp_path):
        """Compact mode removes sidechain (subagent) messages entirely."""
        sidechain_user = _user_entry("Subagent task", timestamp="2025-01-01T10:00:01Z")
        sidechain_user["isSidechain"] = True
        sidechain_assistant = _assistant_entry(
            "Subagent result", timestamp="2025-01-01T10:00:02Z"
        )
        sidechain_assistant["isSidechain"] = True
        entries = [
            _user_entry("Do a task"),
            sidechain_user,
            sidechain_assistant,
            _assistant_entry("Task done.", timestamp="2025-01-01T10:00:03Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        root_messages, _, ctx = generate_template_messages(messages, compact=True)
        # No sidechain messages should remain
        for msg in ctx.messages:
            assert not msg.is_sidechain, f"Sidechain message found: {msg.type}"

    def test_compact_vs_normal_fewer_messages(self, tmp_path):
        """Compact mode produces fewer messages than normal mode."""
        entries = [
            _user_entry("Do something"),
            _assistant_entry(
                "OK, running bash.",
                extra_content=[_tool_use_item()],
                timestamp="2025-01-01T10:00:01Z",
            ),
            _user_entry(
                "",
                extra_content=[_tool_result_item()],
                timestamp="2025-01-01T10:00:02Z",
            ),
            _assistant_entry("All done!", timestamp="2025-01-01T10:00:03Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        normal_roots, _, normal_ctx = generate_template_messages(
            messages, compact=False
        )
        compact_roots, _, compact_ctx = generate_template_messages(
            messages, compact=True
        )

        normal_count = len(normal_ctx.messages)
        compact_count = len(compact_ctx.messages)
        assert compact_count < normal_count


# -- HTML rendering tests -----------------------------------------------------


class TestCompactHtmlRendering:
    """Test compact mode through the HTML renderer."""

    def test_compact_html_no_tool_divs(self, tmp_path):
        """Compact HTML should not contain tool_use or tool_result message divs."""
        entries = [
            _user_entry("Write a file"),
            _assistant_entry(
                "Creating the file.",
                extra_content=[_tool_use_item("Write", "tool_w01")],
                timestamp="2025-01-01T10:00:01Z",
            ),
            _user_entry(
                "",
                extra_content=[_tool_result_item("tool_w01")],
                timestamp="2025-01-01T10:00:02Z",
            ),
            _assistant_entry("File created!", timestamp="2025-01-01T10:00:03Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        renderer = HtmlRenderer()
        renderer.compact = True
        html = renderer.generate(messages, "Compact Test")

        assert "class='message tool_use" not in html
        assert "class='message tool_result" not in html
        assert "Write a file" in html
        assert "Creating the file" in html
        assert "File created!" in html

    def test_compact_html_no_thinking(self, tmp_path):
        """Compact HTML should not contain thinking message divs."""
        entries = [
            _user_entry("Explain something"),
            _assistant_entry(
                "Here's the explanation.",
                extra_content=[_thinking_item("I need to consider...")],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        renderer = HtmlRenderer()
        renderer.compact = True
        html = renderer.generate(messages, "Compact Test")

        assert "class='message thinking" not in html
        assert "I need to consider" not in html
        assert "Here's the explanation" in html


# -- Markdown rendering tests --------------------------------------------------


class TestCompactMarkdownRendering:
    """Test compact mode through the Markdown renderer."""

    def test_compact_markdown_no_tool_content(self, tmp_path):
        """Compact Markdown should not contain tool names or tool output."""
        entries = [
            _user_entry("Write a file"),
            _assistant_entry(
                "Creating the file.",
                extra_content=[_tool_use_item("Write", "tool_w01")],
                timestamp="2025-01-01T10:00:01Z",
            ),
            _user_entry(
                "",
                extra_content=[_tool_result_item("tool_w01")],
                timestamp="2025-01-01T10:00:02Z",
            ),
            _assistant_entry("File created!", timestamp="2025-01-01T10:00:03Z"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        renderer = MarkdownRenderer()
        renderer.compact = True
        md = renderer.generate(messages, "Compact Test")

        assert "Write a file" in md
        assert "Creating the file" in md
        assert "File created!" in md
        # Tool-specific content should be absent
        assert (
            "Write" not in md.split("File created!")[0].split("Creating the file.")[1]
        )

    def test_compact_markdown_no_thinking(self, tmp_path):
        """Compact Markdown should not contain thinking blocks."""
        entries = [
            _user_entry("Explain this"),
            _assistant_entry(
                "Here's the explanation.",
                extra_content=[_thinking_item("Let me reason about this...")],
            ),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        renderer = MarkdownRenderer()
        renderer.compact = True
        md = renderer.generate(messages, "Compact Test")

        assert "Here's the explanation" in md
        assert "Let me reason about this" not in md
        assert "Thinking" not in md

    def test_compact_markdown_preserves_session_structure(self, tmp_path):
        """Compact Markdown preserves session headers."""
        entries = [
            _user_entry("Hello"),
            _assistant_entry("Hi there"),
        ]
        messages = load_transcript(_write_jsonl(entries, tmp_path / "t.jsonl"))

        renderer = MarkdownRenderer()
        renderer.compact = True
        md = renderer.generate(messages, "Compact Test")

        assert "# Compact Test" in md
        assert "Hello" in md
        assert "Hi there" in md

    def test_compact_markdown_on_real_projects(self, tmp_path):
        """Compact Markdown works on real project data."""
        real_projects = Path(__file__).parent / "test_data" / "real_projects"
        if not real_projects.exists():
            pytest.skip("Real test projects not available")

        # Pick first JSONL file
        jsonl_files = []
        for project_dir in real_projects.iterdir():
            if project_dir.is_dir():
                jsonl_files.extend(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            pytest.skip("No JSONL files in real_projects")

        renderer = MarkdownRenderer()
        renderer.compact = True
        messages = load_transcript(jsonl_files[0])
        md = renderer.generate(messages, "Compact MD Test")
        assert md
        assert "# Compact MD Test" in md


# -- CLI tests ----------------------------------------------------------------


class TestCompactCLI:
    """Test the --compact CLI flag."""

    def test_compact_flag_accepted(self, tmp_path):
        """CLI accepts --compact without error."""
        entries = [
            _user_entry("Hello"),
            _assistant_entry("Hi there"),
        ]
        _write_jsonl(entries, tmp_path / "test.jsonl")
        output_file = tmp_path / "output.html"

        runner = CliRunner()
        result = runner.invoke(
            main,
            [str(tmp_path / "test.jsonl"), "-o", str(output_file), "--compact"],
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert output_file.exists()

    def test_compact_flag_filters_tools(self, tmp_path):
        """CLI --compact produces HTML without tool messages."""
        entries = [
            _user_entry("Run a command"),
            _assistant_entry(
                "Running it.",
                extra_content=[_tool_use_item("Bash", "tool_b01")],
                timestamp="2025-01-01T10:00:01Z",
            ),
            _user_entry(
                "",
                extra_content=[_tool_result_item("tool_b01")],
                timestamp="2025-01-01T10:00:02Z",
            ),
            _assistant_entry("Here's the output.", timestamp="2025-01-01T10:00:03Z"),
        ]
        _write_jsonl(entries, tmp_path / "test.jsonl")
        output_file = tmp_path / "output.html"

        runner = CliRunner()
        result = runner.invoke(
            main,
            [str(tmp_path / "test.jsonl"), "-o", str(output_file), "--compact"],
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"

        html = output_file.read_text(encoding="utf-8")
        assert "class='message tool_use" not in html
        assert "class='message tool_result" not in html
        assert "Run a command" in html
        assert "Here's the output" in html

    def test_compact_with_markdown_format(self, tmp_path):
        """CLI --compact works with --format md too."""
        entries = [
            _user_entry("Hello"),
            _assistant_entry(
                "Hi",
                extra_content=[_tool_use_item()],
            ),
        ]
        _write_jsonl(entries, tmp_path / "test.jsonl")
        output_file = tmp_path / "output.md"

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                str(tmp_path / "test.jsonl"),
                "-o",
                str(output_file),
                "--compact",
                "--format",
                "md",
            ],
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert output_file.exists()
        md = output_file.read_text(encoding="utf-8")
        assert "Hello" in md
        assert "Bash" not in md  # Tool name should not appear


# -- Real project data tests --------------------------------------------------

REAL_PROJECTS_DIR = Path(__file__).parent / "test_data" / "real_projects"


@pytest.fixture(scope="module")
def real_projects_path() -> Path:
    if not REAL_PROJECTS_DIR.exists():
        pytest.skip("Real test projects not available")
    return REAL_PROJECTS_DIR


class TestCompactRealProjects:
    """Test compact mode against real project data from test_data/real_projects/."""

    def _get_project_jsonl_files(self, projects_path: Path) -> list[Path]:
        """Get all JSONL files from real projects (top-level only, no subagents)."""
        files = []
        for project_dir in sorted(projects_path.iterdir()):
            if project_dir.is_dir():
                for f in project_dir.glob("*.jsonl"):
                    files.append(f)
        return files

    def test_compact_produces_valid_html(self, real_projects_path):
        """Compact mode generates valid HTML for every real project file."""
        files = self._get_project_jsonl_files(real_projects_path)
        assert files, "No JSONL files found in real_projects"

        renderer = HtmlRenderer()
        renderer.compact = True

        for jsonl_file in files:
            messages = load_transcript(jsonl_file)
            html = renderer.generate(messages, f"Compact: {jsonl_file.name}")
            assert html, f"Empty HTML for {jsonl_file.name}"
            assert "<!DOCTYPE html>" in html

    def test_compact_has_no_excluded_messages(self, real_projects_path):
        """Compact HTML from real projects contains no tool, thinking, bash, or sidechain divs."""
        files = self._get_project_jsonl_files(real_projects_path)

        renderer = HtmlRenderer()
        renderer.compact = True

        excluded_patterns = [
            "class='message tool_use",
            "class='message tool_result",
            "class='message thinking",
            "class='message bash-input",
            "class='message bash-output",
            "class='message user-slash-command",
            "class='message command-output",
            "class='message compacted-summary",
        ]

        for jsonl_file in files:
            messages = load_transcript(jsonl_file)
            html = renderer.generate(messages, "Compact Test")
            for pattern in excluded_patterns:
                count = html.count(pattern)
                msg_type = pattern.split("class='message ")[1]
                assert count == 0, (
                    f"{jsonl_file.name}: found {count} {msg_type} messages"
                )

    def test_compact_fewer_messages_than_normal(self, real_projects_path):
        """Compact mode produces strictly fewer messages for projects with tools."""
        files = self._get_project_jsonl_files(real_projects_path)

        for jsonl_file in files:
            messages = load_transcript(jsonl_file)
            _, _, normal_ctx = generate_template_messages(messages, compact=False)
            _, _, compact_ctx = generate_template_messages(messages, compact=True)

            normal_count = len(normal_ctx.messages)
            compact_count = len(compact_ctx.messages)

            # Real projects typically have many tool calls, so compact should
            # have fewer messages. Some tiny projects might only have text.
            assert compact_count <= normal_count, (
                f"{jsonl_file.name}: compact ({compact_count}) > normal ({normal_count})"
            )

    def test_compact_preserves_user_and_assistant(self, real_projects_path):
        """Compact mode keeps user and assistant messages from real projects."""
        files = self._get_project_jsonl_files(real_projects_path)

        for jsonl_file in files:
            messages = load_transcript(jsonl_file)
            root_messages, _, _ = generate_template_messages(messages, compact=True)
            all_types = set()
            _collect_types(root_messages, all_types)

            # Should only have user/assistant text types (plus session headers)
            non_header_types = all_types - {
                "session-header",
                "session_header",
            }
            allowed = {
                "user",
                "assistant",
                "user-steering",
                "user-memory",
            }
            unexpected = non_header_types - allowed
            assert not unexpected, (
                f"{jsonl_file.name}: unexpected types in compact: {unexpected}"
            )

    def test_compact_directory_mode(self, real_projects_path, tmp_path):
        """Compact mode works on a directory of JSONL files."""
        # Copy a project to tmp for isolated testing
        project_dirs = [d for d in real_projects_path.iterdir() if d.is_dir()]
        if not project_dirs:
            pytest.skip("No project dirs in real_projects")

        source = project_dirs[0]
        dest = tmp_path / source.name
        shutil.copytree(source, dest)

        output = convert_jsonl_to(
            "html",
            dest,
            use_cache=False,
            generate_individual_sessions=False,
            silent=True,
            compact=True,
        )
        html = output.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "class='message tool_use" not in html
        assert "class='message tool_result" not in html


# -- Test data file tests (representative_messages.jsonl) ----------------------


class TestCompactTestData:
    """Test compact mode on the bundled test data files."""

    @pytest.fixture
    def test_data_dir(self) -> Path:
        return Path(__file__).parent / "test_data"

    def test_compact_representative_messages(self, test_data_dir):
        """Compact mode on representative_messages.jsonl removes tools."""
        test_file = test_data_dir / "representative_messages.jsonl"
        messages = load_transcript(test_file)

        renderer = HtmlRenderer()
        renderer.compact = True
        html = renderer.generate(messages, "Compact Representative")

        # Should have user and assistant content
        assert "class='message user" in html
        assert "class='message assistant" in html
        # Should not have tool content
        assert "class='message tool_use" not in html
        assert "class='message tool_result" not in html

    def test_compact_sidechain(self, test_data_dir):
        """Compact mode on sidechain data removes tool messages."""
        test_file = test_data_dir / "sidechain.jsonl"
        if not test_file.exists():
            pytest.skip("sidechain.jsonl not available")
        messages = load_transcript(test_file)

        root_messages, _, _ = generate_template_messages(messages, compact=True)
        all_types = set()
        _collect_types(root_messages, all_types)
        assert "tool_use" not in all_types
        assert "tool_result" not in all_types


# -- Helpers ------------------------------------------------------------------


def _collect_types(messages: list, types: set[str]) -> None:
    """Recursively collect all message types from a tree of TemplateMessages."""
    for msg in messages:
        types.add(msg.type)
        if hasattr(msg, "children"):
            _collect_types(msg.children, types)
