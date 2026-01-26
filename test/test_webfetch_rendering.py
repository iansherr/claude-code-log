#!/usr/bin/env python3
"""Test cases for WebFetch tool rendering functionality."""

import json
import tempfile
from pathlib import Path

import pytest
from claude_code_log.converter import load_transcript
from claude_code_log.factories.tool_factory import parse_webfetch_output
from claude_code_log.html import format_webfetch_input, format_webfetch_output
from claude_code_log.html.renderer import HtmlRenderer, generate_html
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    MessageMeta,
    ToolResultContent,
    ToolUseMessage,
    WebFetchInput,
    WebFetchOutput,
)
from claude_code_log.renderer import TemplateMessage


class TestWebFetchInput:
    """Test WebFetch input model and formatting."""

    def test_webfetch_input_creation(self):
        """Test basic WebFetchInput model creation."""
        webfetch_input = WebFetchInput(
            url="https://example.com/api",
            prompt="Extract the main content",
        )
        assert webfetch_input.url == "https://example.com/api"
        assert webfetch_input.prompt == "Extract the main content"

    def test_format_webfetch_input_short_prompt(self):
        """Test formatting with short prompt (under 100 chars)."""
        webfetch_input = WebFetchInput(
            url="https://example.com",
            prompt="Get the title",
        )
        html = format_webfetch_input(webfetch_input)
        # Short prompt should not show prompt content
        assert html == ""

    def test_format_webfetch_input_long_prompt(self):
        """Test formatting with long prompt (over 100 chars)."""
        long_prompt = "Extract all the information about the API endpoints, including parameters, return types, and examples. Also include any authentication requirements."
        webfetch_input = WebFetchInput(
            url="https://api.example.com/docs",
            prompt=long_prompt,
        )
        html = format_webfetch_input(webfetch_input)
        # Long prompt should show prompt content
        assert "webfetch-prompt" in html
        assert long_prompt in html

    def test_format_webfetch_input_html_escaping(self):
        """Test that prompt content is properly HTML escaped."""
        webfetch_input = WebFetchInput(
            url="https://example.com",
            prompt="A very long prompt " * 10 + "<script>alert('xss')</script>",
        )
        html = format_webfetch_input(webfetch_input)
        assert "&lt;script&gt;" in html
        assert "<script>" not in html


class TestWebFetchOutput:
    """Test WebFetch output parsing and formatting."""

    def test_parse_webfetch_output_full_data(self):
        """Test parsing WebFetch output with full toolUseResult data."""
        tool_result = ToolResultContent(
            type="tool_result",
            tool_use_id="toolu_test",
            content="# Example Result\n\nThis is the fetched content.",
        )
        tool_use_result = {
            "url": "https://example.com/page",
            "result": "# Example Result\n\nThis is the fetched content.",
            "bytes": 12345,
            "code": 200,
            "codeText": "OK",
            "durationMs": 1500,
        }
        output = parse_webfetch_output(tool_result, None, tool_use_result)

        assert output is not None
        assert output.url == "https://example.com/page"
        assert output.result == "# Example Result\n\nThis is the fetched content."
        assert output.bytes == 12345
        assert output.code == 200
        assert output.code_text == "OK"
        assert output.duration_ms == 1500

    def test_parse_webfetch_output_minimal_data(self):
        """Test parsing WebFetch output with minimal toolUseResult data."""
        tool_result = ToolResultContent(
            type="tool_result",
            tool_use_id="toolu_test",
            content="Content here",
        )
        tool_use_result = {
            "url": "https://example.com",
            "result": "Content here",
        }
        output = parse_webfetch_output(tool_result, None, tool_use_result)

        assert output is not None
        assert output.url == "https://example.com"
        assert output.result == "Content here"
        assert output.bytes is None
        assert output.code is None

    def test_parse_webfetch_output_no_tool_use_result(self):
        """Test parsing WebFetch output without toolUseResult returns None."""
        tool_result = ToolResultContent(
            type="tool_result",
            tool_use_id="toolu_test",
            content="Some content",
        )
        output = parse_webfetch_output(tool_result, None, None)
        assert output is None

    def test_parse_webfetch_output_missing_required_fields(self):
        """Test parsing WebFetch output with missing url/result returns None."""
        tool_result = ToolResultContent(
            type="tool_result",
            tool_use_id="toolu_test",
            content="Content",
        )
        # Missing result field
        tool_use_result = {"url": "https://example.com"}
        output = parse_webfetch_output(tool_result, None, tool_use_result)
        assert output is None

        # Missing url field
        tool_use_result = {"result": "Content"}
        output = parse_webfetch_output(tool_result, None, tool_use_result)
        assert output is None

    def test_format_webfetch_output_full_metadata(self):
        """Test formatting WebFetch output with full metadata badge."""
        output = WebFetchOutput(
            url="https://example.com/api",
            result="# API Documentation\n\nThis is the content.",
            bytes=5678,
            code=200,
            code_text="OK",
            duration_ms=2500,
        )
        html = format_webfetch_output(output)

        # Check metadata badge
        assert "webfetch-meta" in html
        assert "webfetch-status-success" in html  # Status class for 200
        assert ">200<" in html  # Status code in span
        assert "5.5 KB" in html  # bytes formatted
        assert "2.5s" in html  # duration formatted

        # Check result rendered as markdown (via collapsible)
        assert "API Documentation" in html
        assert "webfetch-result" in html  # Container class

    def test_format_webfetch_output_error_status(self):
        """Test formatting WebFetch output with error status."""
        output = WebFetchOutput(
            url="https://example.com/missing",
            result="Page not found",
            code=404,
            code_text="Not Found",
        )
        html = format_webfetch_output(output)

        assert ">404<" in html  # Status code in span
        assert "webfetch-status-error" in html  # Error status class

    def test_format_webfetch_output_minimal_metadata(self):
        """Test formatting WebFetch output with minimal metadata."""
        output = WebFetchOutput(
            url="https://example.com",
            result="Simple content",
        )
        html = format_webfetch_output(output)

        # Should still have result content
        assert "Simple content" in html
        # Should have the result container class
        assert "webfetch-result" in html


