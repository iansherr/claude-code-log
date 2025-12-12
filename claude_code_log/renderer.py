#!/usr/bin/env python3
"""Render Claude transcript data to HTML format."""

import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .cache import CacheManager
    from .models import MessageContent
from datetime import datetime
import html
from .models import (
    MessageModifiers,
    MessageType,
    TranscriptEntry,
    AssistantTranscriptEntry,
    SystemTranscriptEntry,
    SummaryTranscriptEntry,
    QueueOperationTranscriptEntry,
    ContentItem,
    TextContent,
    ToolResultContent,
    ToolUseContent,
    ThinkingContent,
    ThinkingContentModel,
    ImageContent,
    # Structured content types
    HookInfo,
    HookSummaryContent,
    SystemContent,
)
from .parser import (
    extract_text_content,
    is_assistant_entry,
    is_bash_input,
    is_bash_output,
    is_command_message,
    is_local_command_output,
    is_user_entry,
)
from .utils import (
    format_timestamp,
    format_timestamp_range,
    get_project_display_name,
    should_skip_message,
    should_use_as_session_starter,
    create_session_preview,
)
from .renderer_timings import (
    DEBUG_TIMING,
    report_timing_statistics,
    set_timing_var,
    log_timing,
)
from .ansi_colors import convert_ansi_to_html

from .html import (
    escape_html,
    format_askuserquestion_result,
    format_bash_input_content,
    format_command_output_content,
    format_edit_tool_result,
    format_exitplanmode_result,
    format_read_tool_result,
    format_slash_command_content,
    format_thinking_content,
    format_tool_use_content,
    format_tool_use_title,
    parse_bash_input,
    parse_command_output,
    parse_edit_output,
    parse_read_output,
    parse_slash_command,
    render_markdown_collapsible,
    render_params_table,
)


# -- Tool Result Content Formatting -------------------------------------------
# NOTE: Slash command parsing moved to html/user_formatters.py (parse_slash_command)
# NOTE: Parsing functions moved to html/tool_formatters.py


