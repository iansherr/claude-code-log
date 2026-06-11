"""Type-aware fork-point / branch nav labels (dev/tool-use-continuation).

Tool-use and thinking fork points / branches previously rendered with an empty
preview (``Fork point (2 branches)`` / ``Branch • <uuid8>``). These pin the
enriched labels: a tool call reads as ``Bash — <description>``, thinking as
``Thinking``.
"""

from claude_code_log.factories import create_transcript_entry
from claude_code_log.models import (
    AssistantTextMessage,
    BashInput,
    MessageMeta,
    TextContent,
    ToolUseMessage,
    TranscriptEntry,
)
from claude_code_log.renderer import (
    RenderingContext,
    TemplateMessage,
    _entry_nav_summary,
    _fork_point_preview,
    _tool_summary_label,
)


def _meta() -> MessageMeta:
    return MessageMeta(uuid="u", session_id="s", timestamp="2025-01-01T00:00:00Z")


class TestToolSummaryLabel:
    def test_name_and_description(self):
        assert (
            _tool_summary_label("Bash", "Timeline d-N dependency check (retry)")
            == "Bash — Timeline d-N dependency check (retry)"
        )

    def test_name_only_when_no_description(self):
        assert _tool_summary_label("Read", None) == "Read"
        assert _tool_summary_label("Write", "") == "Write"

    def test_default_name(self):
        assert _tool_summary_label(None, None) == "Tool"

    def test_description_truncated_and_first_line(self):
        out = _tool_summary_label("Bash", "first line\nsecond line")
        assert out == "Bash — first line"
        long = _tool_summary_label("Bash", "x" * 100)
        assert long.startswith("Bash — ") and long.endswith("…") and len(long) < 90


def _entry(content: list[dict]) -> TranscriptEntry:
    raw = {
        "type": "assistant",
        "uuid": "e1",
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/x",
        "sessionId": "s",
        "version": "1.0",
        "timestamp": "2025-01-01T00:00:00Z",
        "requestId": "r",
        "message": {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": content,
        },
    }
    return create_transcript_entry(raw)


class TestEntryNavSummary:
    def test_tool_use_entry(self):
        e = _entry(
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls", "description": "List files"},
                }
            ]
        )
        assert _entry_nav_summary(e) == "Bash — List files"

    def test_tool_use_without_description(self):
        e = _entry(
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"file_path": "/x"},
                }
            ]
        )
        assert _entry_nav_summary(e) == "Read"

    def test_thinking_entry(self):
        e = _entry(
            [{"type": "thinking", "thinking": "pondering...", "signature": "sig"}]
        )
        assert _entry_nav_summary(e) == "Thinking"

    def test_text_entry(self):
        e = _entry([{"type": "text", "text": "Hello there"}])
        assert _entry_nav_summary(e) == "Hello there"


class TestForkPointPreviewTypeAware:
    def _ctx(self) -> RenderingContext:
        return RenderingContext(messages=[])

    def test_tool_use_fork_point(self):
        content = ToolUseMessage(
            meta=_meta(),
            input=BashInput(command="ls", description="List files"),
            tool_use_id="t1",
            tool_name="Bash",
        )
        msg = TemplateMessage(content)
        assert _fork_point_preview(msg, self._ctx()) == "Bash — List files"

    def test_text_fork_point_unchanged(self):
        content = AssistantTextMessage(
            meta=_meta(), items=[TextContent(type="text", text="hi there")]
        )
        msg = TemplateMessage(content)
        assert _fork_point_preview(msg, self._ctx()) == "hi there"