class TestWebFetchHtmlRenderer:
    """Test WebFetch HTML renderer methods."""

    def test_html_renderer_format_webfetch_input(self):
        """Test HtmlRenderer.format_WebFetchInput method."""
        renderer = HtmlRenderer()
        webfetch_input = WebFetchInput(
            url="https://example.com",
            prompt="Short prompt",
        )
        tool_msg = ToolUseMessage(
            MessageMeta.empty(),
            input=webfetch_input,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_msg)
        html = renderer.format_WebFetchInput(webfetch_input, msg)
        # Short prompt returns empty
        assert html == ""

    def test_html_renderer_format_webfetch_output(self):
        """Test HtmlRenderer.format_WebFetchOutput method."""
        renderer = HtmlRenderer()
        output = WebFetchOutput(
            url="https://example.com",
            result="Fetched content here",
            code=200,
            code_text="OK",
        )
        from claude_code_log.models import ToolResultMessage

        tool_result_msg = ToolResultMessage(
            MessageMeta.empty(),
            output=output,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_result_msg)
        html = renderer.format_WebFetchOutput(output, msg)
        assert "Fetched content here" in html
        assert "webfetch-status-success" in html  # 200 renders with success class

    def test_html_renderer_title_webfetch_input(self):
        """Test HtmlRenderer.title_WebFetchInput method."""
        renderer = HtmlRenderer()
        webfetch_input = WebFetchInput(
            url="https://api.github.com/repos/owner/repo",
            prompt="Get the repository info",
        )
        tool_msg = ToolUseMessage(
            MessageMeta.empty(),
            input=webfetch_input,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_msg)
        title = renderer.title_WebFetchInput(webfetch_input, msg)
        assert "🌐" in title
        assert "https://api.github.com/repos/owner/repo" in title


class TestWebFetchMarkdownRenderer:
    """Test WebFetch Markdown renderer methods."""

    def test_markdown_renderer_format_webfetch_input_short(self):
        """Test MarkdownRenderer.format_WebFetchInput with short prompt."""
        renderer = MarkdownRenderer()
        webfetch_input = WebFetchInput(
            url="https://example.com",
            prompt="Short",
        )
        tool_msg = ToolUseMessage(
            MessageMeta.empty(),
            input=webfetch_input,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_msg)
        md = renderer.format_WebFetchInput(webfetch_input, msg)
        assert md == ""

    def test_markdown_renderer_format_webfetch_input_long(self):
        """Test MarkdownRenderer.format_WebFetchInput with long prompt."""
        renderer = MarkdownRenderer()
        long_prompt = "A " * 60 + "very long prompt"  # > 100 chars
        webfetch_input = WebFetchInput(
            url="https://example.com",
            prompt=long_prompt,
        )
        tool_msg = ToolUseMessage(
            MessageMeta.empty(),
            input=webfetch_input,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_msg)
        md = renderer.format_WebFetchInput(webfetch_input, msg)
        assert "```" in md  # Code fence
        assert long_prompt in md

    def test_markdown_renderer_format_webfetch_output(self):
        """Test MarkdownRenderer.format_WebFetchOutput method."""
        renderer = MarkdownRenderer()
        output = WebFetchOutput(
            url="https://example.com",
            result="# Heading\n\nParagraph text",
        )
        from claude_code_log.models import ToolResultMessage

        tool_result_msg = ToolResultMessage(
            MessageMeta.empty(),
            output=output,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_result_msg)
        md = renderer.format_WebFetchOutput(output, msg)
        assert "<details>" in md
        assert "Result" in md
        assert "> # Heading" in md  # Blockquoted

    def test_markdown_renderer_title_webfetch_input(self):
        """Test MarkdownRenderer.title_WebFetchInput method."""
        renderer = MarkdownRenderer()
        webfetch_input = WebFetchInput(
            url="https://docs.python.org",
            prompt="Find info",
        )
        tool_msg = ToolUseMessage(
            MessageMeta.empty(),
            input=webfetch_input,
            tool_use_id="toolu_webfetch",
            tool_name="WebFetch",
        )
        msg = TemplateMessage(tool_msg)
        title = renderer.title_WebFetchInput(webfetch_input, msg)
        assert "🌐" in title
        assert "WebFetch" in title
        assert "`https://docs.python.org`" in title