def format_tool_result_content(
    tool_result: ToolResultContent,
    file_path: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> str:
    """Format tool result content as HTML, including images.

    Args:
        tool_result: The tool result content
        file_path: Optional file path for context (used for Read/Edit/Write tool rendering)
        tool_name: Optional tool name for specialized rendering (e.g., "Write", "Read", "Edit", "Task")
    """
    # Handle both string and structured content
    if isinstance(tool_result.content, str):
        raw_content = tool_result.content
        has_images = False
        image_html_parts: List[str] = []
    else:
        # Content is a list of structured items, extract text and images
        content_parts: List[str] = []
        image_html_parts: List[str] = []
        for item in tool_result.content:
            item_type = item.get("type")
            if item_type == "text":
                text_value = item.get("text")
                if isinstance(text_value, str):
                    content_parts.append(text_value)
            elif item_type == "image":
                # Handle image content within tool results
                source = cast(Dict[str, Any], item.get("source", {}))
                if source:
                    media_type: str = str(source.get("media_type", "image/png"))
                    data: str = str(source.get("data", ""))
                    if data:
                        data_url = f"data:{media_type};base64,{data}"
                        image_html_parts.append(
                            f'<img src="{data_url}" alt="Tool result image" '
                            f'class="tool-result-image" />'
                        )
        raw_content = "\n".join(content_parts)
        has_images = len(image_html_parts) > 0

    # Strip <tool_use_error> XML tags but keep the content inside
    # Also strip redundant "String: ..." portions that echo the input
    import re

    if raw_content:
        # Remove <tool_use_error>...</tool_use_error> tags but keep inner content
        raw_content = re.sub(
            r"<tool_use_error>(.*?)</tool_use_error>",
            r"\1",
            raw_content,
            flags=re.DOTALL,
        )
        # Remove "String: ..." portions that echo the input (everything after "String:" to end)
        raw_content = re.sub(r"\nString:.*$", "", raw_content, flags=re.DOTALL)

    # Special handling for Write tool: only show first line (acknowledgment) on success
    if tool_name == "Write" and not tool_result.is_error and not has_images:
        lines = raw_content.split("\n")
        if lines:
            # Keep only the first acknowledgment line and add ellipsis
            first_line = lines[0]
            escaped_html = escape_html(first_line)
            return f"<pre>{escaped_html} ...</pre>"

    # Try to parse as Read tool result if file_path is provided
    if file_path and tool_name == "Read" and not has_images:
        read_output = parse_read_output(raw_content, file_path)
        if read_output:
            return format_read_tool_result(read_output)

    # Try to parse as Edit tool result if file_path is provided
    if file_path and tool_name == "Edit" and not has_images:
        edit_output = parse_edit_output(raw_content, file_path)
        if edit_output:
            return format_edit_tool_result(edit_output)

    # Special handling for Task tool: render result as markdown with Pygments (agent's final message)
    # Deduplication is now handled retroactively by replacing the sub-assistant content
    if tool_name == "Task" and not has_images:
        return render_markdown_collapsible(raw_content, "task-result")

    # Special handling for ExitPlanMode tool: truncate redundant plan echo on success
    if tool_name == "ExitPlanMode" and not has_images:
        processed_content = format_exitplanmode_result(raw_content)
        escaped_content = escape_html(processed_content)
        return f"<pre>{escaped_content}</pre>"

    # Special handling for AskUserQuestion tool: render Q&A pairs with styling
    if tool_name == "AskUserQuestion" and not has_images:
        styled_result = format_askuserquestion_result(raw_content)
        if styled_result:
            return styled_result
        # Fall through to default handling if parsing fails

    # Check if this looks like Bash tool output and process ANSI codes
    # Bash tool results often contain ANSI escape sequences and terminal output
    if _looks_like_bash_output(raw_content):
        escaped_content = convert_ansi_to_html(raw_content)
    else:
        escaped_content = escape_html(raw_content)

    # Build final HTML based on content length and presence of images
    if has_images:
        # Combine text and images
        text_html = f"<pre>{escaped_content}</pre>" if escaped_content else ""
        images_html = "".join(image_html_parts)
        combined_content = f"{text_html}{images_html}"

        # Always make collapsible when images are present
        preview_text = "Text and image content"
        return f"""
    <details class="collapsible-details">
        <summary>
            <span class='preview-text'>{preview_text}</span>
        </summary>
        <div class="details-content">
            {combined_content}
        </div>
    </details>
    """
    else:
        # Text-only content (existing behavior)
        # For simple content, show directly without collapsible wrapper
        if len(escaped_content) <= 200:
            return f"<pre>{escaped_content}</pre>"

        # For longer content, use collapsible details but no extra wrapper
        preview_text = escaped_content[:200] + "..."
        return f"""
    <details class="collapsible-details">
        <summary>
            <div class="preview-content"><pre>{preview_text}</pre></div>
        </summary>
        <div class="details-content">
            <pre>{escaped_content}</pre>
        </div>
    </details>
    """


def _looks_like_bash_output(content: str) -> bool:
    """Check if content looks like it's from a Bash tool based on common patterns."""
    if not content:
        return False

    # Check for ANSI escape sequences
    if "\x1b[" in content:
        return True

    # Check for common bash/terminal patterns
    bash_indicators = [
        "$ ",  # Shell prompt
        "❯ ",  # Modern shell prompt
        "> ",  # Shell continuation
        "\n+ ",  # Bash -x output
        "bash: ",  # Bash error messages
        "/bin/bash",  # Bash path
        "command not found",  # Common bash error
        "Permission denied",  # Common bash error
        "No such file or directory",  # Common bash error
    ]

    # Check for file path patterns that suggest command output
    import re

    if re.search(r"/[a-zA-Z0-9_-]+(/[a-zA-Z0-9_.-]+)*", content):  # Unix-style paths
        return True

    # Check for common command output patterns
    if any(indicator in content for indicator in bash_indicators):
        return True

    return False


# -- Content Formatters -------------------------------------------------------
# NOTE: format_thinking_content moved to html/assistant_formatters.py


def format_image_content(image: ImageContent) -> str:
    """Format image content as HTML."""
    # Create a data URL from the base64 image data
    data_url = f"data:{image.source.media_type};base64,{image.source.data}"

    return f'<img src="{data_url}" alt="Uploaded image" class="uploaded-image" />'


def _is_compacted_session_summary(text: str) -> bool:
    """Check if text is a compacted session summary (model-generated markdown).

    Compacted summaries are generated when a session runs out of context and
    needs to be continued. They are well-formed markdown and should be rendered
    as such rather than in preformatted blocks.
    """
    return text.startswith(
        "This session is being continued from a previous conversation that ran out of context"
    )


def extract_ide_notifications(text: str) -> tuple[List[str], str]:
    """Extract IDE notification tags from user message text.

    Handles:
    - <ide_opened_file>: Simple file open notifications
    - <ide_selection>: Code selection notifications (collapsible for large selections)
    - <post-tool-use-hook><ide_diagnostics>: JSON diagnostic arrays

    Returns:
        A tuple of (notifications_html_list, remaining_text)
        where notifications are pre-rendered HTML divs and remaining_text
        is the message content with IDE tags removed.
    """
    import re

    notifications: List[str] = []
    remaining_text = text

    # Pattern 1: <ide_opened_file>content</ide_opened_file>
    ide_file_pattern = r"<ide_opened_file>(.*?)</ide_opened_file>"
    file_matches = list(re.finditer(ide_file_pattern, remaining_text, flags=re.DOTALL))

    for match in file_matches:
        content = match.group(1).strip()
        escaped_content = escape_html(content)
        notification_html = f"<div class='ide-notification'>🤖 {escaped_content}</div>"
        notifications.append(notification_html)

    # Remove ide_opened_file tags
    remaining_text = re.sub(ide_file_pattern, "", remaining_text, flags=re.DOTALL)

    # Pattern 2: <ide_selection>content</ide_selection>
    selection_pattern = r"<ide_selection>(.*?)</ide_selection>"
    selection_matches = list(
        re.finditer(selection_pattern, remaining_text, flags=re.DOTALL)
    )

    for match in selection_matches:
        content = match.group(1).strip()
        escaped_content = escape_html(content)

        # For large selections, make them collapsible
        if len(content) > 200:
            preview = escape_html(content[:150]) + "..."
            notification_html = f"""
                <div class='ide-notification ide-selection'>
                    <details class='ide-selection-collapsible'>
                        <summary>📝 {preview}</summary>
                        <pre class='ide-selection-content'>{escaped_content}</pre>
                    </details>
                </div>
            """
        else:
            notification_html = f"<div class='ide-notification ide-selection'>📝 {escaped_content}</div>"

        notifications.append(notification_html)

    # Remove ide_selection tags
    remaining_text = re.sub(selection_pattern, "", remaining_text, flags=re.DOTALL)

    # Pattern 3: <post-tool-use-hook><ide_diagnostics>JSON</ide_diagnostics></post-tool-use-hook>
    hook_pattern = r"<post-tool-use-hook>\s*<ide_diagnostics>(.*?)</ide_diagnostics>\s*</post-tool-use-hook>"
    hook_matches = list(re.finditer(hook_pattern, remaining_text, flags=re.DOTALL))

    for match in hook_matches:
        json_content = match.group(1).strip()
        try:
            # Parse JSON array of diagnostic objects
            diagnostics: Any = json.loads(json_content)
            if isinstance(diagnostics, list):
                # Render each diagnostic as a table
                for diagnostic in cast(List[Any], diagnostics):
                    if isinstance(diagnostic, dict):
                        # Type assertion: we've confirmed it's a dict
                        diagnostic_dict = cast(Dict[str, Any], diagnostic)
                        table_html = render_params_table(diagnostic_dict)
                        notification_html = (
                            f"<div class='ide-notification ide-diagnostic'>"
                            f"⚠️ IDE Diagnostic<br>{table_html}"
                            f"</div>"
                        )
                        notifications.append(notification_html)
        except (json.JSONDecodeError, ValueError):
            # If JSON parsing fails, render as plain text
            escaped_content = escape_html(json_content[:200])
            notification_html = (
                f"<div class='ide-notification'>🤖 IDE Diagnostics (parse error)<br>"
                f"<pre>{escaped_content}...</pre></div>"
            )
            notifications.append(notification_html)

    # Remove hook tags
    remaining_text = re.sub(hook_pattern, "", remaining_text, flags=re.DOTALL)

    return notifications, remaining_text.strip()


def render_user_message_content(
    content_list: List[ContentItem],
) -> tuple[str, bool, bool]:
    """Render user message content with IDE tag extraction and compacted summary handling.

    Returns:
        A tuple of (content_html, is_compacted, is_memory_input)
    """
    # Check first text item
    if content_list and hasattr(content_list[0], "text"):
        first_text = getattr(content_list[0], "text", "")

        # Check for compacted session summary first
        if _is_compacted_session_summary(first_text):
            # Combine all text content for compacted summaries
            all_text = "\n\n".join(
                item.text for item in content_list if isinstance(item, TextContent)
            )
            # Render as collapsible markdown (threshold=30, preview=10 for large summaries)
            content_html = render_markdown_collapsible(
                all_text, "compacted-summary", line_threshold=30, preview_line_count=10
            )
            return content_html, True, False

        # Check for user memory input
        memory_match = re.search(
            r"<user-memory-input>(.*?)</user-memory-input>",
            first_text,
            re.DOTALL,
        )
        if memory_match:
            memory_content = memory_match.group(1).strip()
            # Render the memory content as user message
            memory_content_list: List[ContentItem] = [
                TextContent(type="text", text=memory_content)
            ]
            content_html = render_message_content(memory_content_list, "user")
            return content_html, False, True

        # Extract IDE notifications from first text item
        ide_notifications_html, remaining_text = extract_ide_notifications(first_text)
        modified_content = content_list[1:]

        # Build new content list with remaining text
        if remaining_text:
            # Replace first item with remaining text
            modified_content = [
                TextContent(type="text", text=remaining_text)
            ] + modified_content

        # Render the content
        content_html = render_message_content(modified_content, "user")

        # Prepend IDE notifications
        if ide_notifications_html:
            content_html = "".join(ide_notifications_html) + content_html
    else:
        # No text in first item or empty list, render normally
        content_html = render_message_content(content_list, "user")

    return content_html, False, False


def render_message_content(content: List[ContentItem], message_type: str) -> str:
    """Render message content with proper tool use and tool result formatting.

    Note: This does NOT handle user-specific preprocessing like IDE tags or
    compacted session summaries. Those should be handled by render_user_message_content.
    """
    if len(content) == 1 and isinstance(content[0], TextContent):
        if message_type == MessageType.USER:
            # User messages are shown as-is in preformatted blocks
            escaped_text = escape_html(content[0].text)
            return "<pre>" + escaped_text + "</pre>"
        else:
            # Assistant messages get markdown rendering with collapsible for long content
            return render_markdown_collapsible(
                content[0].text,
                "assistant-text",
                line_threshold=30,
                preview_line_count=10,
            )

    # content is a list of ContentItem objects
    rendered_parts: List[str] = []

    for item in content:
        # Handle both custom and Anthropic types
        item_type = getattr(item, "type", None)

        if type(item) is TextContent or (
            hasattr(item, "type") and hasattr(item, "text") and item_type == "text"
        ):
            # Handle both TextContent and Anthropic TextBlock
            text_value = getattr(item, "text", str(item))
            if message_type == MessageType.USER:
                # User messages are shown as-is in preformatted blocks
                escaped_text = escape_html(text_value)
                rendered_parts.append("<pre>" + escaped_text + "</pre>")
            else:
                # Assistant messages get markdown rendering with collapsible for long content
                rendered_parts.append(
                    render_markdown_collapsible(
                        text_value,
                        "assistant-text",
                        line_threshold=30,
                        preview_line_count=10,
                    )
                )
        elif type(item) is ToolUseContent or (
            hasattr(item, "type") and item_type == "tool_use"
        ):
            # Tool use items should not appear here - they are filtered out before this function
            print(
                "Warning: tool_use content should not be processed in render_message_content",
                flush=True,
            )
        elif type(item) is ToolResultContent or (
            hasattr(item, "type") and item_type == "tool_result"
        ):
            # Tool result items should not appear here - they are filtered out before this function
            print(
                "Warning: tool_result content should not be processed in render_message_content",
                flush=True,
            )
        elif type(item) is ThinkingContent or (
            hasattr(item, "type") and item_type == "thinking"
        ):
            # Thinking items should not appear here - they are filtered out before this function
            print(
                "Warning: thinking content should not be processed in render_message_content",
                flush=True,
            )
        elif type(item) is ImageContent:
            rendered_parts.append(format_image_content(item))  # type: ignore

    return "\n".join(rendered_parts)


def _format_type_counts(type_counts: dict[str, int]) -> str:
    """Format type counts into human-readable label.

    Args:
        type_counts: Dictionary of message type to count

    Returns:
        Human-readable label like "3 assistant, 4 tools" or "8 messages"

    Examples:
        {"assistant": 3, "tool_use": 4} -> "3 assistant, 4 tools"
        {"tool_use": 2, "tool_result": 2} -> "2 tool pairs"
        {"assistant": 1} -> "1 assistant"
        {"thinking": 3} -> "3 thoughts"
    """
    if not type_counts:
        return "0 messages"

    # Type name mapping for better readability
    type_labels = {
        "assistant": ("assistant", "assistants"),
        "user": ("user", "users"),
        "tool_use": ("tool", "tools"),
        "tool_result": ("result", "results"),
        "thinking": ("thought", "thoughts"),
        "system": ("system", "systems"),
        "system-warning": ("warning", "warnings"),
        "system-error": ("error", "errors"),
        "system-info": ("info", "infos"),
        "sidechain": ("task", "tasks"),
    }

    # Handle special case: tool_use and tool_result together = "tool pairs"
    # Create a modified counts dict that combines tool pairs
    modified_counts = dict(type_counts)
    if (
        "tool_use" in modified_counts
        and "tool_result" in modified_counts
        and modified_counts["tool_use"] == modified_counts["tool_result"]
    ):
        # Replace tool_use and tool_result with tool_pair
        pair_count = modified_counts["tool_use"]
        del modified_counts["tool_use"]
        del modified_counts["tool_result"]
        modified_counts["tool_pair"] = pair_count

    # Add tool_pair label
    type_labels_with_pairs = {
        **type_labels,
        "tool_pair": ("tool pair", "tool pairs"),
    }

    # Build label parts
    parts: list[str] = []
    for msg_type, count in sorted(
        modified_counts.items(), key=lambda x: x[1], reverse=True
    ):
        singular, plural = type_labels_with_pairs.get(
            msg_type, (msg_type, f"{msg_type}s")
        )
        label = singular if count == 1 else plural
        parts.append(f"{count} {label}")

    # Return combined label
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return f"{parts[0]}, {parts[1]}"
    else:
        # For 3+ types, show top 2 and "X more"
        remaining = sum(type_counts.values()) - sum(
            type_counts[t] for t in list(type_counts.keys())[:2]
        )
        return f"{parts[0]}, {parts[1]}, {remaining} more"


# -- Template Classes ---------------------------------------------------------


class TemplateMessage:
    """Structured message data for template rendering."""

    def __init__(
        self,
        message_type: str,
        content_html: str,
        formatted_timestamp: str,
        raw_timestamp: Optional[str] = None,
        session_summary: Optional[str] = None,
        session_id: Optional[str] = None,
        is_session_header: bool = False,
        token_usage: Optional[str] = None,
        tool_use_id: Optional[str] = None,
        title_hint: Optional[str] = None,
        has_markdown: bool = False,
        message_title: Optional[str] = None,
        message_id: Optional[str] = None,
        ancestry: Optional[List[str]] = None,
        has_children: bool = False,
        uuid: Optional[str] = None,
        parent_uuid: Optional[str] = None,
        agent_id: Optional[str] = None,
        modifiers: Optional[MessageModifiers] = None,
        content: Optional["MessageContent"] = None,
    ):
        self.type = message_type
        self.content_html = content_html
        # Structured content for format-neutral rendering (migration in progress)
        self.content = content
        self.formatted_timestamp = formatted_timestamp
        self.modifiers = modifiers if modifiers is not None else MessageModifiers()
        self.raw_timestamp = raw_timestamp
        # Display title for message header (capitalized, with decorations)
        self.message_title = (
            message_title if message_title is not None else message_type.title()
        )
        self.session_summary = session_summary
        self.session_id = session_id
        self.is_session_header = is_session_header
        self.session_subtitle: Optional[str] = None
        self.token_usage = token_usage
        self.tool_use_id = tool_use_id
        self.title_hint = title_hint
        self.message_id = message_id
        self.ancestry = ancestry or []
        self.has_children = has_children
        self.has_markdown = has_markdown
        self.uuid = uuid
        self.parent_uuid = parent_uuid
        self.agent_id = agent_id  # Agent ID for sidechain messages and Task results
        # Raw text content for deduplication (sidechain assistants vs Task results)
        self.raw_text_content: Optional[str] = None
        # Fold/unfold counts
        self.immediate_children_count = 0  # Direct children only
        self.total_descendants_count = 0  # All descendants recursively
        # Type-aware counting for smarter labels
        self.immediate_children_by_type: dict[
            str, int
        ] = {}  # {"assistant": 2, "tool_use": 3}
        self.total_descendants_by_type: dict[str, int] = {}  # All descendants by type
        # Pairing metadata
        self.is_paired = False
        self.pair_role: Optional[str] = None  # "pair_first", "pair_last", "pair_middle"
        self.pair_duration: Optional[str] = None  # Duration for pair_last messages
        # Children for tree-based rendering (future use)
        self.children: List["TemplateMessage"] = []

    def get_immediate_children_label(self) -> str:
        """Generate human-readable label for immediate children."""
        return _format_type_counts(self.immediate_children_by_type)

    def get_total_descendants_label(self) -> str:
        """Generate human-readable label for all descendants."""
        return _format_type_counts(self.total_descendants_by_type)

    def flatten(self) -> List["TemplateMessage"]:
        """Recursively flatten this message and all children into a list.

        Returns a list with this message followed by all descendants in
        depth-first order. This provides backward compatibility with the
        flat-list template rendering approach.
        """
        result: List["TemplateMessage"] = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result

    @staticmethod
    def flatten_all(messages: List["TemplateMessage"]) -> List["TemplateMessage"]:
        """Flatten a list of root messages into a single flat list.

        Useful for converting a tree structure back to a flat list for
        templates that expect the traditional flat message list.
        """
        result: List["TemplateMessage"] = []
        for message in messages:
            result.extend(message.flatten())
        return result


class TemplateProject:
    """Structured project data for template rendering."""

    def __init__(self, project_data: Dict[str, Any]):
        self.name = project_data["name"]
        self.html_file = project_data["html_file"]
        self.jsonl_count = project_data["jsonl_count"]
        self.message_count = project_data["message_count"]
        self.last_modified = project_data["last_modified"]
        self.total_input_tokens = project_data.get("total_input_tokens", 0)
        self.total_output_tokens = project_data.get("total_output_tokens", 0)
        self.total_cache_creation_tokens = project_data.get(
            "total_cache_creation_tokens", 0
        )
        self.total_cache_read_tokens = project_data.get("total_cache_read_tokens", 0)
        self.latest_timestamp = project_data.get("latest_timestamp", "")
        self.earliest_timestamp = project_data.get("earliest_timestamp", "")
        self.sessions = project_data.get("sessions", [])
        self.working_directories = project_data.get("working_directories", [])

        # Format display name using shared logic
        self.display_name = get_project_display_name(
            self.name, self.working_directories
        )

        # Format last modified date
        last_modified_dt = datetime.fromtimestamp(self.last_modified)
        self.formatted_date = last_modified_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Format interaction time range
        if self.earliest_timestamp and self.latest_timestamp:
            if self.earliest_timestamp == self.latest_timestamp:
                # Single interaction
                self.formatted_time_range = format_timestamp(self.latest_timestamp)
            else:
                # Time range
                earliest_formatted = format_timestamp(self.earliest_timestamp)
                latest_formatted = format_timestamp(self.latest_timestamp)
                self.formatted_time_range = (
                    f"{earliest_formatted} to {latest_formatted}"
                )
        elif self.latest_timestamp:
            self.formatted_time_range = format_timestamp(self.latest_timestamp)
        else:
            self.formatted_time_range = ""

        # Format last interaction timestamp (kept for backward compatibility)
        if self.latest_timestamp:
            self.formatted_last_interaction = format_timestamp(self.latest_timestamp)
        else:
            self.formatted_last_interaction = ""

        # Format token usage
        self.token_summary = ""
        if self.total_input_tokens > 0 or self.total_output_tokens > 0:
            token_parts: List[str] = []
            if self.total_input_tokens > 0:
                token_parts.append(f"Input: {self.total_input_tokens}")
            if self.total_output_tokens > 0:
                token_parts.append(f"Output: {self.total_output_tokens}")
            if self.total_cache_creation_tokens > 0:
                token_parts.append(
                    f"Cache Creation: {self.total_cache_creation_tokens}"
                )
            if self.total_cache_read_tokens > 0:
                token_parts.append(f"Cache Read: {self.total_cache_read_tokens}")
            self.token_summary = " | ".join(token_parts)


class TemplateSummary:
    """Summary statistics for template rendering."""

    def __init__(self, project_summaries: List[Dict[str, Any]]):
        self.total_projects = len(project_summaries)
        self.total_jsonl = sum(p["jsonl_count"] for p in project_summaries)
        self.total_messages = sum(p["message_count"] for p in project_summaries)

        # Calculate aggregated token usage
        self.total_input_tokens = sum(
            p.get("total_input_tokens", 0) for p in project_summaries
        )
        self.total_output_tokens = sum(
            p.get("total_output_tokens", 0) for p in project_summaries
        )
        self.total_cache_creation_tokens = sum(
            p.get("total_cache_creation_tokens", 0) for p in project_summaries
        )
        self.total_cache_read_tokens = sum(
            p.get("total_cache_read_tokens", 0) for p in project_summaries
        )

        # Find the most recent and earliest interaction timestamps across all projects
        self.latest_interaction = ""
        self.earliest_interaction = ""
        for project in project_summaries:
            # Check latest timestamp
            latest_timestamp = project.get("latest_timestamp", "")
            if latest_timestamp and (
                not self.latest_interaction
                or latest_timestamp > self.latest_interaction
            ):
                self.latest_interaction = latest_timestamp

            # Check earliest timestamp
            earliest_timestamp = project.get("earliest_timestamp", "")
            if earliest_timestamp and (
                not self.earliest_interaction
                or earliest_timestamp < self.earliest_interaction
            ):
                self.earliest_interaction = earliest_timestamp

        # Format the latest interaction timestamp
        if self.latest_interaction:
            self.formatted_latest_interaction = format_timestamp(
                self.latest_interaction
            )
        else:
            self.formatted_latest_interaction = ""

        # Format the time range
        if self.earliest_interaction and self.latest_interaction:
            if self.earliest_interaction == self.latest_interaction:
                # Single interaction
                self.formatted_time_range = format_timestamp(self.latest_interaction)
            else:
                # Time range
                earliest_formatted = format_timestamp(self.earliest_interaction)
                latest_formatted = format_timestamp(self.latest_interaction)
                self.formatted_time_range = (
                    f"{earliest_formatted} to {latest_formatted}"
                )
        else:
            self.formatted_time_range = ""

        # Format token usage summary
        self.token_summary = ""
        if self.total_input_tokens > 0 or self.total_output_tokens > 0:
            token_parts: List[str] = []
            if self.total_input_tokens > 0:
                token_parts.append(f"Input: {self.total_input_tokens}")
            if self.total_output_tokens > 0:
                token_parts.append(f"Output: {self.total_output_tokens}")
            if self.total_cache_creation_tokens > 0:
                token_parts.append(
                    f"Cache Creation: {self.total_cache_creation_tokens}"
                )
            if self.total_cache_read_tokens > 0:
                token_parts.append(f"Cache Read: {self.total_cache_read_tokens}")
            self.token_summary = " | ".join(token_parts)


# -- Template Generation ------------------------------------------------------


def generate_template_messages(
    messages: List[TranscriptEntry],
) -> Tuple[List[TemplateMessage], List[Dict[str, Any]]]:
    """Generate template messages and session navigation from transcript messages.

    This is the format-neutral rendering step that produces data structures
    ready for template rendering by any format-specific renderer.

    Args:
        messages: List of transcript entries to process.

    Returns:
        A tuple of (template_messages, session_nav) where:
        - template_messages: Processed messages ready for template rendering
        - session_nav: Session navigation data with summaries and metadata
    """
    from .utils import get_warmup_session_ids

    # Performance timing
    t_start = time.time()

    # Filter out warmup-only sessions
    with log_timing("Filter warmup sessions", t_start):
        warmup_session_ids = get_warmup_session_ids(messages)
        if warmup_session_ids:
            messages = [
                msg
                for msg in messages
                if getattr(msg, "sessionId", None) not in warmup_session_ids
            ]

    # Pre-process to find and attach session summaries
    with log_timing("Session summary processing", t_start):
        prepare_session_summaries(messages)

    # Process messages through the main rendering loop
    template_messages, sessions, session_order = _process_messages_loop(messages)

    # Prepare session navigation data
    with log_timing(
        lambda: f"Session navigation building ({len(session_nav)} sessions)", t_start
    ):
        session_nav = prepare_session_navigation(sessions, session_order)

    # Reorder messages so each session's messages follow their session header
    # This fixes interleaving that occurs when sessions are resumed
    with log_timing("Reorder session messages", t_start):
        template_messages = _reorder_session_template_messages(template_messages)

    # Identify and mark paired messages (command+output, tool_use+tool_result, etc.)
    with log_timing("Identify message pairs", t_start):
        _identify_message_pairs(template_messages)

    # Reorder messages so pairs are adjacent while preserving chronological order
    with log_timing("Reorder paired messages", t_start):
        template_messages = _reorder_paired_messages(template_messages)

    # Reorder sidechains to appear after their Task results
    # This must happen AFTER pair reordering, since that moves tool_results
    with log_timing("Reorder sidechain messages", t_start):
        template_messages = _reorder_sidechain_template_messages(template_messages)

    # Build hierarchy (message_id and ancestry) based on final order
    # This must happen AFTER all reordering to get correct parent-child relationships
    with log_timing("Build message hierarchy", t_start):
        _build_message_hierarchy(template_messages)

    # Mark messages that have children for fold/unfold controls
    with log_timing("Mark messages with children", t_start):
        _mark_messages_with_children(template_messages)

    # Build tree structure by populating children fields
    # This enables future recursive template rendering while maintaining
    # backward compatibility with the current flat-list approach
    with log_timing("Build message tree", t_start):
        _root_messages = _build_message_tree(template_messages)
        # Note: root_messages contains just the top-level messages with children populated
        # For now, we continue using template_messages (flat list) for template rendering
        # Future: pass root_messages to a recursive template macro

    return template_messages, session_nav


# -- Session Utilities --------------------------------------------------------


def prepare_session_summaries(messages: List[TranscriptEntry]) -> None:
    """Pre-process messages to find and attach session summaries.

    Modifies messages in place by attaching _session_summary attribute.
    """
    session_summaries: Dict[str, str] = {}
    uuid_to_session: Dict[str, str] = {}
    uuid_to_session_backup: Dict[str, str] = {}

    # Build mapping from message UUID to session ID
    for message in messages:
        if hasattr(message, "uuid") and hasattr(message, "sessionId"):
            message_uuid = getattr(message, "uuid", "")
            session_id = getattr(message, "sessionId", "")
            if message_uuid and session_id:
                # There is often duplication, in that case we want to prioritise the assistant
                # message because summaries are generated from Claude's (last) success message
                if type(message) is AssistantTranscriptEntry:
                    uuid_to_session[message_uuid] = session_id
                else:
                    uuid_to_session_backup[message_uuid] = session_id

    # Map summaries to sessions via leafUuid -> message UUID -> session ID
    for message in messages:
        if isinstance(message, SummaryTranscriptEntry):
            leaf_uuid = message.leafUuid
            if leaf_uuid in uuid_to_session:
                session_summaries[uuid_to_session[leaf_uuid]] = message.summary
            elif (
                leaf_uuid in uuid_to_session_backup
                and uuid_to_session_backup[leaf_uuid] not in session_summaries
            ):
                session_summaries[uuid_to_session_backup[leaf_uuid]] = message.summary

    # Attach summaries to messages
    for message in messages:
        if hasattr(message, "sessionId"):
            session_id = getattr(message, "sessionId", "")
            if session_id in session_summaries:
                setattr(message, "_session_summary", session_summaries[session_id])


def prepare_session_navigation(
    sessions: Dict[str, Dict[str, Any]],
    session_order: List[str],
) -> List[Dict[str, Any]]:
    """Prepare session navigation data for template rendering.

    Args:
        sessions: Dictionary mapping session_id to session info dict
        session_order: List of session IDs in display order

    Returns:
        List of session navigation dicts for template rendering
    """
    session_nav: List[Dict[str, Any]] = []

    for session_id in session_order:
        session_info = sessions[session_id]

        # Skip empty sessions (agent-only, no user messages)
        if not session_info["first_user_message"]:
            continue

        # Format timestamp range
        first_ts = session_info["first_timestamp"]
        last_ts = session_info["last_timestamp"]
        timestamp_range = format_timestamp_range(first_ts, last_ts)

        # Format token usage summary
        token_summary = ""
        total_input = session_info["total_input_tokens"]
        total_output = session_info["total_output_tokens"]
        total_cache_creation = session_info["total_cache_creation_tokens"]
        total_cache_read = session_info["total_cache_read_tokens"]

        if total_input > 0 or total_output > 0:
            token_parts: List[str] = []
            if total_input > 0:
                token_parts.append(f"Input: {total_input}")
            if total_output > 0:
                token_parts.append(f"Output: {total_output}")
            if total_cache_creation > 0:
                token_parts.append(f"Cache Creation: {total_cache_creation}")
            if total_cache_read > 0:
                token_parts.append(f"Cache Read: {total_cache_read}")
            token_summary = "Token usage – " + " | ".join(token_parts)

        session_nav.append(
            {
                "id": session_id,
                "summary": session_info["summary"],
                "timestamp_range": timestamp_range,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "message_count": session_info["message_count"],
                "first_user_message": session_info["first_user_message"]
                if session_info["first_user_message"] != ""
                else "[No user message found in session.]",
                "token_summary": token_summary,
            }
        )

    return session_nav


# -- Message Processing Functions ---------------------------------------------
# Note: HTML formatting logic has been moved to html/content_formatters.py
# as part of the refactoring to support format-neutral content models.


# def _process_summary_message(message: SummaryTranscriptEntry) -> tuple[str, str, str]:
#     """Process a summary message and return (css_class, content_html, message_type)."""
#     css_class = "summary"
#     content_html = f"<strong>Summary:</strong> {escape_html(str(message.summary))}"
#     message_type = "summary"
#     return css_class, content_html, message_type


def _process_command_message(
    text_content: str,
) -> tuple[MessageModifiers, str, str, str]:
    """Process a slash command message and return (modifiers, content_html, message_type, message_title).

    These are user messages containing slash command invocations (e.g., /context, /model).
    The JSONL type is "user", not "system".
    """
    modifiers = MessageModifiers(is_slash_command=True)

    # Parse and format using user_formatters
    slash_command = parse_slash_command(text_content)
    if slash_command:
        content_html = format_slash_command_content(slash_command)
    else:
        # Fallback to escaped text if parsing fails
        content_html = f"<pre>{escape_html(text_content)}</pre>"

    message_type = "user"
    message_title = "Slash Command"
    return modifiers, content_html, message_type, message_title


def _process_local_command_output(
    text_content: str,
) -> tuple[MessageModifiers, str, str, str]:
    """Process slash command output and return (modifiers, content_html, message_type, message_title).

    These are user messages containing the output from slash commands (e.g., /context, /model).
    The JSONL type is "user", not "system".
    """
    modifiers = MessageModifiers(is_command_output=True)

    # Parse and format using user_formatters
    command_output = parse_command_output(text_content)
    if command_output:
        content_html = format_command_output_content(command_output)
    else:
        content_html = escape_html(text_content)

    message_type = "user"
    message_title = "Command Output"
    return modifiers, content_html, message_type, message_title


def _process_bash_input(text_content: str) -> tuple[MessageModifiers, str, str, str]:
    """Process bash input command and return (modifiers, content_html, message_type, message_title)."""
    modifiers = MessageModifiers()  # bash-input is a message type, not a modifier

    # Parse and format using user_formatters
    bash_input = parse_bash_input(text_content)
    if bash_input:
        content_html = format_bash_input_content(bash_input)
    else:
        content_html = escape_html(text_content)

    message_type = "bash-input"
    message_title = "Bash"
    return modifiers, content_html, message_type, message_title


def _process_bash_output(text_content: str) -> tuple[MessageModifiers, str, str, str]:
    """Process bash output and return (modifiers, content_html, message_type, message_title)."""
    import re

    modifiers = MessageModifiers()  # bash-output is a message type, not a modifier
    COLLAPSE_THRESHOLD = 10  # Collapse if more than this many lines

    stdout_match = re.search(
        r"<bash-stdout>(.*?)</bash-stdout>",
        text_content,
        re.DOTALL,
    )
    stderr_match = re.search(
        r"<bash-stderr>(.*?)</bash-stderr>",
        text_content,
        re.DOTALL,
    )

    output_parts: List[tuple[str, str, int, str]] = []
    total_lines = 0

    if stdout_match:
        stdout_content = stdout_match.group(1).strip()
        if stdout_content:
            escaped_stdout = convert_ansi_to_html(stdout_content)
            stdout_lines = stdout_content.count("\n") + 1
            total_lines += stdout_lines
            output_parts.append(
                ("stdout", escaped_stdout, stdout_lines, stdout_content)
            )

    if stderr_match:
        stderr_content = stderr_match.group(1).strip()
        if stderr_content:
            escaped_stderr = convert_ansi_to_html(stderr_content)
            stderr_lines = stderr_content.count("\n") + 1
            total_lines += stderr_lines
            output_parts.append(
                ("stderr", escaped_stderr, stderr_lines, stderr_content)
            )

    if output_parts:
        # Build the HTML parts
        html_parts: List[str] = []
        for output_type, escaped_content, _, _ in output_parts:
            css_name = f"bash-{output_type}"
            html_parts.append(f"<pre class='{css_name}'>{escaped_content}</pre>")

        full_html = "".join(html_parts)

        # Wrap in collapsible if output is large
        if total_lines > COLLAPSE_THRESHOLD:
            # Create preview (first few lines)
            preview_lines = 3
            first_output = output_parts[0]
            raw_preview = "\n".join(first_output[3].split("\n")[:preview_lines])
            preview_html = html.escape(raw_preview)
            if total_lines > preview_lines:
                preview_html += "\n..."

            content_html = f"""<details class='collapsible-code'>
                <summary>
                    <span class='line-count'>{total_lines} lines</span>
                    <pre class='preview-content bash-stdout'>{preview_html}</pre>
                </summary>
                <div class='code-full'>{full_html}</div>
            </details>"""
        else:
            content_html = full_html
    else:
        # Empty output
        content_html = (
            "<pre class='bash-stdout'><span class='bash-empty'>(no output)</span></pre>"
        )

    message_type = "bash"
    message_title = "Bash"
    return modifiers, content_html, message_type, message_title


def _process_regular_message(
    text_only_content: List[ContentItem],
    message_type: str,
    is_sidechain: bool,
    is_meta: bool = False,
) -> tuple[MessageModifiers, str, str, str]:
    """Process regular message and return (modifiers, content_html, message_type, message_title).

    Note: Sidechain user messages (Sub-assistant prompts) are now skipped entirely
    in the main processing loop since they duplicate the Task tool input prompt.

    Args:
        is_meta: True for slash command expanded prompts (isMeta=True in JSONL)
    """
    message_title = message_type.title()  # Default title
    is_compacted = False
    is_slash_command = False
    is_memory_input = False

    # Handle user-specific preprocessing
    if message_type == MessageType.USER:
        # Note: sidechain user messages are skipped before reaching this function
        if is_meta:
            # Slash command expanded prompts - render as collapsible markdown
            # These contain LLM-generated instruction text (markdown formatted)
            is_slash_command = True
            message_title = "User (slash command)"
            # Combine all text content (items may be TextContent, dicts, or SDK objects)
            all_text = "\n\n".join(
                getattr(item, "text", "")
                for item in text_only_content
                if hasattr(item, "text")
            )
            content_html = render_markdown_collapsible(
                all_text,
                "slash-command-content",
                line_threshold=20,
                preview_line_count=5,
            )
        else:
            content_html, is_compacted, is_memory_input = render_user_message_content(
                text_only_content
            )
            if is_compacted:
                message_title = "User (compacted conversation)"
            elif is_memory_input:
                message_title = "Memory"
    else:
        # Non-user messages: render directly
        content_html = render_message_content(text_only_content, message_type)

    if is_sidechain:
        # Update message title for display (only non-user types reach here)
        if not is_compacted:
            message_title = "🔗 Sub-assistant"

    modifiers = MessageModifiers(
        is_sidechain=is_sidechain,
        is_slash_command=is_slash_command,
        is_compacted=is_compacted,
    )

    return modifiers, content_html, message_type, message_title


def _process_system_message(
    message: SystemTranscriptEntry,
) -> Optional[TemplateMessage]:
    """Process a system message and return a TemplateMessage, or None if it should be skipped.

    Handles:
    - Hook summaries (subtype="stop_hook_summary")
    - Other system messages with level-specific styling (info, warning, error)

    Note: Slash command messages (<command-name>, <local-command-stdout>) are user messages,
    not system messages. They are handled by _process_command_message and
    _process_local_command_output in the main processing loop.
    """
    from .models import MessageContent  # Local import to avoid circular dependency

    session_id = getattr(message, "sessionId", "unknown")
    timestamp = getattr(message, "timestamp", "")
    formatted_timestamp = format_timestamp(timestamp) if timestamp else ""

    # Build structured content based on message subtype
    content: MessageContent
    if message.subtype == "stop_hook_summary":
        # Skip silent hook successes (no output, no errors)
        if not message.hasOutput and not message.hookErrors:
            return None
        # Create structured hook summary content
        hook_infos = [
            HookInfo(command=info.get("command", "unknown"))
            for info in (message.hookInfos or [])
        ]
        content = HookSummaryContent(
            has_output=bool(message.hasOutput),
            hook_errors=message.hookErrors or [],
            hook_infos=hook_infos,
        )
        level = "hook"
    elif not message.content:
        # Skip system messages without content (shouldn't happen normally)
        return None
    else:
        # Create structured system content
        level = getattr(message, "level", "info")
        content = SystemContent(level=level, text=message.content)

    # Store parent UUID for hierarchy rebuild (handled by _build_message_hierarchy)
    parent_uuid = getattr(message, "parentUuid", None)

    # Note: content_html will be populated by HtmlRenderer from content
    return TemplateMessage(
        message_type="system",
        content_html="",  # Populated by renderer from content
        content=content,
        formatted_timestamp=formatted_timestamp,
        raw_timestamp=timestamp,
        session_id=session_id,
        message_title=f"System {level.title()}",
        message_id=None,  # Will be assigned by _build_message_hierarchy
        ancestry=[],  # Will be assigned by _build_message_hierarchy
        uuid=message.uuid,
        parent_uuid=parent_uuid,
        modifiers=MessageModifiers(system_level=level),
    )


@dataclass
class ToolItemResult:
    """Result of processing a single tool/thinking/image item."""

    message_type: str
    content_html: str
    message_title: str
    tool_use_id: Optional[str] = None
    title_hint: Optional[str] = None
    pending_dedup: Optional[str] = None  # For Task result deduplication
    is_error: bool = False  # For tool_result error state


def _process_tool_use_item(
    tool_item: ContentItem,
    tool_use_context: Dict[str, ToolUseContent],
) -> Optional[ToolItemResult]:
    """Process a tool_use content item.

    Args:
        tool_item: The tool use content item
        tool_use_context: Dict to populate with tool_use_id -> ToolUseContent mapping

    Returns:
        ToolItemResult with processed content, or None if item should be skipped
    """
    # Convert Anthropic type to our format if necessary
    if not isinstance(tool_item, ToolUseContent):
        tool_use = ToolUseContent(
            type="tool_use",
            id=getattr(tool_item, "id", ""),
            name=getattr(tool_item, "name", ""),
            input=getattr(tool_item, "input", {}),
        )
    else:
        tool_use = tool_item

    tool_content_html = format_tool_use_content(tool_use)
    tool_message_title = format_tool_use_title(tool_use)
    escaped_id = escape_html(tool_use.id)
    item_tool_use_id = tool_use.id
    tool_title_hint = f"ID: {escaped_id}"

    # Populate tool_use_context for later use when processing tool results
    tool_use_context[item_tool_use_id] = tool_use

    return ToolItemResult(
        message_type="tool_use",
        content_html=tool_content_html,
        message_title=tool_message_title,
        tool_use_id=item_tool_use_id,
        title_hint=tool_title_hint,
    )


def _process_tool_result_item(
    tool_item: ContentItem,
    tool_use_context: Dict[str, ToolUseContent],
) -> Optional[ToolItemResult]:
    """Process a tool_result content item.

    Args:
        tool_item: The tool result content item
        tool_use_context: Dict with tool_use_id -> ToolUseContent mapping

    Returns:
        ToolItemResult with processed content, or None if item should be skipped
    """
    # Convert Anthropic type to our format if necessary
    if not isinstance(tool_item, ToolResultContent):
        tool_result = ToolResultContent(
            type="tool_result",
            tool_use_id=getattr(tool_item, "tool_use_id", ""),
            content=getattr(tool_item, "content", ""),
            is_error=getattr(tool_item, "is_error", False),
        )
    else:
        tool_result = tool_item

    # Get file_path and tool_name from tool_use context for specialized rendering
    result_file_path: Optional[str] = None
    result_tool_name: Optional[str] = None
    if tool_result.tool_use_id in tool_use_context:
        tool_use_from_ctx = tool_use_context[tool_result.tool_use_id]
        result_tool_name = tool_use_from_ctx.name
        if (
            result_tool_name in ("Read", "Edit", "Write")
            and "file_path" in tool_use_from_ctx.input
        ):
            result_file_path = tool_use_from_ctx.input["file_path"]

    tool_content_html = format_tool_result_content(
        tool_result, result_file_path, result_tool_name
    )

    # Retroactive deduplication: if Task result, extract content for later matching
    pending_dedup: Optional[str] = None
    if result_tool_name == "Task":
        # Extract text content from tool result
        # Note: tool_result.content can be str or List[Dict[str, Any]]
        if isinstance(tool_result.content, str):
            task_result_content = tool_result.content.strip()
        else:
            # Handle list of dicts (tool result format)
            content_parts: list[str] = []
            for item in tool_result.content:
                text_val = item.get("text", "")
                if isinstance(text_val, str):
                    content_parts.append(text_val)
            task_result_content = "\n".join(content_parts).strip()
        pending_dedup = task_result_content if task_result_content else None

    escaped_id = escape_html(tool_result.tool_use_id)
    tool_title_hint = f"ID: {escaped_id}"
    tool_message_title = "Error" if tool_result.is_error else ""

    return ToolItemResult(
        message_type="tool_result",
        content_html=tool_content_html,
        message_title=tool_message_title,
        tool_use_id=tool_result.tool_use_id,
        title_hint=tool_title_hint,
        pending_dedup=pending_dedup,
        is_error=tool_result.is_error or False,
    )


def _process_thinking_item(tool_item: ContentItem) -> Optional[ToolItemResult]:
    """Process a thinking content item.

    Returns:
        ToolItemResult with processed content
    """
    # Extract thinking text from the content item
    if isinstance(tool_item, ThinkingContent):
        thinking_text = tool_item.thinking.strip()
        signature = getattr(tool_item, "signature", None)
    else:
        thinking_text = getattr(tool_item, "thinking", str(tool_item)).strip()
        signature = None

    # Create the content model and format
    thinking_model = ThinkingContentModel(thinking=thinking_text, signature=signature)

    return ToolItemResult(
        message_type="thinking",
        content_html=format_thinking_content(thinking_model, line_threshold=10),
        message_title="Thinking",
    )


def _process_image_item(tool_item: ContentItem) -> Optional[ToolItemResult]:
    """Process an image content item.

    Returns:
        ToolItemResult with processed content, or None if item should be skipped
    """
    # Convert Anthropic type to our format if necessary
    if not isinstance(tool_item, ImageContent):
        # For now, skip Anthropic image types - we'll handle when we encounter them
        return None

    return ToolItemResult(
        message_type="image",
        content_html=format_image_content(tool_item),
        message_title="Image",
    )


# -- Message Pairing ----------------------------------------------------------


@dataclass
class PairingIndices:
    """Indices for efficient message pairing lookups.

    All indices are built in a single pass for efficiency.
    """

    # (session_id, tool_use_id) -> message index for tool_use messages
    tool_use: Dict[tuple[str, str], int]
    # (session_id, tool_use_id) -> message index for tool_result messages
    tool_result: Dict[tuple[str, str], int]
    # uuid -> message index for system messages (parent-child pairing)
    uuid: Dict[str, int]
    # parent_uuid -> message index for slash-command messages
    slash_command_by_parent: Dict[str, int]


def _build_pairing_indices(messages: List[TemplateMessage]) -> PairingIndices:
    """Build indices for efficient message pairing lookups.

    Single pass through messages to build all indices needed for pairing.
    """
    tool_use_index: Dict[tuple[str, str], int] = {}
    tool_result_index: Dict[tuple[str, str], int] = {}
    uuid_index: Dict[str, int] = {}
    slash_command_by_parent: Dict[str, int] = {}

    for i, msg in enumerate(messages):
        # Index tool_use and tool_result by (session_id, tool_use_id)
        if msg.tool_use_id and msg.session_id:
            key = (msg.session_id, msg.tool_use_id)
            if msg.type == "tool_use":
                tool_use_index[key] = i
            elif msg.type == "tool_result":
                tool_result_index[key] = i

        # Index system messages by UUID for parent-child pairing
        if msg.uuid and msg.type == "system":
            uuid_index[msg.uuid] = i

        # Index slash-command user messages by parent_uuid
        if msg.parent_uuid and msg.modifiers.is_slash_command:
            slash_command_by_parent[msg.parent_uuid] = i

    return PairingIndices(
        tool_use=tool_use_index,
        tool_result=tool_result_index,
        uuid=uuid_index,
        slash_command_by_parent=slash_command_by_parent,
    )


def _mark_pair(first: TemplateMessage, last: TemplateMessage) -> None:
    """Mark two messages as a pair."""
    first.is_paired = True
    first.pair_role = "pair_first"
    last.is_paired = True
    last.pair_role = "pair_last"


def _try_pair_adjacent(
    current: TemplateMessage,
    next_msg: TemplateMessage,
) -> bool:
    """Try to pair adjacent messages based on their types.

    Returns True if messages were paired, False otherwise.

    Adjacent pairing rules:
    - user slash-command + user command-output
    - bash-input + bash-output
    - thinking + assistant
    """
    # Slash command + command output (both are user messages)
    if current.modifiers.is_slash_command and next_msg.modifiers.is_command_output:
        _mark_pair(current, next_msg)
        return True

    # Bash input + bash output
    if current.type == "bash-input" and next_msg.type == "bash-output":
        _mark_pair(current, next_msg)
        return True

    # Thinking + assistant
    if current.type == "thinking" and next_msg.type == "assistant":
        _mark_pair(current, next_msg)
        return True

    return False


def _try_pair_by_index(
    current: TemplateMessage,
    messages: List[TemplateMessage],
    indices: PairingIndices,
) -> None:
    """Try to pair current message with another using index lookups.

    Index-based pairing rules (can be any distance apart):
    - tool_use + tool_result (by tool_use_id within same session)
    - system parent + system child (by uuid/parent_uuid)
    - system + slash-command (by uuid -> parent_uuid)
    """
    # Tool use + tool result (by tool_use_id within same session)
    if current.type == "tool_use" and current.tool_use_id and current.session_id:
        key = (current.session_id, current.tool_use_id)
        if key in indices.tool_result:
            result_msg = messages[indices.tool_result[key]]
            _mark_pair(current, result_msg)

    # System child message finding its parent (by parent_uuid)
    if current.type == "system" and current.parent_uuid:
        if current.parent_uuid in indices.uuid:
            parent_msg = messages[indices.uuid[current.parent_uuid]]
            _mark_pair(parent_msg, current)

    # System command finding its slash-command child (by uuid -> parent_uuid)
    if current.type == "system" and current.uuid:
        if current.uuid in indices.slash_command_by_parent:
            slash_msg = messages[indices.slash_command_by_parent[current.uuid]]
            _mark_pair(current, slash_msg)


def _identify_message_pairs(messages: List[TemplateMessage]) -> None:
    """Identify and mark paired messages (e.g., command + output, tool use + result).

    Modifies messages in-place by setting is_paired and pair_role fields.

    Uses a two-pass algorithm:
    1. First pass: Build indices for efficient lookups (tool_use_id, uuid, parent_uuid)
    2. Second pass: Sequential scan for adjacent pairs and index-based pairs

    Pairing types:
    - Adjacent: system+output, bash-input+output, thinking+assistant
    - Indexed: tool_use+result (by ID), system parent+child (by UUID)
    """
    # Pass 1: Build all indices for efficient lookups
    indices = _build_pairing_indices(messages)

    # Pass 2: Sequential scan to identify pairs
    i = 0
    while i < len(messages):
        current = messages[i]

        # Skip session headers
        if current.is_session_header:
            i += 1
            continue

        # Try adjacent pairing first (can skip next message if paired)
        if i + 1 < len(messages):
            next_msg = messages[i + 1]
            if _try_pair_adjacent(current, next_msg):
                i += 2
                continue

        # Try index-based pairing (doesn't skip, continues to next message)
        _try_pair_by_index(current, messages, indices)

        i += 1


def _reorder_paired_messages(messages: List[TemplateMessage]) -> List[TemplateMessage]:
    """Reorder messages so paired messages are adjacent while preserving chronological order.

    - Unpaired messages and first messages in pairs maintain chronological order
    - Last messages in pairs are moved immediately after their first message
    - Timestamps are enhanced to show duration for paired messages

    Uses dictionary-based approach to find pairs efficiently:
    1. Build index of all pair_last messages by tool_use_id
    2. Build index of slash-command pair_last messages by parent_uuid
    3. Single pass through messages, inserting pair_last immediately after pair_first
    """
    from datetime import datetime

    # Build index of pair_last messages by (session_id, tool_use_id)
    # Session ID is included to prevent cross-session pairing when sessions are resumed
    pair_last_index: Dict[
        tuple[str, str], int
    ] = {}  # (session_id, tool_use_id) -> message index
    # Index slash-command pair_last messages by parent_uuid
    slash_command_pair_index: Dict[str, int] = {}  # parent_uuid -> message index

    for i, msg in enumerate(messages):
        if (
            msg.is_paired
            and msg.pair_role == "pair_last"
            and msg.tool_use_id
            and msg.session_id
        ):
            key = (msg.session_id, msg.tool_use_id)
            pair_last_index[key] = i
        # Index slash-command messages by parent_uuid
        if (
            msg.is_paired
            and msg.pair_role == "pair_last"
            and msg.parent_uuid
            and msg.modifiers.is_slash_command
        ):
            slash_command_pair_index[msg.parent_uuid] = i

    # Create reordered list
    reordered: List[TemplateMessage] = []
    skip_indices: set[int] = set()

    for i, msg in enumerate(messages):
        if i in skip_indices:
            continue

        reordered.append(msg)

        # If this is the first message in a pair, immediately add its pair_last
        # Key includes session_id to prevent cross-session pairing on resume
        if msg.is_paired and msg.pair_role == "pair_first":
            pair_last = None
            last_idx = None

            # Check for tool_use_id based pairs
            if msg.tool_use_id and msg.session_id:
                key = (msg.session_id, msg.tool_use_id)
                if key in pair_last_index:
                    last_idx = pair_last_index[key]
                    pair_last = messages[last_idx]

            # Check for system + slash-command pairs (via uuid -> parent_uuid)
            if pair_last is None and msg.uuid and msg.uuid in slash_command_pair_index:
                last_idx = slash_command_pair_index[msg.uuid]
                pair_last = messages[last_idx]

            if pair_last is not None and last_idx is not None:
                reordered.append(pair_last)
                skip_indices.add(last_idx)

                # Calculate duration between pair messages
                try:
                    if msg.raw_timestamp and pair_last.raw_timestamp:
                        # Parse ISO timestamps
                        first_time = datetime.fromisoformat(
                            msg.raw_timestamp.replace("Z", "+00:00")
                        )
                        last_time = datetime.fromisoformat(
                            pair_last.raw_timestamp.replace("Z", "+00:00")
                        )
                        duration = last_time - first_time

                        # Format duration nicely
                        total_seconds = duration.total_seconds()
                        if total_seconds < 1:
                            duration_str = f"took {int(total_seconds * 1000)} ms"
                        elif total_seconds < 60:
                            duration_str = f"took {total_seconds:.1f}s"
                        else:
                            minutes = int(total_seconds // 60)
                            seconds = int(total_seconds % 60)
                            duration_str = f"took {minutes}m {seconds}s"

                        # Store duration in pair_last for template rendering
                        pair_last.pair_duration = duration_str
                except (ValueError, AttributeError):
                    pass

    return reordered


# -- Message Hierarchy --------------------------------------------------------


def _get_message_hierarchy_level(msg: TemplateMessage) -> int:
    """Determine the hierarchy level for a message based on its type and modifiers.

    Correct hierarchy based on logical nesting:
    - Level 0: Session headers
    - Level 1: User messages
    - Level 2: System commands/errors, Assistant, Thinking
    - Level 3: Tool use/result, System info/warning (nested under assistant)
    - Level 4: Sidechain assistant/thinking (nested under Task tool result)
    - Level 5: Sidechain tools (nested under sidechain assistant)

    Note: Sidechain user messages (Sub-assistant prompts) are now skipped entirely
    since they duplicate the Task tool input prompt.

    Returns:
        Integer hierarchy level (1-5, session headers are 0)
    """
    msg_type = msg.type
    is_sidechain = msg.modifiers.is_sidechain
    system_level = msg.modifiers.system_level

    # User messages at level 1 (under session)
    # Note: sidechain user messages are skipped before reaching this function
    if msg_type == "user" and not is_sidechain:
        return 1

    # System info/warning at level 3 (tool-related, e.g., hook notifications)
    if (
        msg_type == "system"
        and system_level in ("info", "warning")
        and not is_sidechain
    ):
        return 3

    # System commands/errors at level 2 (siblings to assistant)
    if msg_type == "system" and not is_sidechain:
        return 2

    # Sidechain assistant/thinking at level 4 (nested under Task tool result)
    if is_sidechain and msg_type in ("assistant", "thinking"):
        return 4

    # Sidechain tools at level 5
    if is_sidechain and msg_type in ("tool_use", "tool_result"):
        return 5

    # Main assistant/thinking at level 2 (nested under user)
    if msg_type in ("assistant", "thinking"):
        return 2

    # Main tools at level 3 (nested under assistant)
    if msg_type in ("tool_use", "tool_result"):
        return 3

    # Default to level 1
    return 1


def _build_message_hierarchy(messages: List[TemplateMessage]) -> None:
    """Build message_id and ancestry for all messages based on their current order.

    This should be called after all reordering operations (pair reordering, sidechain
    reordering) to ensure the hierarchy reflects the final display order.

    The hierarchy is determined by message type using _get_message_hierarchy_level(),
    and a stack-based approach builds proper parent-child relationships.

    Args:
        messages: List of template messages in their final order (modified in place)
    """
    hierarchy_stack: List[tuple[int, str]] = []
    message_id_counter = 0

    for message in messages:
        # Session headers are level 0
        if message.is_session_header:
            current_level = 0
        else:
            # Determine level from message type and modifiers
            current_level = _get_message_hierarchy_level(message)

        # Pop stack until we find the appropriate parent level
        while hierarchy_stack and hierarchy_stack[-1][0] >= current_level:
            hierarchy_stack.pop()

        # Build ancestry from remaining stack
        ancestry = [msg_id for _, msg_id in hierarchy_stack]

        # Generate new message ID
        # Session headers use session-{session_id} format for navigation links
        if message.is_session_header and message.session_id:
            message_id = f"session-{message.session_id}"
        else:
            message_id = f"d-{message_id_counter}"
            message_id_counter += 1

        # Push current message onto stack
        hierarchy_stack.append((current_level, message_id))

        # Update the message
        message.message_id = message_id
        message.ancestry = ancestry


def _mark_messages_with_children(messages: List[TemplateMessage]) -> None:
    """Mark messages that have children and calculate descendant counts.

    Efficiently calculates:
    - has_children: Whether message has any children
    - immediate_children_count: Count of direct children only
    - total_descendants_count: Count of all descendants recursively

    Time complexity: O(n) where n is the number of messages.

    Args:
        messages: List of template messages to process
    """
    # Build index of messages by ID for O(1) lookup
    message_by_id: dict[str, TemplateMessage] = {}
    for message in messages:
        if message.message_id:
            message_by_id[message.message_id] = message

    # Process each message and update counts for ancestors
    for message in messages:
        if not message.ancestry:
            continue  # Top-level message, no parents

        # Skip counting pair_last messages (second in a pair)
        # Pairs are visually presented as a single unit, so we only count the first
        if message.is_paired and message.pair_role == "pair_last":
            continue

        # Get immediate parent (last in ancestry list)
        immediate_parent_id = message.ancestry[-1]

        # Get message type for categorization
        msg_type = message.type

        # Increment immediate parent's child count
        if immediate_parent_id in message_by_id:
            parent = message_by_id[immediate_parent_id]
            parent.immediate_children_count += 1
            parent.has_children = True
            # Track by type
            parent.immediate_children_by_type[msg_type] = (
                parent.immediate_children_by_type.get(msg_type, 0) + 1
            )

        # Increment descendant count for ALL ancestors
        for ancestor_id in message.ancestry:
            if ancestor_id in message_by_id:
                ancestor = message_by_id[ancestor_id]
                ancestor.total_descendants_count += 1
                # Track by type
                ancestor.total_descendants_by_type[msg_type] = (
                    ancestor.total_descendants_by_type.get(msg_type, 0) + 1
                )


def _build_message_tree(messages: List[TemplateMessage]) -> List[TemplateMessage]:
    """Build tree structure by populating children fields based on ancestry.

    This function takes a flat list of messages (with message_id and ancestry
    already set by _build_message_hierarchy) and populates the children field
    of each message to form an explicit tree structure.

    The tree structure enables:
    - Recursive template rendering with nested DOM elements
    - Simpler JavaScript fold/unfold (just hide/show children container)
    - More natural parent-child traversal

    Args:
        messages: List of template messages with message_id and ancestry set

    Returns:
        List of root messages (those with empty ancestry). Each message's
        children field is populated with its direct children.
    """
    # Build index of messages by ID for O(1) lookup
    message_by_id: dict[str, TemplateMessage] = {}
    for message in messages:
        if message.message_id:
            message_by_id[message.message_id] = message

    # Clear any existing children (in case of re-processing)
    for message in messages:
        message.children = []

    # Collect root messages (those with no ancestry)
    root_messages: List[TemplateMessage] = []

    # Populate children based on ancestry
    for message in messages:
        if not message.ancestry:
            # Root message (level 0, no parent)
            root_messages.append(message)
        else:
            # Has a parent - add to parent's children
            immediate_parent_id = message.ancestry[-1]
            if immediate_parent_id in message_by_id:
                parent = message_by_id[immediate_parent_id]
                parent.children.append(message)

    return root_messages


# -- Message Reordering -------------------------------------------------------


def _reorder_session_template_messages(
    messages: List[TemplateMessage],
) -> List[TemplateMessage]:
    """Reorder template messages to group all messages under their correct session headers.

    When a user resumes session A into session B, Claude Code copies messages from
    session A into session B's JSONL file (keeping their original sessionId). After
    global chronological sorting, these copied messages get interleaved. This function
    fixes that by grouping all messages by session_id and inserting them after their
    corresponding session header.

    This must be called BEFORE _identify_message_pairs and _reorder_paired_messages,
    since those functions expect messages to be in session-grouped order.

    Args:
        messages: Template messages (including session headers)

    Returns:
        Reordered messages with all messages grouped under their session headers
    """
    # First pass: extract session headers and group non-header messages by session_id
    session_headers: List[TemplateMessage] = []
    session_messages_map: Dict[str, List[TemplateMessage]] = {}

    for message in messages:
        if message.is_session_header:
            session_headers.append(message)
            # Initialize the list for this session (preserves session order)
            if message.session_id and message.session_id not in session_messages_map:
                session_messages_map[message.session_id] = []
        else:
            session_id = message.session_id
            if session_id:
                if session_id not in session_messages_map:
                    session_messages_map[session_id] = []
                session_messages_map[session_id].append(message)

    # If no session headers, return original order
    if not session_headers:
        return messages

    # Second pass: for each session header, insert all messages with that session_id
    result: List[TemplateMessage] = []
    used_sessions: set[str] = set()

    for header in session_headers:
        result.append(header)
        session_id = header.session_id

        if session_id and session_id in session_messages_map:
            # Messages are already in timestamp order from original processing
            result.extend(session_messages_map[session_id])
            used_sessions.add(session_id)

    # Append any messages that weren't matched to a session header (shouldn't happen normally)
    for session_id, msgs in session_messages_map.items():
        if session_id not in used_sessions:
            result.extend(msgs)

    return result


def _reorder_sidechain_template_messages(
    messages: List[TemplateMessage],
) -> List[TemplateMessage]:
    """Reorder template messages to place sidechains immediately after their Task results.

    When parallel Task agents run, their sidechain messages may appear in arbitrary
    order based on when each agent finishes. This function reorders messages so that
    each sidechain's messages appear right after the Task result that references them.

    This function also handles deduplication: the last sidechain assistant message
    typically contains the same content as the Task result, so we replace it with
    a forward link to avoid showing the same content twice.

    This must be called AFTER _reorder_paired_messages, since that function moves
    tool_results next to their tool_uses, which changes where the agentId-bearing
    messages end up.

    Args:
        messages: Template messages including sidechains

    Returns:
        Reordered messages with sidechains properly placed after their Task results
    """
    # First pass: extract sidechains grouped by agent_id
    main_messages: List[TemplateMessage] = []
    sidechain_map: Dict[str, List[TemplateMessage]] = {}

    for message in messages:
        is_sidechain = message.modifiers.is_sidechain
        agent_id = message.agent_id

        if is_sidechain and agent_id:
            # Group sidechain messages by agent_id
            if agent_id not in sidechain_map:
                sidechain_map[agent_id] = []
            sidechain_map[agent_id].append(message)
        else:
            main_messages.append(message)

    # If no sidechains, return original order
    if not sidechain_map:
        return messages

    # Second pass: insert sidechains after their Task result messages
    # Also perform deduplication of sidechain assistants vs Task results
    result: List[TemplateMessage] = []
    used_agents: set[str] = set()

    for message in main_messages:
        result.append(message)

        # Check if this is a Task tool_result that references a sidechain (via agent_id)
        # We only insert after tool_result (not tool_use) to avoid duplicates if
        # tool_use ever gets agent_id in the future
        agent_id = message.agent_id

        if (
            agent_id
            and message.type == MessageType.TOOL_RESULT
            and agent_id in sidechain_map
        ):
            sidechain_msgs = sidechain_map[agent_id]

            # Deduplicate: find the last sidechain assistant with text content
            # that matches the Task result content
            task_result_content = (
                message.raw_text_content.strip() if message.raw_text_content else None
            )
            if task_result_content and message.type == MessageType.TOOL_RESULT:
                # Find the last assistant message in this sidechain
                for sidechain_msg in reversed(sidechain_msgs):
                    sidechain_text = (
                        sidechain_msg.raw_text_content.strip()
                        if sidechain_msg.raw_text_content
                        else None
                    )
                    if (
                        sidechain_msg.type == MessageType.ASSISTANT
                        and sidechain_text
                        and sidechain_text == task_result_content
                    ):
                        # Replace with note pointing to the Task result
                        forward_link_html = "<p><em>(Task summary — already displayed in Task tool result above)</em></p>"
                        sidechain_msg.content_html = forward_link_html
                        # Mark as deduplicated for potential debugging
                        sidechain_msg.raw_text_content = None
                        break

            # Insert the sidechain messages for this agent right after this message
            # Note: ancestry will be rebuilt by _build_message_hierarchy() later
            result.extend(sidechain_msgs)
            used_agents.add(agent_id)

    # Append any sidechains that weren't matched (shouldn't happen normally)
    for agent_id, sidechain_msgs in sidechain_map.items():
        if agent_id not in used_agents:
            result.extend(sidechain_msgs)

    return result


def _process_messages_loop(
    messages: List[TranscriptEntry],
) -> tuple[
    List[TemplateMessage],
    Dict[str, Dict[str, Any]],  # sessions
    List[str],  # session_order
]:
    """Process messages through the main rendering loop.

    This function handles the core message processing logic:
    - Processes each message into template-friendly format
    - Tracks sessions and token usage
    - Handles message deduplication and hierarchy
    - Collects timing statistics

    Note: Tool use context must be built before calling this function via
    _define_tool_use_context()

    Args:
        messages: List of transcript entries to process

    Returns:
        Tuple containing:
        - template_messages: Processed messages ready for template rendering
        - sessions: Session metadata dict mapping session_id to info
        - session_order: List of session IDs in chronological order
    """
    # Group messages by session and collect session info for navigation
    sessions: Dict[str, Dict[str, Any]] = {}
    session_order: List[str] = []
    seen_sessions: set[str] = set()

    # Track requestIds to avoid double-counting token usage
    seen_request_ids: set[str] = set()
    # Track which messages should show token usage (first occurrence of each requestId)
    show_tokens_for_message: set[str] = set()

    # Build mapping of tool_use_id to ToolUseContent for specialized tool result rendering
    # This will be populated inline as we encounter tool_use items during message processing
    tool_use_context: Dict[str, ToolUseContent] = {}

    # Process messages into template-friendly format
    template_messages: List[TemplateMessage] = []

    # Per-message timing tracking
    message_timings: List[
        tuple[float, str, int, str]
    ] = []  # (duration, message_type, index, uuid)

    # Track expensive operations
    markdown_timings: List[tuple[float, str]] = []  # (duration, context_uuid)
    pygments_timings: List[tuple[float, str]] = []  # (duration, context_uuid)

    # Initialize timing tracking
    set_timing_var("_markdown_timings", markdown_timings)
    set_timing_var("_pygments_timings", pygments_timings)
    set_timing_var("_current_msg_uuid", "")

    for msg_idx, message in enumerate(messages):
        msg_start_time = time.time() if DEBUG_TIMING else 0.0
        message_type = message.type
        msg_uuid = getattr(message, "uuid", f"no-uuid-{msg_idx}")

        # Update current message UUID for timing tracking
        set_timing_var("_current_msg_uuid", msg_uuid)

        # NOTE: Sidechain user messages are handled below after content extraction
        # to distinguish prompts (skip) from tool results (render)

        # Skip summary messages - they should already be attached to their sessions
        if isinstance(message, SummaryTranscriptEntry):
            continue

        # Skip most queue operations - only render 'remove' as steering user messages
        if isinstance(message, QueueOperationTranscriptEntry):
            if message.operation != "remove":
                continue
            # 'remove' operations fall through to be rendered as user messages

        # Handle system messages separately
        if isinstance(message, SystemTranscriptEntry):
            system_template_message = _process_system_message(message)
            if system_template_message:
                template_messages.append(system_template_message)
            continue

        # Handle queue-operation 'remove' messages as user messages
        if isinstance(message, QueueOperationTranscriptEntry):
            # Queue operations have content directly, not in message.message
            message_content = message.content if message.content else []
            # Treat as user message type
            message_type = MessageType.QUEUE_OPERATION
        else:
            # Extract message content first to check for duplicates
            # Must be UserTranscriptEntry or AssistantTranscriptEntry
            message_content = message.message.content  # type: ignore

        text_content = extract_text_content(message_content)

        # Separate tool/thinking/image content from text content
        # Images in user messages stay inline, images in assistant messages are separate
        tool_items: List[ContentItem] = []
        text_only_content: List[ContentItem] = []

        if isinstance(message_content, list):
            text_only_items: List[ContentItem] = []
            for item in message_content:
                # Check for both custom types and Anthropic types
                item_type = getattr(item, "type", None)
                is_image = isinstance(item, ImageContent) or item_type == "image"
                is_tool_item = isinstance(
                    item,
                    (ToolUseContent, ToolResultContent, ThinkingContent),
                ) or item_type in ("tool_use", "tool_result", "thinking")

                # Keep images inline for user messages and queue operations (steering),
                # extract for assistant messages
                if is_image and (
                    message_type == MessageType.USER
                    or isinstance(message, QueueOperationTranscriptEntry)
                ):
                    text_only_items.append(item)
                elif is_tool_item or is_image:
                    tool_items.append(item)
                else:
                    text_only_items.append(item)
            text_only_content = text_only_items
        else:
            # Single string content
            message_content = message_content.strip()
            if message_content:
                text_only_content = [TextContent(type="text", text=message_content)]

        # Skip if no meaningful content
        if not text_content.strip() and not tool_items:
            continue

        # Skip messages that should be filtered out
        if should_skip_message(text_content):
            continue

        # Skip sidechain user messages that are just prompts (no tool results)
        # Sidechain prompts duplicate the Task tool input and are redundant,
        # but tool results from sidechain agents should be rendered
        if message_type == MessageType.USER and getattr(message, "isSidechain", False):
            has_tool_results = any(
                getattr(item, "type", None) == "tool_result"
                or isinstance(item, ToolResultContent)
                for item in tool_items
            )
            if not has_tool_results:
                continue
            # For sidechain user messages with tool results, clear text content
            # to avoid rendering the redundant prompt text
            text_only_content = []
            text_content = ""

        # Check message types for special handling
        is_command = is_command_message(text_content)
        is_local_output = is_local_command_output(text_content)
        is_bash_cmd = is_bash_input(text_content)
        is_bash_result = is_bash_output(text_content)

        # Check if we're in a new session
        session_id = getattr(message, "sessionId", "unknown")
        session_summary = getattr(message, "_session_summary", None)

        # Track sessions for navigation and add session header if new
        if session_id not in sessions:
            # Get the session summary for this session (may be None)
            current_session_summary = getattr(message, "_session_summary", None)

            # Get first user message content for preview
            first_user_message = ""
            if is_user_entry(message) and should_use_as_session_starter(text_content):
                content = extract_text_content(message.message.content)
                first_user_message = create_session_preview(content)

            sessions[session_id] = {
                "id": session_id,
                "summary": current_session_summary,
                "first_timestamp": getattr(message, "timestamp", ""),
                "last_timestamp": getattr(message, "timestamp", ""),
                "message_count": 0,
                "first_user_message": first_user_message,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_creation_tokens": 0,
                "total_cache_read_tokens": 0,
            }
            session_order.append(session_id)

            # Add session header message
            if session_id not in seen_sessions:
                seen_sessions.add(session_id)
                # Create a meaningful session title
                session_title = (
                    f"{current_session_summary} • {session_id[:8]}"
                    if current_session_summary
                    else session_id[:8]
                )

                session_header = TemplateMessage(
                    message_type="session_header",
                    content_html=session_title,
                    formatted_timestamp="",
                    raw_timestamp=None,
                    session_summary=current_session_summary,
                    session_id=session_id,
                    is_session_header=True,
                    message_id=None,  # Will be assigned by _build_message_hierarchy
                    ancestry=[],  # Session headers are top-level
                    modifiers=MessageModifiers(),  # No modifiers for session headers
                )
                template_messages.append(session_header)

        # Update first user message if this is a user message and we don't have one yet
        elif is_user_entry(message) and not sessions[session_id]["first_user_message"]:
            first_user_content = extract_text_content(message.message.content)
            if should_use_as_session_starter(first_user_content):
                sessions[session_id]["first_user_message"] = create_session_preview(
                    first_user_content
                )

        sessions[session_id]["message_count"] += 1

        # Update last timestamp for this session
        current_timestamp = getattr(message, "timestamp", "")
        if current_timestamp:
            sessions[session_id]["last_timestamp"] = current_timestamp

        # Extract and accumulate token usage for assistant messages
        # Only count tokens for the first message with each requestId to avoid duplicates
        if is_assistant_entry(message):
            assistant_message = message.message
            request_id = message.requestId
            message_uuid = message.uuid

            if (
                assistant_message.usage
                and request_id
                and request_id not in seen_request_ids
            ):
                # Mark this requestId as seen to avoid double-counting
                seen_request_ids.add(request_id)
                # Mark this specific message UUID as one that should show token usage
                show_tokens_for_message.add(message_uuid)

                usage = assistant_message.usage
                sessions[session_id]["total_input_tokens"] += usage.input_tokens
                sessions[session_id]["total_output_tokens"] += usage.output_tokens
                if usage.cache_creation_input_tokens:
                    sessions[session_id]["total_cache_creation_tokens"] += (
                        usage.cache_creation_input_tokens
                    )
                if usage.cache_read_input_tokens:
                    sessions[session_id]["total_cache_read_tokens"] += (
                        usage.cache_read_input_tokens
                    )

        # Get timestamp (only for non-summary messages)
        timestamp = getattr(message, "timestamp", "")
        formatted_timestamp = format_timestamp(timestamp) if timestamp else ""

        # Extract token usage for assistant messages
        # Only show token usage for the first message with each requestId to avoid duplicates
        token_usage_str: Optional[str] = None
        if is_assistant_entry(message):
            assistant_message = message.message
            message_uuid = message.uuid

            if assistant_message.usage and message_uuid in show_tokens_for_message:
                # Only show token usage for messages marked as first occurrence of requestId
                usage = assistant_message.usage
                token_parts = [
                    f"Input: {usage.input_tokens}",
                    f"Output: {usage.output_tokens}",
                ]
                if usage.cache_creation_input_tokens:
                    token_parts.append(
                        f"Cache Creation: {usage.cache_creation_input_tokens}"
                    )
                if usage.cache_read_input_tokens:
                    token_parts.append(f"Cache Read: {usage.cache_read_input_tokens}")
                token_usage_str = " | ".join(token_parts)

        # Determine modifiers and content based on message type and duplicate status
        if is_command:
            modifiers, content_html, message_type, message_title = (
                _process_command_message(text_content)
            )
        elif is_local_output:
            modifiers, content_html, message_type, message_title = (
                _process_local_command_output(text_content)
            )
        elif is_bash_cmd:
            modifiers, content_html, message_type, message_title = _process_bash_input(
                text_content
            )
        elif is_bash_result:
            modifiers, content_html, message_type, message_title = _process_bash_output(
                text_content
            )
        else:
            # For queue-operation messages, treat them as user messages
            if isinstance(message, QueueOperationTranscriptEntry):
                effective_type = "user"
            else:
                effective_type = message_type

            modifiers, content_html, message_type_result, message_title = (
                _process_regular_message(
                    text_only_content,
                    effective_type,
                    getattr(message, "isSidechain", False),
                    getattr(message, "isMeta", False),
                )
            )
            message_type = message_type_result  # Update message_type with result

            # Add 'steering' modifier for queue-operation 'remove' messages
            if (
                isinstance(message, QueueOperationTranscriptEntry)
                and message.operation == "remove"
            ):
                modifiers = replace(modifiers, is_steering=True)
                message_title = "User (steering)"

        # Only create main message if it has text content
        # For assistant/thinking with only tools (no text), we don't create a container message
        # The tools will be direct children of the current hierarchy level
        if text_only_content:
            template_message = TemplateMessage(
                message_type=message_type,
                content_html=content_html,
                formatted_timestamp=formatted_timestamp,
                raw_timestamp=timestamp,
                session_summary=session_summary,
                session_id=session_id,
                token_usage=token_usage_str,
                message_title=message_title,
                message_id=None,  # Will be assigned by _build_message_hierarchy
                ancestry=[],  # Will be assigned by _build_message_hierarchy
                agent_id=getattr(message, "agentId", None),
                uuid=getattr(message, "uuid", None),
                parent_uuid=getattr(message, "parentUuid", None),
                modifiers=modifiers,
            )

            # Store raw text content for potential future use (e.g., deduplication,
            # alternative output formats). Stripping happens when used.
            template_message.raw_text_content = text_content

            template_messages.append(template_message)

        # Create separate messages for each tool/thinking/image item
        for tool_item in tool_items:
            tool_timestamp = getattr(message, "timestamp", "")
            tool_formatted_timestamp = (
                format_timestamp(tool_timestamp) if tool_timestamp else ""
            )

            # Handle both custom types and Anthropic types
            item_type = getattr(tool_item, "type", None)

            # Dispatch to appropriate handler based on item type
            tool_result: Optional[ToolItemResult] = None
            if isinstance(tool_item, ToolUseContent) or item_type == "tool_use":
                tool_result = _process_tool_use_item(tool_item, tool_use_context)
            elif isinstance(tool_item, ToolResultContent) or item_type == "tool_result":
                tool_result = _process_tool_result_item(tool_item, tool_use_context)
            elif isinstance(tool_item, ThinkingContent) or item_type == "thinking":
                tool_result = _process_thinking_item(tool_item)
            elif isinstance(tool_item, ImageContent) or item_type == "image":
                tool_result = _process_image_item(tool_item)
            else:
                # Handle unknown content types
                tool_result = ToolItemResult(
                    message_type="unknown",
                    content_html=f"<p>Unknown content type: {escape_html(str(type(tool_item)))}</p>",
                    message_title="Unknown Content",
                )

            # Skip if handler returned None (e.g., unsupported image types)
            if tool_result is None:
                continue

            # Preserve sidechain context for tool/thinking/image content within sidechain messages
            tool_is_sidechain = getattr(message, "isSidechain", False)

            # Build modifiers directly from tool_result properties
            tool_modifiers = MessageModifiers(
                is_sidechain=tool_is_sidechain,
                is_error=tool_result.is_error,
            )

            # Generate unique UUID for this tool message
            # Use tool_use_id if available, otherwise fall back to message UUID + index
            tool_uuid = (
                tool_result.tool_use_id
                if tool_result.tool_use_id
                else f"{msg_uuid}-tool-{len(template_messages)}"
            )

            tool_template_message = TemplateMessage(
                message_type=tool_result.message_type,
                content_html=tool_result.content_html,
                formatted_timestamp=tool_formatted_timestamp,
                raw_timestamp=tool_timestamp,
                session_summary=session_summary,
                session_id=session_id,
                tool_use_id=tool_result.tool_use_id,
                title_hint=tool_result.title_hint,
                message_title=tool_result.message_title,
                message_id=None,  # Will be assigned by _build_message_hierarchy
                ancestry=[],  # Will be assigned by _build_message_hierarchy
                agent_id=getattr(message, "agentId", None),
                uuid=tool_uuid,
                modifiers=tool_modifiers,
            )

            # Store raw text for Task result deduplication
            # (handled later in _reorder_sidechain_template_messages)
            if tool_result.pending_dedup is not None:
                tool_template_message.raw_text_content = tool_result.pending_dedup

            template_messages.append(tool_template_message)

        # Track message timing
        if DEBUG_TIMING:
            msg_duration = time.time() - msg_start_time
            message_timings.append((msg_duration, message_type, msg_idx, msg_uuid))

    # Report loop statistics
    if DEBUG_TIMING:
        report_timing_statistics(
            message_timings,
            [("Markdown", markdown_timings), ("Pygments", pygments_timings)],
        )

    return (
        template_messages,
        sessions,
        session_order,
    )


# -- Project Index Generation -------------------------------------------------


def prepare_projects_index(
    project_summaries: List[Dict[str, Any]],
) -> tuple[List["TemplateProject"], "TemplateSummary"]:
    """Prepare project data for rendering in any format.

    Args:
        project_summaries: List of project summary dictionaries.

    Returns:
        A tuple of (template_projects, template_summary) for use by renderers.
    """
    # Sort projects by last modified (most recent first)
    sorted_projects = sorted(
        project_summaries, key=lambda p: p["last_modified"], reverse=True
    )

    # Convert to template-friendly format
    template_projects = [TemplateProject(project) for project in sorted_projects]
    template_summary = TemplateSummary(project_summaries)

    return template_projects, template_summary


def title_for_projects_index(
    project_summaries: List[Dict[str, Any]],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> str:
    """Generate a title for the projects index page.

    Determines a meaningful title based on working directories from projects,
    with optional date range suffix.

    Args:
        project_summaries: List of project summary dictionaries.
        from_date: Optional start date filter string.
        to_date: Optional end date filter string.

    Returns:
        A title string for the projects index page.
    """
    title = "Claude Code Projects"

    if project_summaries:
        # Collect all working directories from all projects
        all_working_dirs: set[str] = set()
        for project in project_summaries:
            working_dirs = project.get("working_directories", [])
            if working_dirs:
                all_working_dirs.update(working_dirs)

        # Use the common parent directory if available
        if all_working_dirs:
            # Find the most common parent directory
            from pathlib import Path

            working_paths = [Path(wd) for wd in all_working_dirs]

            if len(working_paths) == 1:
                # Single working directory - use its name
                title = f"Claude Code Projects - {working_paths[0].name}"
            else:
                # Multiple working directories - try to find common parent
                try:
                    # Find common parent
                    common_parts: list[str] = []
                    if working_paths:
                        # Get parts of first path
                        first_parts = working_paths[0].parts
                        for i, part in enumerate(first_parts):
                            # Check if this part exists in all paths
                            if all(
                                len(p.parts) > i and p.parts[i] == part
                                for p in working_paths
                            ):
                                common_parts.append(part)
                            else:
                                break

                        if len(common_parts) > 1:  # More than just root "/"
                            common_path = Path(*common_parts)
                            title = f"Claude Code Projects - {common_path.name}"
                except Exception:
                    # Fall back to default title if path analysis fails
                    pass

    # Add date range suffix if provided
    if from_date or to_date:
        date_range_parts: List[str] = []
        if from_date:
            date_range_parts.append(f"from {from_date}")
        if to_date:
            date_range_parts.append(f"to {to_date}")
        date_range_str = " ".join(date_range_parts)
        title += f" ({date_range_str})"

    return title


# -- Renderer Classes ---------------------------------------------------------


class Renderer:
    """Base class for transcript renderers.

    Subclasses implement format-specific rendering (HTML, Markdown, etc.).
    """

    def generate(
        self,
        messages: List[TranscriptEntry],
        title: Optional[str] = None,
        combined_transcript_link: Optional[str] = None,
    ) -> Optional[str]:
        """Generate output from transcript messages.

        Returns None by default; subclasses override to return formatted output.
        """
        return None

    def generate_session(
        self,
        messages: List[TranscriptEntry],
        session_id: str,
        title: Optional[str] = None,
        cache_manager: Optional["CacheManager"] = None,
    ) -> Optional[str]:
        """Generate output for a single session.

        Returns None by default; subclasses override to return formatted output.
        """
        return None

    def generate_projects_index(
        self,
        project_summaries: List[Dict[str, Any]],
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a projects index page.

        Returns None by default; subclasses override to return formatted output.
        """
        return None

    def is_outdated(self, file_path: Path) -> Optional[bool]:
        """Check if a rendered file is outdated.

        Returns None by default; subclasses override to return True/False.
        """
        return None


def get_renderer(format: str) -> Renderer:
    """Get a renderer instance for the specified format.

    Args:
        format: The output format (currently only "html" is supported).

    Returns:
        A Renderer instance for the specified format.

    Raises:
        ValueError: If the format is not supported.
    """
    if format == "html":
        from .html.renderer import HtmlRenderer

        return HtmlRenderer()
    raise ValueError(f"Unsupported format: {format}")
