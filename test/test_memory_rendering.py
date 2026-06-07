"""Tests for auto-memory interaction detection + rendering (issue #192).

Auto-memory has no dedicated tool: it surfaces as ordinary Read/Write/Edit
calls on paths inside ``~/.claude/projects/<slug>/memory/``. We detect those,
give them a 🧠 title, and tag them with a ``memory`` CSS modifier so the
filter toolbar + timeline can isolate/hide them.
"""

import re
from pathlib import Path

from claude_code_log.converter import load_transcript
from claude_code_log.html.renderer import HtmlRenderer, generate_html
from claude_code_log.html.utils import (
    css_class_from_message,
    is_memory_path,
    is_memory_tool,
    memory_short_path,
)
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    EditInput,
    MessageMeta,
    ReadInput,
    ToolResultContent,
    ToolResultMessage,
    ToolUseMessage,
    WriteInput,
)
from claude_code_log.renderer import TemplateMessage

# Default auto-memory location: ~/.claude/projects/<slug>/memory/...
MEM = "/home/u/.claude/projects/-home-u-proj/memory/MEMORY.md"
MEM_SUB = "/home/u/.claude/projects/-home-u-proj/memory/topics/debugging.md"
# A project's own bare memory/ dir must NOT match (false-positive guard).
NON_MEM = "/home/u/proj/memory/notes.md"
# Windows backslash form of a memory path (windows-latest CI).
MEM_WIN = r"C:\Users\u\.claude\projects\-C--Users-u-proj\memory\MEMORY.md"


def _meta(is_sidechain: bool = False) -> MessageMeta:
    return MessageMeta(
        uuid="test-uuid",
        session_id="test",
        timestamp="2025-01-01T00:00:00Z",
        is_sidechain=is_sidechain,
    )


def _tool_use(
    tool_name: str, tool_input: object, is_sidechain: bool = False
) -> TemplateMessage:
    content = ToolUseMessage(
        meta=_meta(is_sidechain),
        input=tool_input,  # type: ignore[arg-type]
        tool_use_id="toolu_test",
        tool_name=tool_name,
    )
    return TemplateMessage(content)


def _tool_result(tool_name: str, file_path: str) -> TemplateMessage:
    content = ToolResultMessage(
        meta=_meta(),
        output=ToolResultContent(
            type="tool_result", tool_use_id="toolu_test", content="ok"
        ),
        tool_use_id="toolu_test",
        tool_name=tool_name,
        file_path=file_path,
    )
    return TemplateMessage(content)


class TestIsMemoryPath:
    def test_default_memory_paths_match(self):
        assert is_memory_path(MEM)
        assert is_memory_path(MEM_SUB)

    def test_bare_memory_dir_not_matched(self):
        """A project's own memory/ dir is not under .claude/projects/<slug>/."""
        assert not is_memory_path(NON_MEM)

    def test_windows_backslash_path_matches(self):
        """Backslash separators (windows-latest) are normalized before match."""
        assert is_memory_path(MEM_WIN)
        assert memory_short_path(MEM_WIN) == "MEMORY.md"
        assert is_memory_tool("Read", MEM_WIN)

    def test_unrelated_paths_not_matched(self):
        assert not is_memory_path("/home/u/proj/src/main.py")
        assert not is_memory_path(None)
        assert not is_memory_path("")

    def test_short_path_is_relative_to_memory_dir(self):
        assert memory_short_path(MEM) == "MEMORY.md"
        assert memory_short_path(MEM_SUB) == "topics/debugging.md"

    def test_is_memory_tool_gates_by_tool_name(self):
        for name in ("Read", "Write", "Edit"):
            assert is_memory_tool(name, MEM), name
        # Bash references memory paths only in its command string — out of scope.
        assert not is_memory_tool("Bash", MEM)
        assert not is_memory_tool("Glob", MEM)
        # File tool on a non-memory path is not a memory interaction.
        assert not is_memory_tool("Read", NON_MEM)
        assert not is_memory_tool("Read", None)


