"""HTML formatters for user message content.

This module formats non-tool user message content types to HTML.
Part of the thematic formatter organization:
- system_formatters.py: SystemMessage, HookSummaryMessage
- user_formatters.py: SlashCommandMessage, CommandOutputMessage, etc.
- assistant_formatters.py: AssistantTextMessage, ThinkingMessage, ImageContent
- tool_formatters.py: tool use/result content
"""

from typing import Callable, Optional

from .ansi_colors import convert_ansi_to_html
from ..models import (
    BashInputMessage,
    BashOutputMessage,
    CommandOutputMessage,
    CompactedSummaryMessage,
    IdeDiagnostic,
    IdeNotificationContent,
    IdeOpenedFile,
    IdeSelection,
    ImageContent,
    SlashCommandMessage,
    UserMemoryMessage,
    UserSlashCommandMessage,
    UserTextMessage,
)
from .tool_formatters import render_params_table
from .utils import escape_html, render_collapsible_code, render_markdown_collapsible


# =============================================================================
# Formatting Functions
# =============================================================================


def format_slash_command_content(content: SlashCommandMessage) -> str:
    """Format slash command content as HTML.

    Args:
        content: SlashCommandMessage with command name, args, and contents

    Returns:
        HTML string for the slash command display
    """
    escaped_command_name = escape_html(content.command_name)
    escaped_command_args = escape_html(content.command_args)

    # Format the command contents with proper line breaks
    formatted_contents = content.command_contents.replace("\\n", "\n")
    escaped_command_contents = escape_html(formatted_contents)

    # Build the content HTML - command name is the primary content
    content_parts: list[str] = [f"<code>{escaped_command_name}</code>"]
    if content.command_args:
        content_parts.append(f"<strong>Args:</strong> {escaped_command_args}")
    if content.command_contents:
        lines = escaped_command_contents.splitlines()
        line_count = len(lines)
        if line_count <= 12:
            # Short content, show inline
            details_html = (
                f"<strong>Content:</strong><pre>{escaped_command_contents}</pre>"
            )
        else:
            # Long content, make collapsible with truncation indicator
            preview = "\n".join(lines[:5]) + "\n..."
            collapsible = render_collapsible_code(
                f"<pre>{preview}</pre>",
                f"<pre>{escaped_command_contents}</pre>",
                line_count,
            )
            details_html = f"<strong>Content:</strong>{collapsible}"
        content_parts.append(details_html)

    return "<br>".join(content_parts)


def format_command_output_content(content: CommandOutputMessage) -> str:
    """Format command output content as HTML.

    Args:
        content: CommandOutputMessage with stdout and is_markdown flag

    Returns:
        HTML string for the command output display
    """
    if content.is_markdown:
        # Render as markdown using shared renderer for GFM plugins and syntax highlighting
        return render_markdown_collapsible(
            content.stdout, "command-output-content", line_threshold=20
        )
    else:
        # Convert ANSI codes to HTML for colored display
        html_content = convert_ansi_to_html(content.stdout)
        # Use <pre> to preserve formatting and line breaks
        return f"<pre class='command-output-content'>{html_content}</pre>"


def format_bash_input_content(content: BashInputMessage) -> str:
    """Format bash input content as HTML.

    Args:
        content: BashInputMessage with the bash command

    Returns:
        HTML string for the bash input display
    """
    escaped_command = escape_html(content.command)
    return (
        f"<span class='bash-prompt'>❯</span> "
        f"<code class='bash-command'>{escaped_command}</code>"
    )


