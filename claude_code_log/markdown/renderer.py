"""Markdown renderer implementation for Claude Code transcripts."""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from ..cache import get_library_version
from ..utils import generate_unified_diff, strip_error_tags
from ..models import (
    AssistantTextMessage,
    BashInputMessage,
    BashOutputMessage,
    CommandOutputMessage,
    CompactedSummaryMessage,
    HookSummaryMessage,
    ImageContent,
    SessionHeaderMessage,
    SlashCommandMessage,
    SystemMessage,
    TextContent,
    ThinkingMessage,
    ToolResultMessage,
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
    RenderingContext,
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
        self._ctx: RenderingContext | None = None

    # -------------------------------------------------------------------------
    # Private Utility Methods
    # -------------------------------------------------------------------------

    def _quote(self, text: str) -> str:
        """Prefix each line with '> ' to create a blockquote.

        Also escapes <summary> tags that would interfere with <details> rendering.
        """
        # Escape <summary> and </summary> tags on their own lines
        text = re.sub(r"^(</?summary>)$", r"\\\1", text, flags=re.MULTILINE)
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
        src = export_image(
            image,
            self.image_export_mode,
            output_dir=self._output_dir,
            counter=self._image_counter,
        )
        if src is None:
            return "[Image]"
        return f"![image]({src})"

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
        if isinstance(content, (AssistantTextMessage, UserTextMessage)):
            # Get first text item
            for item in content.items:
                if isinstance(item, TextContent) and item.text.strip():
                    return item.text
        return ""

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
            return f"📋 Session `{session_short}`: {content.summary}"
        return f"📋 Session `{session_short}`"

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

    def title_UserTextMessage(self, message: TemplateMessage) -> str:
        if excerpt := self._excerpt(self._get_message_text(message)):
            return f"🤷 User: *{self._escape_stars(excerpt)}*"
        return "🤷 User"

    def format_UserSlashCommandMessage(self, message: UserSlashCommandMessage) -> str:
        # UserSlashCommandMessage has a text attribute (markdown), quote to protect it
        return self._quote(message.text) if message.text.strip() else ""

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
        # command_name already includes the leading slash
        return f"🤷 Command `{content.command_name}`"

    def format_CommandOutputMessage(self, message: CommandOutputMessage) -> str:
        if message.is_markdown:
            # Quote markdown output to protect it
            return self._quote(message.stdout)
        return self._code_fence(message.stdout)

    def format_BashInputMessage(self, message: BashInputMessage) -> str:
        return self._code_fence(f"$ {message.command}", "bash")

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
        # Description is in the title, just show the command with $ prefix
        return self._code_fence(f"$ {input.command}", "bash")

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
        # Diff is visible; result goes in collapsible in format_EditOutput
        diff_text = generate_unified_diff(input.old_string, input.new_string)
        return self._code_fence(diff_text, "diff")

    def format_MultiEditInput(self, input: MultiEditInput) -> str:
        # All diffs visible; result goes in collapsible in format_EditOutput
        parts: list[str] = []
        for i, edit in enumerate(input.edits, 1):
            parts.append(f"**Edit {i}:**")
            diff_text = generate_unified_diff(edit.old_string, edit.new_string)
            parts.append(self._code_fence(diff_text, "diff"))
        return "\n\n".join(parts)

    def format_GlobInput(self, input: GlobInput) -> str:  # noqa: ARG002
        # Pattern and path are in the title
        return ""

    def format_GrepInput(self, input: GrepInput) -> str:
        # Pattern and path are in the title, only show glob filter if present
        if input.glob:
            return f"Glob: `{input.glob}`"
        return ""

    def format_TaskInput(self, input: TaskInput) -> str:
        # Description is now in the title, just show prompt as collapsible
        return (
            self._collapsible("Instructions", self._quote(input.prompt))
            if input.prompt
            else ""
        )

    def format_TodoWriteInput(self, input: TodoWriteInput) -> str:
        parts: list[str] = []
        for todo in input.todos:
            status_icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}.get(
                todo.status, "⬜"
            )
            parts.append(f"- {status_icon} {todo.content}")
        return "\n".join(parts)

    def format_AskUserQuestionInput(
        self,
        input: AskUserQuestionInput,  # noqa: ARG002
    ) -> str:
        # Input is rendered together with output in format_AskUserQuestionOutput
        return ""

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
        if msg := output.message:
            content = self._code_fence(msg, self._lang_from_path(output.file_path))
            return self._collapsible(f"<code>{output.file_path}</code>", content)
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

    def format_ToolResultMessage(self, message: ToolResultMessage) -> str:
        """Override for special output handling."""
        if isinstance(message.output, AskUserQuestionOutput):
            return self._format_ask_user_question(message, message.output)

        # TodoWrite success message - render as plain text, not code fence
        if message.tool_name == "TodoWrite":
            if isinstance(message.output, ToolResultContent):
                if isinstance(message.output.content, str):
                    return message.output.content
            return ""

        # Default: dispatch to output formatter
        return self._dispatch_format(message.output)

    def _format_ask_user_question(
        self, message: ToolResultMessage, output: AskUserQuestionOutput
    ) -> str:
        """Format AskUserQuestion with interleaved Q/options/A.

        Uses message.message_index to look up paired input for question options.
        """
        # Get questions from paired input via message_index → TemplateMessage → pair
        questions_map: dict[str, Any] = {}
        if message.message_index is not None and self._ctx:
            template_msg = self._ctx.get(message.message_index)
            if template_msg and template_msg.pair_first is not None:
                pair_msg = self._ctx.get(template_msg.pair_first)
                if pair_msg and isinstance(pair_msg.content, ToolUseMessage):
                    input_content = pair_msg.content.input
                    if isinstance(input_content, AskUserQuestionInput):
                        questions_map = {q.question: q for q in input_content.questions}

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

    def format_TaskOutput(self, output: TaskOutput) -> str:
        # TaskOutput contains markdown, wrap in collapsible Report
        return self._collapsible("Report", self._quote(output.result))

    def format_ExitPlanModeOutput(self, output: ExitPlanModeOutput) -> str:
        status = "✓ Approved" if output.approved else "✗ Not approved"
        if output.message:
            return f"{status}\n\n{output.message}"
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
        input = cast(BashInput, cast(ToolUseMessage, message.content).input)
        if desc := input.description:
            return f"💻 Bash: *{self._escape_stars(desc)}*"
        return "💻 Bash"

    def title_ReadInput(self, message: TemplateMessage) -> str:
        input = cast(ReadInput, cast(ToolUseMessage, message.content).input)
        return f"👀 Read `{Path(input.file_path).name}`"

    def title_WriteInput(self, message: TemplateMessage) -> str:
        input = cast(WriteInput, cast(ToolUseMessage, message.content).input)
        return f"✍️ Write `{Path(input.file_path).name}`"

    def title_EditInput(self, message: TemplateMessage) -> str:
        input = cast(EditInput, cast(ToolUseMessage, message.content).input)
        return f"✏️ Edit `{Path(input.file_path).name}`"

    def title_MultiEditInput(self, message: TemplateMessage) -> str:
        input = cast(MultiEditInput, cast(ToolUseMessage, message.content).input)
        return f"✏️ MultiEdit `{Path(input.file_path).name}`"

    def title_GlobInput(self, message: TemplateMessage) -> str:
        input = cast(GlobInput, cast(ToolUseMessage, message.content).input)
        title = f"📂 Glob `{input.pattern}`"
        return f"{title} in `{input.path}`" if input.path else title

    def title_GrepInput(self, message: TemplateMessage) -> str:
        input = cast(GrepInput, cast(ToolUseMessage, message.content).input)
        base = f"🔎 Grep `{input.pattern}`"
        return f"{base} in `{input.path}`" if input.path else base

    def title_TaskInput(self, message: TemplateMessage) -> str:
        input = cast(TaskInput, cast(ToolUseMessage, message.content).input)
        subagent = f" ({input.subagent_type})" if input.subagent_type else ""
        if desc := input.description:
            return f"🤖 Task{subagent}: *{self._escape_stars(desc)}*"
        return f"🤖 Task{subagent}"

    def title_TodoWriteInput(self, message: TemplateMessage) -> str:  # noqa: ARG002
        return "✅ Todo List"

    def title_AskUserQuestionInput(self, message: TemplateMessage) -> str:  # noqa: ARG002
        return "❓ Asking questions..."

    def title_ExitPlanModeInput(self, message: TemplateMessage) -> str:  # noqa: ARG002
        return "📝 Exiting plan mode"

    def title_ThinkingMessage(self, message: TemplateMessage) -> str:
        # When paired with Assistant, use Assistant title with assistant excerpt
        if message.is_first_in_pair and message.pair_last is not None:
            if (
                pair_msg := self._ctx.get(message.pair_last) if self._ctx else None
            ) and isinstance(pair_msg.content, AssistantTextMessage):
                if excerpt := self._excerpt(self._get_message_text(pair_msg)):
                    return f"🤖 Assistant: *{self._escape_stars(excerpt)}*"
                return "🤖 Assistant"

        # Standalone thinking
        if excerpt := self._excerpt(self._get_message_text(message)):
            return f"💭 Thinking: *{self._escape_stars(excerpt)}*"
        return "💭 Thinking"

    def title_AssistantTextMessage(self, message: TemplateMessage) -> str:
        # When paired (after Thinking), skip title (already rendered with Thinking)
        if message.is_last_in_pair:
            return ""
        # Sidechain assistant messages get special title
        if message.meta.is_sidechain:
            return "🔗 Sub-assistant"
        if excerpt := self._excerpt(self._get_message_text(message)):
            return f"🤖 Assistant: *{self._escape_stars(excerpt)}*"
        return "🤖 Assistant"

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

        # Heading with title (skip if empty)
        title = self.title_content(msg)
        if title:
            heading_level = min(level, 6)  # Markdown max is h6
            parts.append(f"{'#' * heading_level} {title}")

            # Format content (if not already output above)
            if content:
                parts.append(content)

        # Format paired message content (e.g., tool result)
        pair_msg = None
        if msg.is_first_in_pair and msg.pair_last is not None:
            if pair_msg := (self._ctx.get(msg.pair_last) if self._ctx else None):
                if pair_content := self.format_content(pair_msg):
                    parts.append(pair_content)

        # Render children at next level (from both this message and paired message)
        all_children = list(msg.children)
        if pair_msg and pair_msg.children:
            all_children.extend(pair_msg.children)
        for child in all_children:
            if child_output := self._render_message(child, level + 1):
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

        # Get root messages (tree), session navigation, and rendering context
        root_messages, session_nav, ctx = generate_template_messages(messages)
        self._ctx = ctx

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
            if rendered := self._render_message(root, level=1):
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