class TestMemoryTitles:
    """🧠 memory titles for Read/Write/Edit; plain icons otherwise."""

    def setup_method(self):
        self.r = HtmlRenderer()

    def test_read_memory_title(self):
        inp = ReadInput(file_path=MEM)
        title = self.r.title_ReadInput(inp, _tool_use("Read", inp))
        assert "🧠" in title
        assert "Read" in title
        assert "memory MEMORY.md" in title
        assert "📄" not in title

    def test_read_memory_title_with_line_range(self):
        inp = ReadInput(file_path=MEM_SUB, offset=10, limit=5)
        title = self.r.title_ReadInput(inp, _tool_use("Read", inp))
        assert "🧠" in title
        assert "memory topics/debugging.md" in title
        assert "lines 10-14" in title

    def test_write_memory_title(self):
        inp = WriteInput(file_path=MEM, content="x")
        title = self.r.title_WriteInput(inp, _tool_use("Write", inp))
        assert "🧠" in title
        assert "memory MEMORY.md" in title

    def test_edit_memory_title(self):
        inp = EditInput(file_path=MEM_SUB, old_string="a", new_string="b")
        title = self.r.title_EditInput(inp, _tool_use("Edit", inp))
        assert "🧠" in title
        assert "memory topics/debugging.md" in title

    def test_non_memory_read_keeps_plain_icon(self):
        inp = ReadInput(file_path="/home/u/proj/src/main.py")
        title = self.r.title_ReadInput(inp, _tool_use("Read", inp))
        assert "📄" in title
        assert "🧠" not in title
        assert "memory" not in title

    def test_non_memory_edit_keeps_plain_icon(self):
        inp = EditInput(file_path=NON_MEM, old_string="a", new_string="b")
        title = self.r.title_EditInput(inp, _tool_use("Edit", inp))
        assert "📝" in title
        assert "🧠" not in title


class TestMemoryBodyMarkdown:
    """Memory file bodies (which are Markdown) render as rendered Markdown via
    the usual collapsible-markdown helper, not as syntax-highlighted source."""

    def _render(self) -> str:
        fixture = Path(__file__).parent / "test_data" / "memory_interactions.jsonl"
        return generate_html(load_transcript(fixture), "Memory Body Markdown")

    def test_read_memory_body_rendered_as_markdown(self):
        html = self._render()
        # The MEMORY.md read result ('# Memory index\n\n- build: `just ci`')
        # renders as Markdown (h1 + inline code) inside a markdown wrapper.
        assert re.search(r'class="read-tool-result markdown"', html)
        assert re.search(r"<h1[^>]*>Memory index", html)
        assert "<code>just ci</code>" in html

    def test_write_memory_body_rendered_as_markdown(self):
        html = self._render()
        assert re.search(r'class="write-tool-content markdown"', html)

    def test_relative_link_anchored_to_memory_dir(self):
        """A relative Markdown link in a memory body (e.g. `[build](build.md)`)
        must resolve under the memory file's own directory — including the
        `memory/` segment — not the transcript page's directory (#192)."""
        html = self._render()
        assert (
            'href="file:///home/u/.claude/projects/-home-u-proj/memory/build.md"'
            in html
        )
        # The bare relative target (missing the memory/ segment) must not survive.
        assert 'href="build.md"' not in html

    def test_absolute_link_in_memory_body_untouched(self):
        """Absolute URLs in a memory body are left as-is (only relative links
        are anchored to the memory directory)."""
        html = self._render()
        assert 'href="https://example.com"' in html

    def test_memory_body_escapes_raw_html(self):
        """Memory files are untrusted content: raw HTML in the body must render
        as escaped text, not live DOM (#192 — uses the escape=True renderer)."""
        from claude_code_log.html.tool_formatters import format_write_input

        inp = WriteInput(file_path=MEM, content="# Note\n\n<script>alert(1)</script>")
        html = format_write_input(inp)
        assert "&lt;script&gt;" in html
        assert "<script>alert(1)</script>" not in html