def format_bash_output_content(
    content: BashOutputMessage,
    collapse_threshold: int = 10,
    preview_lines: int = 3,
) -> str:
    """Format bash output content as HTML.

    Args:
        content: BashOutputMessage with stdout and/or stderr
        collapse_threshold: Number of lines before output becomes collapsible
        preview_lines: Number of preview lines to show when collapsed

    Returns:
        HTML string for the bash output display
    """
    output_parts: list[tuple[str, str, int, str]] = []
    total_lines = 0

    if content.stdout:
        escaped_stdout = convert_ansi_to_html(content.stdout)
        stdout_lines = len(content.stdout.splitlines())
        total_lines += stdout_lines
        output_parts.append(("stdout", escaped_stdout, stdout_lines, content.stdout))

    if content.stderr:
        escaped_stderr = convert_ansi_to_html(content.stderr)
        stderr_lines = len(content.stderr.splitlines())
        total_lines += stderr_lines
        output_parts.append(("stderr", escaped_stderr, stderr_lines, content.stderr))

    if not output_parts:
        # Empty output
        return (
            "<pre class='bash-stdout'><span class='bash-empty'>(no output)</span></pre>"
        )

    # Build the HTML parts
    html_parts: list[str] = []
    for output_type, escaped_content, _, _ in output_parts:
        css_name = f"bash-{output_type}"
        html_parts.append(f"<pre class='{css_name}'>{escaped_content}</pre>")

    full_html = "".join(html_parts)

    # Wrap in collapsible if output is large
    if total_lines > collapse_threshold:
        # Create preview (first few lines)
        first_output = output_parts[0]
        raw_preview = "\n".join(first_output[3].split("\n")[:preview_lines])
        preview_html = escape_html(raw_preview)
        if total_lines > preview_lines:
            preview_html += "\n..."

        # Use render_collapsible_code for consistent collapse markup
        return render_collapsible_code(
            preview_html=f"<pre class='bash-stdout'>{preview_html}</pre>",
            full_html=full_html,
            line_count=total_lines,
        )

    return full_html


def format_user_text_content(text: str) -> str:
    """Format plain user text content as HTML.

    User text is displayed as-is in preformatted blocks to preserve
    formatting and whitespace.

    Args:
        text: The raw user message text

    Returns:
        HTML string with escaped text in a pre tag
    """
    escaped_text = escape_html(text)
    return f"<pre>{escaped_text}</pre>"


def format_user_text_model_content(
    content: UserTextMessage,
    image_formatter: Optional[Callable[[ImageContent], str]] = None,
) -> str:
    """Format UserTextMessage model as HTML.

    Handles user text with optional IDE notifications, compacted summaries,
    memory input markers, and inline images.

    When `items` is set, iterates through the content items preserving order:
    - TextContent: Rendered as preformatted text
    - ImageContent: Rendered as inline <img> tag
    - IdeNotificationContent: Rendered as IDE notification blocks

    Args:
        content: UserTextMessage with text/items and optional flags/notifications
        image_formatter: Optional callback for image formatting. If None, uses
            format_image_content() which embeds images as base64 data URLs.

    Returns:
        HTML string combining all content items
    """
    # Import here to avoid circular dependency
    from .assistant_formatters import format_image_content

    formatter = image_formatter or format_image_content
    parts: list[str] = []

    for item in content.items:
        if isinstance(item, IdeNotificationContent):
            notifications = format_ide_notification_content(item)
            parts.extend(notifications)
        elif isinstance(item, ImageContent):
            parts.append(formatter(item))
        else:  # TextContent
            # Regular user text as preformatted
            if item.text.strip():
                parts.append(format_user_text_content(item.text))

    return "\n".join(parts)


def format_compacted_summary_content(content: CompactedSummaryMessage) -> str:
    """Format compacted session summary content as HTML.

    Compacted summaries are rendered as collapsible markdown since they
    contain structured summary text generated by Claude.

    Args:
        content: CompactedSummaryMessage with summary text

    Returns:
        HTML string with collapsible markdown rendering
    """
    return render_markdown_collapsible(
        content.summary_text,
        "compacted-summary",
        line_threshold=30,
        preview_line_count=10,
    )


