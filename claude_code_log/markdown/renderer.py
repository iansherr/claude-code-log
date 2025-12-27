"""Markdown renderer implementation for Claude Code transcripts."""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from ..cache import get_library_version
from ..utils import strip_error_tags
from ..models import (
    AssistantTextMessage,
    BashInputMessage,
    BashOutputMessage,
    CommandOutputMessage,
    CompactedSummaryMessage,
    DedupNoticeMessage,
    HookSummaryMessage,
    ImageContent,
    SessionHeaderMessage,
    SlashCommandMessage,
    SystemMessage,
    TextContent,
    ThinkingMessage,
    ToolUseMessage,
    TranscriptEntry,
    UnknownMessage,
    UserMemoryMessage,
    UserSlashCommandMessage,
    UserTextMessage,
    # Tool input types
    AskUserQuestionInput,
    BashInput,
    EditInput,
    ExitPlanModeInput,
    GlobInput,
    GrepInput,
    MultiEditInput,
    ReadInput,
    TaskInput,
    TodoWriteInput,
    ToolUseContent,
    WriteInput,
    # Tool output types
    AskUserQuestionOutput,
    BashOutput,
    EditOutput,
    ExitPlanModeOutput,
    GlobOutput,
    ReadOutput,
    TaskOutput,
    ToolResultContent,
    WriteOutput,
)
from ..renderer import (
    Renderer,
    TemplateMessage,
    generate_template_messages,
    prepare_projects_index,
    title_for_projects_index,
)

if TYPE_CHECKING:
    from ..cache import CacheManager


