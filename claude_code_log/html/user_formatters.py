"""HTML formatters for user message content.

This module formats non-tool user message content types to HTML.
Part of the thematic formatter organization:
- system_formatters.py: SystemContent, HookSummaryContent
- user_formatters.py: SlashCommandContent, CommandOutputContent, etc.
- assistant_formatters.py: (future) assistant message variants
- tool_formatters.py: tool use/result content
"""

from typing import List

import mistune

from .ansi_colors import convert_ansi_to_html
from ..models import (
    BashInputContent,
    BashOutputContent,
    CommandOutputContent,
    IdeDiagnostic,
    IdeNotificationContent,
    IdeOpenedFile,
    IdeSelection,
    SlashCommandContent,
    UserTextContent,
)
from .tool_formatters import render_params_table
from .utils import escape_html, render_collapsible_code, render_markdown_collapsible


# =============================================================================
# Formatting Functions
# =============================================================================


def format_slash_command_content(content: SlashCommandContent) -> str:
    """Format slash command content as HTML.

    Args:
        content: SlashCommandContent with command name, args, and contents

    Returns:
        HTML string for the slash command display
    """
    escaped_command_name = escape_html(content.command_name)
    escaped_command_args = escape_html(content.command_args)

    # Format the command contents with proper line breaks
    formatted_contents = content.command_contents.replace("\\n", "\n")
    escaped_command_contents = escape_html(formatted_contents)

    # Build the content HTML - command name is the primary content
    content_parts: List[str] = [f"<code>{escaped_command_name}</code>"]
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
            # Long content, make collapsible
            preview = "\n".join(lines[:5])
            collapsible = render_collapsible_code(
                f"<pre>{preview}</pre>",
                f"<pre>{escaped_command_contents}</pre>",
                line_count,
            )
            details_html = f"<strong>Content:</strong>{collapsible}"
        content_parts.append(details_html)

    return "<br>".join(content_parts)


def format_command_output_content(content: CommandOutputContent) -> str:
    """Format command output content as HTML.

    Args:
        content: CommandOutputContent with stdout and is_markdown flag

    Returns:
        HTML string for the command output display
    """
    if content.is_markdown:
        # Render as markdown
        markdown_html = mistune.html(content.stdout)
        return f"<div class='command-output-content'>{markdown_html}</div>"
    else:
        # Convert ANSI codes to HTML for colored display
        html_content = convert_ansi_to_html(content.stdout)
        # Use <pre> to preserve formatting and line breaks
        return f"<pre class='command-output-content'>{html_content}</pre>"


def format_bash_input_content(content: BashInputContent) -> str:
    """Format bash input content as HTML.

    Args:
        content: BashInputContent with the bash command

    Returns:
        HTML string for the bash input display
    """
    escaped_command = escape_html(content.command)
    return (
        f"<span class='bash-prompt'>❯</span> "
        f"<code class='bash-command'>{escaped_command}</code>"
    )


def format_bash_output_content(
    content: BashOutputContent,
    collapse_threshold: int = 10,
    preview_lines: int = 3,
) -> str:
    """Format bash output content as HTML.

    Args:
        content: BashOutputContent with stdout and/or stderr
        collapse_threshold: Number of lines before output becomes collapsible
        preview_lines: Number of preview lines to show when collapsed

    Returns:
        HTML string for the bash output display
    """
    output_parts: List[tuple[str, str, int, str]] = []
    total_lines = 0

    if content.stdout:
        escaped_stdout = convert_ansi_to_html(content.stdout)
        stdout_lines = content.stdout.count("\n") + 1
        total_lines += stdout_lines
        output_parts.append(("stdout", escaped_stdout, stdout_lines, content.stdout))

    if content.stderr:
        escaped_stderr = convert_ansi_to_html(content.stderr)
        stderr_lines = content.stderr.count("\n") + 1
        total_lines += stderr_lines
        output_parts.append(("stderr", escaped_stderr, stderr_lines, content.stderr))

    if not output_parts:
        # Empty output
        return (
            "<pre class='bash-stdout'><span class='bash-empty'>(no output)</span></pre>"
        )

    # Build the HTML parts
    html_parts: List[str] = []
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

        return f"""<details class='collapsible-code'>
            <summary>
                <span class='line-count'>{total_lines} lines</span>
                <pre class='preview-content bash-stdout'>{preview_html}</pre>
            </summary>
            <div class='code-full'>{full_html}</div>
        </details>"""

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


def format_user_text_model_content(content: UserTextContent) -> str:
    """Format UserTextContent model as HTML.

    Handles user text with optional IDE notifications, compacted summaries,
    and memory input markers.

    Args:
        content: UserTextContent with text and optional flags/notifications

    Returns:
        HTML string combining IDE notifications and main text content
    """
    parts: List[str] = []

    # Add IDE notifications first if present
    if content.ide_notifications:
        notifications = format_ide_notification_content(content.ide_notifications)
        parts.extend(notifications)

    # Format main text content based on type
    if content.is_compacted:
        # Render compacted summaries as markdown
        text_html = render_markdown_collapsible(
            content.text, "compacted-summary", line_threshold=20
        )
    elif content.is_memory_input:
        # Render memory input as markdown
        text_html = render_markdown_collapsible(
            content.text, "user-memory", line_threshold=20
        )
    else:
        # Regular user text as preformatted
        text_html = format_user_text_content(content.text)

    parts.append(text_html)
    return "\n".join(parts)


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
        return f"""
            <div class='ide-notification ide-selection'>
                <details class='ide-selection-collapsible'>
                    <summary>📝 {preview}</summary>
                    <pre class='ide-selection-content'>{escaped_content}</pre>
                </details>
            </div>
        """
    else:
        return f"<div class='ide-notification ide-selection'>📝 {escaped_content}</div>"


def _format_diagnostic(diagnostic: IdeDiagnostic) -> List[str]:
    """Format a single IDE diagnostic as HTML (may produce multiple notifications)."""
    notifications: List[str] = []

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
        escaped_content = escape_html(diagnostic.raw_content[:200])
        notification_html = (
            f"<div class='ide-notification'>🤖 IDE Diagnostics (parse error)<br>"
            f"<pre>{escaped_content}...</pre></div>"
        )
        notifications.append(notification_html)

    return notifications


def format_ide_notification_content(content: IdeNotificationContent) -> List[str]:
    """Format IDE notification content as HTML.

    Takes structured IdeNotificationContent and returns a list of HTML
    notification strings, preserving the same output format as the original
    extract_ide_notifications function.

    Args:
        content: IdeNotificationContent with opened_files, selections, diagnostics

    Returns:
        List of HTML notification strings
    """
    notifications: List[str] = []

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
    "format_ide_notification_content",
]