def format_user_memory_content(content: UserMemoryMessage) -> str:
    """Format user memory input content as HTML.

    User memory content (from CLAUDE.md etc.) is rendered as preformatted text
    to preserve the original formatting.

    Args:
        content: UserMemoryMessage with memory text

    Returns:
        HTML string with escaped text in a pre tag
    """
    escaped_text = escape_html(content.memory_text)
    return f"<pre>{escaped_text}</pre>"


def format_user_slash_command_content(content: UserSlashCommandMessage) -> str:
    """Format slash command expanded prompt (isMeta) as HTML.

    These are LLM-generated instruction text from slash commands,
    rendered as collapsible markdown.

    Args:
        content: UserSlashCommandMessage with markdown text

    Returns:
        HTML string with collapsible markdown rendering
    """
    return render_markdown_collapsible(
        content.text,
        "slash-command",
        line_threshold=30,
        preview_line_count=10,
    )


def _format_opened_file(opened_file: IdeOpenedFile) -> str:
    """Format a single IDE opened file notification as HTML."""
    escaped_content = escape_html(opened_file.content)
    return f"<div class='ide-notification'>🤖 {escaped_content}</div>"


def _format_selection(selection: IdeSelection) -> str:
    """Format a single IDE selection notification as HTML."""
    escaped_content = escape_html(selection.content)

    # For large selections, make them collapsible
    if len(selection.content) > 200:
        preview = escape_html(selection.content[:150]) + "..."
        return (
            f"<div class='ide-notification ide-selection'>"
            f"<details class='ide-selection-collapsible'>"
            f"<summary>📝 {preview}</summary>"
            f"<pre class='ide-selection-content'>{escaped_content}</pre>"
            f"</details>"
            f"</div>"
        )
    else:
        return f"<div class='ide-notification ide-selection'>📝 {escaped_content}</div>"


def _format_diagnostic(diagnostic: IdeDiagnostic) -> list[str]:
    """Format a single IDE diagnostic as HTML (may produce multiple notifications)."""
    notifications: list[str] = []

    if diagnostic.diagnostics:
        # Parsed JSON diagnostics - render each as a table
        for diag_item in diagnostic.diagnostics:
            table_html = render_params_table(diag_item)
            notification_html = (
                f"<div class='ide-notification ide-diagnostic'>"
                f"⚠️ IDE Diagnostic<br>{table_html}"
                f"</div>"
            )
            notifications.append(notification_html)
    elif diagnostic.raw_content:
        # JSON parsing failed, render as plain text
        is_truncated = len(diagnostic.raw_content) > 200
        escaped_content = escape_html(diagnostic.raw_content[:200])
        truncation_marker = "..." if is_truncated else ""
        notification_html = (
            f"<div class='ide-notification'>🤖 IDE Diagnostics (parse error)<br>"
            f"<pre>{escaped_content}{truncation_marker}</pre></div>"
        )
        notifications.append(notification_html)

    return notifications


def format_ide_notification_content(content: IdeNotificationContent) -> list[str]:
    """Format IDE notification content as HTML.

    Takes structured IdeNotificationContent and returns a list of HTML
    notification strings.

    Args:
        content: IdeNotificationContent with opened_files, selections, diagnostics

    Returns:
        List of HTML notification strings
    """
    notifications: list[str] = []

    # Format opened files
    for opened_file in content.opened_files:
        notifications.append(_format_opened_file(opened_file))

    # Format selections
    for selection in content.selections:
        notifications.append(_format_selection(selection))

    # Format diagnostics (may produce multiple notifications per diagnostic)
    for diagnostic in content.diagnostics:
        notifications.extend(_format_diagnostic(diagnostic))

    return notifications


# =============================================================================
# Public Exports
# =============================================================================

__all__ = [
    # Formatting functions
    "format_slash_command_content",
    "format_command_output_content",
    "format_bash_input_content",
    "format_bash_output_content",
    "format_user_text_content",
    "format_user_text_model_content",
    "format_compacted_summary_content",
    "format_user_memory_content",
    "format_ide_notification_content",
]