class MarkdownRenderer(Renderer):
    """Markdown renderer for Claude Code transcripts."""

    def __init__(self, image_export_mode: str = "referenced"):
        """Initialize the Markdown renderer.

        Args:
            image_export_mode: Image export mode - "placeholder", "embedded", or "referenced"
        """
        super().__init__()
        self.image_export_mode = image_export_mode
        self._output_dir: Path | None = None
        self._image_counter = 0
        self._message_index: dict[int, TemplateMessage] = {}

    # -------------------------------------------------------------------------
    # Private Utility Methods
    # -------------------------------------------------------------------------

    def _quote(self, text: str) -> str:
        """Prefix each line with '> ' to create a blockquote."""
        return "\n".join(f"> {line}" for line in text.split("\n"))

    def _code_fence(self, text: str, lang: str = "") -> str:
        """Wrap text in a fenced code block with adaptive delimiter.

        If the text contains backticks, uses a longer delimiter to avoid conflicts.
        """
        # Find longest sequence of backticks in text
        max_ticks = 2
        for match in re.finditer(r"`+", text):
            max_ticks = max(max_ticks, len(match.group()))
        fence = "`" * max(3, max_ticks + 1)
        return f"{fence}{lang}\n{text}\n{fence}"

    def _escape_html_tag(self, text: str, tag: str) -> str:
        """Escape HTML closing tags to prevent breaking markdown structure.

        Replaces </tag> with &lt;/tag> to prevent premature closing.
        """
        return text.replace(f"</{tag}>", f"&lt;/{tag}>")

    def _escape_stars(self, text: str) -> str:
        """Escape asterisks for safe use inside emphasis markers.

        - * becomes \\*
        - \\* becomes \\\\\\* (preserves the escaped asterisk)
        """
        # First double all backslashes
        text = text.replace("\\", "\\\\")
        # Then escape all asterisks
        text = text.replace("*", "\\*")
        return text

    def _collapsible(self, summary: str, content: str) -> str:
        """Wrap content in a collapsible <details> block."""
        # Escape closing tags that would break the structure
        safe_summary = self._escape_html_tag(summary, "summary")
        safe_summary = self._escape_html_tag(safe_summary, "details")
        safe_content = self._escape_html_tag(content, "details")
        return f"<details>\n<summary>{safe_summary}</summary>\n\n{safe_content}\n</details>"

    def _format_image(self, image: ImageContent) -> str:
        """Format image based on export mode."""
        from ..image_export import export_image

        self._image_counter += 1
        return export_image(
            image, self.image_export_mode, self._output_dir, self._image_counter
        )

    def _lang_from_path(self, path: str) -> str:
        """Get language hint from file extension."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".json": "json",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".sh": "bash",
            ".bash": "bash",
            ".zsh": "bash",
            ".md": "markdown",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".toml": "toml",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".sql": "sql",
            ".xml": "xml",
            ".swift": "swift",
            ".kt": "kotlin",
            ".scala": "scala",
            ".r": "r",
            ".R": "r",
            ".lua": "lua",
            ".pl": "perl",
            ".ex": "elixir",
            ".exs": "elixir",
            ".erl": "erlang",
            ".hs": "haskell",
            ".ml": "ocaml",
            ".fs": "fsharp",
            ".clj": "clojure",
            ".vim": "vim",
            ".dockerfile": "dockerfile",
            ".Dockerfile": "dockerfile",
        }
        ext = Path(path).suffix.lower() if path else ""
        return ext_map.get(ext, "")

    def _excerpt(self, text: str, max_len: int = 40, min_len: int = 12) -> str:
        """Extract first line excerpt, truncating at word/sentence boundary.

        - Stops at sentence endings ("? ", "! ", ". " but not lone ".")
          only if at least min_len characters
        - If over max_len, continues to end of current word
        - Adds "…" if truncated
        """
        # Get first non-empty line
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Check for early sentence endings (but enforce minimum length)
            for ending in ("? ", "! ", ". "):
                pos = line.find(ending)
                if min_len <= pos < max_len:
                    return line[: pos + 1]  # Include the punctuation

            # If line fits, return as-is
            if len(line) <= max_len:
                return line

            # Find word boundary after max_len
            # Start from max_len and continue until non-word char
            end = max_len
            while end < len(line) and re.match(r"\w", line[end]):
                end += 1

            return line[:end] + "…"

        return ""

    def _get_message_text(self, msg: TemplateMessage) -> str:
        """Extract text content from a message for excerpt generation."""
        content = msg.content
        if isinstance(content, ThinkingMessage):
            return content.thinking
        if isinstance(content, AssistantTextMessage):
            # Get first text item
            for item in content.items:
                if isinstance(item, TextContent) and item.text.strip():
                    return item.text
        return ""

    def _build_message_index(
        self, roots: list[TemplateMessage]
    ) -> dict[int, TemplateMessage]:
        """Build index mapping message_index -> TemplateMessage."""
        index: dict[int, TemplateMessage] = {}

        def visit(msg: TemplateMessage) -> None:
            if msg.message_index is not None:
                index[msg.message_index] = msg
            for child in msg.children:
                visit(child)

        for root in roots:
            visit(root)
        return index

    # -------------------------------------------------------------------------
    # System Content Formatters
    # -------------------------------------------------------------------------

    def format_SystemMessage(self, message: SystemMessage) -> str:
        level_prefix = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}.get(
            message.level, ""
        )
        return f"{level_prefix} {message.text}"

    def format_HookSummaryMessage(self, message: HookSummaryMessage) -> str:
        parts: list[str] = []
        if message.has_output:
            parts.append("Hook produced output")
        if message.hook_errors:
            for error in message.hook_errors:
                parts.append(f"❌ Error: {error}")
        if message.hook_infos:
            for info in message.hook_infos:
                parts.append(f"ℹ️ {info}")
        return "\n\n".join(parts) if parts else ""

    def format_SessionHeaderMessage(self, message: SessionHeaderMessage) -> str:
        # Return just the anchor - it will be placed before the heading
        session_short = message.session_id[:8]
        return f'<a id="session-{session_short}"></a>'

    def title_SessionHeaderMessage(self, message: TemplateMessage) -> str:
        # Return the title with session ID and optional summary
        content = cast(SessionHeaderMessage, message.content)
        session_short = content.session_id[:8]
        if content.summary:
            return f"Session `{session_short}`: {content.summary}"
        return f"Session `{session_short}`"

    def format_DedupNoticeMessage(self, message: DedupNoticeMessage) -> str:  # noqa: ARG002
        # Skip dedup notices in markdown output
        return ""

    # -------------------------------------------------------------------------
    # User Content Formatters
    # -------------------------------------------------------------------------

    def format_UserTextMessage(self, message: UserTextMessage) -> str:
        parts: list[str] = []
        for item in message.items:
            if isinstance(item, ImageContent):
                parts.append(self._format_image(item))
            elif isinstance(item, TextContent):
                if item.text.strip():
                    # Use code fence to protect embedded markdown
                    parts.append(self._code_fence(item.text))
        return "\n\n".join(parts)

    def format_UserSlashCommandMessage(self, message: UserSlashCommandMessage) -> str:
        # UserSlashCommandMessage has a text attribute (markdown), quote to protect it
        if message.text.strip():
            return self._quote(message.text)
        return ""

    def format_SlashCommandMessage(self, message: SlashCommandMessage) -> str:
        parts: list[str] = []
        # Command name is in the title, only include args and contents here
        if message.command_args:
            parts.append(f"**Args:** `{message.command_args}`")
        if message.command_contents:
            parts.append(self._code_fence(message.command_contents))
        return "\n\n".join(parts)

    def title_SlashCommandMessage(self, message: TemplateMessage) -> str:
        content = cast(SlashCommandMessage, message.content)
        return f"Command `/{content.command_name}`"

    def format_CommandOutputMessage(self, message: CommandOutputMessage) -> str:
        if message.is_markdown:
            # Quote markdown output to protect it
            return self._quote(message.stdout)
        return self._code_fence(message.stdout)

    def format_BashInputMessage(self, message: BashInputMessage) -> str:
        return self._code_fence(message.command, "bash")

    def format_BashOutputMessage(self, message: BashOutputMessage) -> str:
        # Combine stdout and stderr, strip ANSI codes for markdown output
        parts: list[str] = []
        if message.stdout:
            parts.append(message.stdout)
        if message.stderr:
            parts.append(message.stderr)
        output = "\n".join(parts)
        output = re.sub(r"\x1b\[[0-9;]*m", "", output)
        return self._code_fence(output)

    def format_CompactedSummaryMessage(self, message: CompactedSummaryMessage) -> str:
        # Quote to protect embedded markdown
        return self._quote(message.summary_text)

    def format_UserMemoryMessage(self, message: UserMemoryMessage) -> str:
        return self._code_fence(message.memory_text)

    # -------------------------------------------------------------------------
    # Assistant Content Formatters
    # -------------------------------------------------------------------------

    def format_AssistantTextMessage(self, message: AssistantTextMessage) -> str:
        parts: list[str] = []
        for item in message.items:
            if isinstance(item, ImageContent):
                parts.append(self._format_image(item))
            else:  # TextContent
                if item.text.strip():
                    # Quote to protect embedded markdown
                    parts.append(self._quote(item.text))
        return "\n\n".join(parts)

    def format_ThinkingMessage(self, message: ThinkingMessage) -> str:
        quoted = self._quote(message.thinking)
        return self._collapsible("Thinking...", quoted)

    def format_UnknownMessage(self, message: UnknownMessage) -> str:
        return f"*Unknown content type: {message.type_name}*"

    # -------------------------------------------------------------------------
    # Tool Input Formatters
    # -------------------------------------------------------------------------

    def format_BashInput(self, input: BashInput) -> str:
        # Description is in the title, just show the command
        return self._code_fence(input.command, "bash")

    def format_ReadInput(self, input: ReadInput) -> str:
        # File path goes in the collapsible summary of ReadOutput
        # Just show line range hint here if applicable
        if input.offset or input.limit:
            start = input.offset or 0
            end = start + (input.limit or 0)
            return f"*(lines {start}–{end})*"
        return ""

    def format_WriteInput(self, input: WriteInput) -> str:
        summary = f"<code>{input.file_path}</code>"
        content = self._code_fence(input.content, self._lang_from_path(input.file_path))
        return self._collapsible(summary, content)

    def format_EditInput(self, input: EditInput) -> str:
        parts = [f"`{input.file_path}`"]
        lang = self._lang_from_path(input.file_path)
        parts.append("**Old:**")
        parts.append(self._code_fence(input.old_string, lang))
        parts.append("**New:**")
        parts.append(self._code_fence(input.new_string, lang))
        return "\n\n".join(parts)

    def format_MultiEditInput(self, input: MultiEditInput) -> str:
        parts = [f"`{input.file_path}`"]
        lang = self._lang_from_path(input.file_path)
        for i, edit in enumerate(input.edits, 1):
            parts.append(f"**Edit {i}:**")
            parts.append("Old:")
            parts.append(self._code_fence(edit.old_string, lang))
            parts.append("New:")
            parts.append(self._code_fence(edit.new_string, lang))
        return "\n\n".join(parts)

    def format_GlobInput(self, input: GlobInput) -> str:
        parts = [f"Pattern: `{input.pattern}`"]
        if input.path:
            parts.append(f"Path: `{input.path}`")
        return "\n\n".join(parts)

    def format_GrepInput(self, input: GrepInput) -> str:
        # Pattern and path are in the title, only show glob filter if present
        if input.glob:
            return f"Glob: `{input.glob}`"
        return ""

    def format_TaskInput(self, input: TaskInput) -> str:
        # Description is now in the title, just show prompt as collapsible
        if input.prompt:
            quoted = self._quote(input.prompt)
            return self._collapsible("Instructions", quoted)
        return ""

    def format_TodoWriteInput(self, input: TodoWriteInput) -> str:
        parts: list[str] = []
        for todo in input.todos:
            status_icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}.get(
                todo.status, "⬜"
            )
            parts.append(f"- {status_icon} {todo.content}")
        return "\n".join(parts)

    def format_AskUserQuestionInput(self, input: AskUserQuestionInput) -> str:
        # Store questions for use by paired output formatter
        self._pending_questions = {q.question: q for q in input.questions}
        return ""  # Full output rendered by format_AskUserQuestionOutput

    def format_ExitPlanModeInput(self, input: ExitPlanModeInput) -> str:  # noqa: ARG002
        # Title contains "Exiting plan mode", body is empty
        return ""

    def format_ToolUseContent(self, content: ToolUseContent) -> str:
        """Fallback for unknown tool inputs - render as key/value list."""
        return self._render_params(content.input)

    def _render_params(self, params: dict[str, Any]) -> str:
        """Render parameters as a markdown key/value list."""
        if not params:
            return "*No parameters*"

        lines: list[str] = []
        for key, value in params.items():
            if isinstance(value, (dict, list)):
                # Structured value - render as JSON code block
                formatted = json.dumps(value, indent=2, ensure_ascii=False)
                lines.append(f"**{key}:**")
                lines.append(self._code_fence(formatted, "json"))
            elif isinstance(value, str) and len(value) > 100:
                # Long string - render as code block
                lines.append(f"**{key}:**")
                lines.append(self._code_fence(value))
            else:
                # Simple value - inline
                lines.append(f"**{key}:** `{value}`")
        return "\n\n".join(lines)

    # -------------------------------------------------------------------------
    # Tool Output Formatters
    # -------------------------------------------------------------------------

    def format_ReadOutput(self, output: ReadOutput) -> str:
        summary = f"<code>{output.file_path}</code>" if output.file_path else "Content"
        lang = self._lang_from_path(output.file_path or "")
        content = self._code_fence(output.content, lang)
        return self._collapsible(summary, content)

    def format_WriteOutput(self, output: WriteOutput) -> str:
        return f"✓ {output.message}"

    def format_EditOutput(self, output: EditOutput) -> str:
        if output.message:
            lang = self._lang_from_path(output.file_path)
            return self._code_fence(output.message, lang)
        return "✓ Edited"

    def format_BashOutput(self, output: BashOutput) -> str:
        # Strip ANSI codes for markdown output
        text = re.sub(r"\x1b\[[0-9;]*m", "", output.content)
        # Detect git diff output
        lang = "diff" if text.startswith("diff --git a/") else ""
        return self._code_fence(text, lang)

    def format_GlobOutput(self, output: GlobOutput) -> str:
        if not output.files:
            return "*No files found*"
        return "\n".join(f"- `{f}`" for f in output.files)

    # Note: GrepOutput is not used (tool results handled as raw strings)
    # Grep results fall back to format_ToolResultContent

    def format_TaskOutput(self, output: TaskOutput) -> str:
        # TaskOutput contains markdown, wrap in collapsible Report
        quoted = self._quote(output.result)
        return self._collapsible("Report", quoted)

    def format_AskUserQuestionOutput(self, output: AskUserQuestionOutput) -> str:
        # Get stored questions from paired input
        questions_map = getattr(self, "_pending_questions", {})
        self._pending_questions = {}  # Clear for next use

        parts: list[str] = []
        for qa in output.answers:
            # Question in italics
            parts.append(f"**Q:** *{qa.question}*")

            # Options from paired input (if available)
            if qa.question in questions_map:
                q = questions_map[qa.question]
                for option in q.options:
                    parts.append(f"- {option.label}: {option.description}")

            # Answer
            parts.append(f"**A:** {qa.answer}")
            parts.append("")  # Blank line between Q&A pairs

        return "\n\n".join(parts).rstrip()

    def format_ExitPlanModeOutput(self, output: ExitPlanModeOutput) -> str:
        status = "✓ Approved" if output.approved else "✗ Not approved"
        if output.message:
            return f"{status}\n\n{self._quote(output.message)}"
        return status

    def format_ToolResultContent(self, output: ToolResultContent) -> str:
        """Fallback for unknown tool outputs."""
        if isinstance(output.content, str):
            content = strip_error_tags(output.content)
            return self._code_fence(content)
        return self._code_fence(json.dumps(output.content, indent=2), "json")

    # -------------------------------------------------------------------------
    # Title Methods (for tool use dispatch)
    # -------------------------------------------------------------------------

    def title_BashInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(BashInput, content.input)
        if input.description:
            escaped = self._escape_stars(input.description)
            return f"Bash: *{escaped}*"
        return "Bash"

    def title_ReadInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(ReadInput, content.input)
        return f"Read `{Path(input.file_path).name}`"

    def title_WriteInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(WriteInput, content.input)
        return f"Write `{Path(input.file_path).name}`"

    def title_EditInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(EditInput, content.input)
        return f"Edit `{Path(input.file_path).name}`"

    def title_MultiEditInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(MultiEditInput, content.input)
        return f"MultiEdit `{Path(input.file_path).name}`"

    def title_GlobInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(GlobInput, content.input)
        title = f"Glob `{input.pattern}`"
        if input.path:
            title += f" in `{input.path}`"
        return title

    def title_GrepInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(GrepInput, content.input)
        if input.path:
            return f"Grep `{input.pattern}` in `{input.path}`"
        return f"Grep `{input.pattern}`"

    def title_TaskInput(self, message: TemplateMessage) -> str:
        content = cast(ToolUseMessage, message.content)
        input = cast(TaskInput, content.input)
        subagent = f" ({input.subagent_type})" if input.subagent_type else ""
        if input.description:
            escaped = self._escape_stars(input.description)
            return f"Task{subagent}: *{escaped}*"
        return f"Task{subagent}"

    def title_TodoWriteInput(self, message: TemplateMessage) -> str:  # noqa: ARG002
        return "Todo List"

    def title_AskUserQuestionInput(self, message: TemplateMessage) -> str:  # noqa: ARG002
        return "Asking questions..."

    def title_ExitPlanModeInput(self, message: TemplateMessage) -> str:  # noqa: ARG002
        return "Exiting plan mode"

    def title_ThinkingMessage(self, message: TemplateMessage) -> str:
        # When paired with Assistant, use Assistant title with assistant excerpt
        if message.is_first_in_pair and message.pair_last is not None:
            pair_msg = self._message_index.get(message.pair_last)
            if pair_msg and isinstance(pair_msg.content, AssistantTextMessage):
                text = self._get_message_text(pair_msg)
                excerpt = self._excerpt(text)
                if excerpt:
                    escaped = self._escape_stars(excerpt)
                    return f"Assistant: *{escaped}*"
                return "Assistant"

        # Standalone thinking
        text = self._get_message_text(message)
        excerpt = self._excerpt(text)
        if excerpt:
            escaped = self._escape_stars(excerpt)
            return f"Thinking: *{escaped}*"
        return "Thinking"

    def title_AssistantTextMessage(self, message: TemplateMessage) -> str:
        # When paired (after Thinking), skip title (already rendered with Thinking)
        if message.is_last_in_pair:
            return ""

        # Sidechain assistant messages get special title
        if message.meta.is_sidechain:
            return "Sub-assistant"

        text = self._get_message_text(message)
        excerpt = self._excerpt(text)
        if excerpt:
            escaped = self._escape_stars(excerpt)
            return f"Assistant: *{escaped}*"
        return "Assistant"

    # -------------------------------------------------------------------------
    # Core Generate Methods
    # -------------------------------------------------------------------------

    def _generate_toc(self, session_nav: list[dict[str, Any]]) -> str:
        """Generate table of contents from session navigation."""
        lines = ["## Sessions", ""]
        for session in session_nav:
            session_id = session.get("id", "")
            session_short = session_id[:8]
            anchor = f"session-{session_short}"
            summary = session.get("summary")
            # Use summary if available, otherwise just the session ID
            label = (
                f"Session `{session_short}`: {summary}"
                if summary
                else f"Session `{session_short}`"
            )
            lines.append(f"- [{label}](#{anchor})")
        lines.append("")
        return "\n".join(lines)

    def _render_message(self, msg: TemplateMessage, level: int) -> str:
        """Render a message and its children recursively."""
        # Skip pair_last messages (rendered with pair_first)
        if msg.is_last_in_pair:
            return ""

        parts: list[str] = []

        # Format content - for session headers, anchor goes before heading
        content = self.format_content(msg)
        is_session_header = isinstance(msg.content, SessionHeaderMessage)
        if is_session_header and content:
            parts.append(content)
            content = None  # Don't output again below

        # Heading with title
        title = self.title_content(msg)
        heading_level = min(level, 6)  # Markdown max is h6
        parts.append(f"{'#' * heading_level} {title}")

        # Format content (if not already output above)
        if content:
            parts.append(content)

        # Format paired message content (e.g., tool result)
        if msg.is_first_in_pair and msg.pair_last is not None:
            pair_msg = self._message_index.get(msg.pair_last)
            if pair_msg:
                pair_content = self.format_content(pair_msg)
                if pair_content:
                    parts.append(pair_content)

        # Render children at next level
        for child in msg.children:
            child_output = self._render_message(child, level + 1)
            if child_output:
                parts.append(child_output)

        return "\n\n".join(parts)

    def generate(
        self,
        messages: list[TranscriptEntry],
        title: Optional[str] = None,
        combined_transcript_link: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> str:
        """Generate Markdown from transcript messages."""
        self._output_dir = output_dir
        self._image_counter = 0

        if not title:
            title = "Claude Transcript"

        # Get root messages (tree) and session navigation
        root_messages, session_nav = generate_template_messages(messages)

        # Build message index for paired message lookup
        self._message_index = self._build_message_index(root_messages)

        parts = [f"<!-- Generated by claude-code-log v{get_library_version()} -->", ""]
        parts.append(f"# {title}")

        # Table of Contents
        if session_nav:
            parts.append(self._generate_toc(session_nav))

        # Back link
        if combined_transcript_link:
            parts.append(f"[← Back to combined transcript]({combined_transcript_link})")
            parts.append("")

        # Render message tree
        for root in root_messages:
            rendered = self._render_message(root, level=1)
            if rendered:
                parts.append(rendered)

        return "\n\n".join(parts)

    def generate_session(
        self,
        messages: list[TranscriptEntry],
        session_id: str,
        title: Optional[str] = None,
        cache_manager: Optional["CacheManager"] = None,
        output_dir: Optional[Path] = None,
    ) -> str:
        """Generate Markdown for a single session."""
        session_messages = [msg for msg in messages if msg.sessionId == session_id]
        combined_link = "combined_transcripts.md" if cache_manager else None
        return self.generate(
            session_messages,
            title or f"Session {session_id[:8]}",
            combined_transcript_link=combined_link,
            output_dir=output_dir,
        )

    def generate_projects_index(
        self,
        project_summaries: list[dict[str, Any]],
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> str:
        """Generate a Markdown projects index page."""
        title = title_for_projects_index(project_summaries, from_date, to_date)
        template_projects, template_summary = prepare_projects_index(project_summaries)

        parts = [f"<!-- Generated by claude-code-log v{get_library_version()} -->", ""]
        parts.append(f"# {title}")

        # Summary stats
        parts.append(
            f"**Total:** {template_summary.total_projects} projects, "
            f"{template_summary.total_jsonl} sessions, "
            f"{template_summary.total_messages} messages"
        )
        parts.append("")

        # Project list
        for project in template_projects:
            # Derive markdown link from html_file path
            md_link = project.html_file.replace(".html", ".md")
            parts.append(f"## [{project.display_name}]({md_link})")
            # Use actual session count (filtered) like HTML does
            session_count = (
                len(project.sessions) if project.sessions else project.jsonl_count
            )
            parts.append(f"- Sessions: {session_count}")
            parts.append(f"- Messages: {project.message_count}")
            if project.formatted_time_range:
                parts.append(f"- Date range: {project.formatted_time_range}")
            parts.append("")

        return "\n".join(parts)

    def is_outdated(self, file_path: Path) -> bool:
        """Check if a Markdown file is outdated based on version comment."""
        if not file_path.exists():
            return True
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for _ in range(5):
                    line = f.readline()
                    if not line:
                        break
                    if "<!-- Generated by claude-code-log v" in line:
                        start = line.find("v") + 1
                        end = line.find(" -->")
                        if start > 0 and end > start:
                            return line[start:end] != get_library_version()
        except (OSError, UnicodeDecodeError):
            pass
        return True
