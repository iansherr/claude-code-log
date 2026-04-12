"""HTML formatters for system message content.

This module formats SystemTranscriptEntry-derived content types to HTML.
Part of the thematic formatter organization:
- system_formatters.py: SystemMessage, HookSummaryMessage
- user_formatters.py: SlashCommandMessage, CommandOutputMessage, etc.
- assistant_formatters.py: AssistantTextMessage, ThinkingMessage, ImageContent
- tool_formatters.py: tool use/result content
"""

import html

from .ansi_colors import convert_ansi_to_html
from ..models import (
    HookSummaryMessage,
    SessionHeaderMessage,
    SystemMessage,
)


def format_system_content(content: SystemMessage) -> str:
    """Format a system message with level-specific icon.

    Args:
        content: SystemMessage with level and text

    Returns:
        HTML with icon and ANSI-converted text
    """
    level_icon = {"warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(content.level, "ℹ️")
    html_content = convert_ansi_to_html(content.text)
    return f"<strong>{level_icon}</strong> {html_content}"


def format_hook_summary_content(content: HookSummaryMessage) -> str:
    """Format a hook summary as collapsible details.

    Shows a compact summary with expandable hook commands and error output.

    Args:
        content: HookSummaryMessage with execution details

    Returns:
        HTML with collapsible details section
    """
    # Determine if this is a failure or just output
    has_errors = bool(content.hook_errors)
    summary_icon = "🪝"
    summary_text = "Hook failed" if has_errors else "Hook output"

    # Build the command section
    command_html = ""
    if content.hook_infos:
        command_html = '<div class="hook-commands">'
        for info in content.hook_infos:
            # Truncate very long commands
            cmd = info.command
            display_cmd = cmd if len(cmd) <= 100 else cmd[:97] + "..."
            command_html += f"<code>{html.escape(display_cmd)}</code>"
        command_html += "</div>"

    # Build the error output section
    error_html = ""
    if content.hook_errors:
        error_html = '<div class="hook-errors">'
        for err in content.hook_errors:
            # Convert ANSI codes in error output
            formatted_err = convert_ansi_to_html(err)
            error_html += f'<pre class="hook-error">{formatted_err}</pre>'
        error_html += "</div>"

    return f"""<details class="hook-summary">
<summary><strong>{summary_icon}</strong> {summary_text}</summary>
<div class="hook-details">
{command_html}
{error_html}
</div>
</details>"""


def format_session_header_content(content: SessionHeaderMessage) -> str:
    """Format a session header as HTML.

    Args:
        content: SessionHeaderMessage with title, session_id, and optional summary

    Returns:
        HTML for the session header display
    """
    escaped_title = html.escape(content.title)
    if content.is_branch and content.parent_message_index is not None:
        # Branch header: compact with back-reference to fork point
        fork_backref = ""
        if content.parent_session_summary:
            escaped_fork = html.escape(content.parent_session_summary)
            fork_backref = (
                f'<div class="branch-from">'
                f'from <a href="#msg-d-{content.parent_message_index}" '
                f'class="branch-backlink">'
                f"&#x2442; Fork point &bull; {escaped_fork}</a></div>"
            )
        else:
            fork_backref = (
                f'<div class="branch-from">'
                f'from <a href="#msg-d-{content.parent_message_index}" '
                f'class="branch-backlink">'
                f"&#x2442; Fork point</a></div>"
            )
        return f"{escaped_title}{fork_backref}"
    if content.parent_session_id:
        parent_label = content.parent_session_summary or content.parent_session_id[:8]
        escaped_parent = html.escape(parent_label)
        if content.parent_message_index is not None:
            link = (
                f'<a href="#msg-d-{content.parent_message_index}" '
                f'class="session-backlink">&#x21b3; continues from '
                f"{escaped_parent}</a>"
            )
        else:
            link = (
                f'<span class="session-backlink">&#x21b3; continues from '
                f"{escaped_parent}</span>"
            )
        return f"{link}{escaped_title}"
    return escaped_title


__all__ = [
    "format_system_content",
    "format_hook_summary_content",
    "format_session_header_content",
]