class TestWebFetchIntegration:
    """Test WebFetch end-to-end integration."""

    def test_webfetch_integration_with_full_message(self):
        """Test WebFetch tool use and result in full message rendering."""
        # Create WebFetch tool_use message
        tool_use_message = {
            "type": "assistant",
            "timestamp": "2025-06-14T10:00:00Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "2.0.37",
            "uuid": "webfetch_001",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5-20250929",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_webfetch_test",
                        "name": "WebFetch",
                        "input": {
                            "url": "https://docs.github.com/en/rest/pulls/comments",
                            "prompt": "What fields are returned in the API response?",
                        },
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
            },
        }

        # Create WebFetch tool_result message with toolUseResult
        tool_result_message = {
            "type": "user",
            "timestamp": "2025-06-14T10:00:05Z",
            "parentUuid": "webfetch_001",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "2.0.37",
            "uuid": "webfetch_002",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_webfetch_test",
                        "content": "# API Fields\n\n- `path` - The file path\n- `line` - The line number",
                    }
                ],
            },
            "toolUseResult": {
                "url": "https://docs.github.com/en/rest/pulls/comments",
                "result": "# API Fields\n\n- `path` - The file path\n- `line` - The line number",
                "bytes": 440193,
                "code": 200,
                "codeText": "OK",
                "durationMs": 5180,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jsonl_file = temp_path / "webfetch_test.jsonl"

            with open(jsonl_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(tool_use_message) + "\n")
                f.write(json.dumps(tool_result_message) + "\n")

            messages = load_transcript(jsonl_file)
            html = generate_html(messages, "WebFetch Test")

            # Check WebFetch input rendering
            assert "🌐" in html  # WebFetch icon in title
            assert "https://docs.github.com/en/rest/pulls/comments" in html

            # Check WebFetch output rendering with metadata
            assert "webfetch-status-success" in html  # HTTP status 200
            assert "API Fields" in html  # Result content

    def test_webfetch_css_classes_included(self):
        """Test that WebFetch CSS classes are included in the template."""
        # Create WebFetch tool_use message
        tool_use_message = {
            "type": "assistant",
            "timestamp": "2025-06-14T10:00:00Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "2.0.37",
            "uuid": "webfetch_css_001",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5-20250929",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_webfetch_css",
                        "name": "WebFetch",
                        "input": {
                            "url": "https://example.com",
                            "prompt": "Get content",
                        },
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
            },
        }

        # Create WebFetch tool_result message with toolUseResult
        tool_result_message = {
            "type": "user",
            "timestamp": "2025-06-14T10:00:05Z",
            "parentUuid": "webfetch_css_001",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "test_session",
            "version": "2.0.37",
            "uuid": "webfetch_css_002",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_webfetch_css",
                        "content": "# Content\n\nFetched page content.",
                    }
                ],
            },
            "toolUseResult": {
                "url": "https://example.com",
                "result": "# Content\n\nFetched page content.",
                "bytes": 1024,
                "code": 200,
                "codeText": "OK",
                "durationMs": 500,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jsonl_file = temp_path / "webfetch_css_test.jsonl"

            with open(jsonl_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(tool_use_message) + "\n")
                f.write(json.dumps(tool_result_message) + "\n")

            messages = load_transcript(jsonl_file)
            html = generate_html(messages, "WebFetch CSS Test")

            # Check that WebFetch-related CSS classes are in the rendered content
            assert "webfetch-meta" in html
            assert "webfetch-result" in html
            assert "webfetch-status" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