class TestResolveMemoryBodyLinks:
    """Unit coverage for the relative-link anchoring helper."""

    BASE = "/home/u/.claude/projects/-home-u-proj/memory/MEMORY.md"

    def test_relative_link_rewritten(self):
        from claude_code_log.html.utils import resolve_memory_body_links

        out = resolve_memory_body_links('<a href="topic.md">x</a>', self.BASE)
        assert (
            out
            == '<a href="file:///home/u/.claude/projects/-home-u-proj/memory/topic.md">x</a>'
        )

    def test_subdir_relative_link_rewritten(self):
        from claude_code_log.html.utils import resolve_memory_body_links

        out = resolve_memory_body_links('<a href="sub/topic.md">x</a>', self.BASE)
        assert "/memory/sub/topic.md" in out

    def test_absolute_anchor_and_external_untouched(self):
        from claude_code_log.html.utils import resolve_memory_body_links

        for href in ("https://example.com", "#section", "/abs/path", "mailto:a@b.c"):
            html = f'<a href="{href}">x</a>'
            assert resolve_memory_body_links(html, self.BASE) == html


class TestMemoryMarkdownTitles:
    """Markdown renderer mirrors the 🧠 memory titles (parity with HTML)."""

    def setup_method(self):
        self.r = MarkdownRenderer()

    def test_read_memory_title(self):
        inp = ReadInput(file_path=MEM)
        title = self.r.title_ReadInput(inp, _tool_use("Read", inp))
        assert title == "🧠 Read memory `MEMORY.md`"

    def test_write_memory_title(self):
        inp = WriteInput(file_path=MEM_SUB, content="x")
        title = self.r.title_WriteInput(inp, _tool_use("Write", inp))
        assert title == "🧠 Write memory `topics/debugging.md`"

    def test_edit_memory_title(self):
        inp = EditInput(file_path=MEM, old_string="a", new_string="b")
        title = self.r.title_EditInput(inp, _tool_use("Edit", inp))
        assert title == "🧠 Edit memory `MEMORY.md`"

    def test_non_memory_read_keeps_plain_icon(self):
        inp = ReadInput(file_path="/home/u/proj/src/main.py")
        title = self.r.title_ReadInput(inp, _tool_use("Read", inp))
        assert "🧠" not in title
        assert "main.py" in title


class TestMemoryCssModifier:
    """The ``memory`` modifier tags both the call and its result."""

    def test_tool_use_read_memory_tagged(self):
        msg = _tool_use("Read", ReadInput(file_path=MEM))
        classes = css_class_from_message(msg).split()
        assert "tool_use" in classes
        assert "memory" in classes

    def test_tool_use_write_memory_tagged(self):
        msg = _tool_use("Write", WriteInput(file_path=MEM, content="x"))
        assert "memory" in css_class_from_message(msg).split()

    def test_tool_result_memory_tagged(self):
        msg = _tool_result("Read", MEM)
        classes = css_class_from_message(msg).split()
        assert "tool_result" in classes
        assert "memory" in classes

    def test_non_memory_tool_use_not_tagged(self):
        msg = _tool_use("Read", ReadInput(file_path="/home/u/proj/src/main.py"))
        assert "memory" not in css_class_from_message(msg).split()

    def test_non_memory_tool_result_not_tagged(self):
        msg = _tool_result("Read", NON_MEM)
        assert "memory" not in css_class_from_message(msg).split()

    def test_bash_on_memory_path_not_tagged(self):
        """Bash is out of scope for v1 even if a memory path appears."""
        msg = _tool_result("Bash", MEM)
        assert "memory" not in css_class_from_message(msg).split()

    def test_memory_in_sidechain_keeps_both_classes(self):
        """A memory interaction inside a sidechain carries BOTH `memory` and
        `sidechain` classes, so it stays 🧠-filterable (the JS treats memory as
        the primary dimension and keeps it in the memory lane). Regression for
        the CR finding that sidechain clobbered the memory classification."""
        msg = _tool_use("Read", ReadInput(file_path=MEM), is_sidechain=True)
        classes = css_class_from_message(msg).split()
        assert "memory" in classes
        assert "sidechain" in classes
