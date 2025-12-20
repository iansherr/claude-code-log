#!/usr/bin/env python3
"""Render Claude transcript data to HTML format."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .cache import CacheManager
    from .models import MessageContent
from datetime import datetime

from .models import (
    MessageType,
    TranscriptEntry,
    AssistantTranscriptEntry,
    SystemTranscriptEntry,
    SummaryTranscriptEntry,
    QueueOperationTranscriptEntry,
    ContentItem,
    TextContent,
    ToolResultContent,
    ToolResultMessage,
    ToolUseContent,
    ThinkingContent,
    ThinkingMessage,
    # Structured content types
    AssistantTextMessage,
    CommandOutputMessage,
    CompactedSummaryMessage,
    DedupNoticeMessage,
    HookInfo,
    HookSummaryMessage,
    SessionHeaderMessage,
    SlashCommandMessage,
    SystemMessage,
    UnknownMessage,
    UserMemoryMessage,
    UserSlashCommandMessage,
    UserSteeringMessage,
    UserTextMessage,
)
from .parser import (
    as_assistant_entry,
    as_user_entry,
    extract_text_content,
    is_bash_input,
    is_bash_output,
    is_command_message,
    is_local_command_output,
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

from .html import (
    escape_html,
    format_tool_use_title,
    parse_bash_input,
    parse_bash_output,
    parse_command_output,
    parse_slash_command,
)
from .parser import parse_user_message_content


# -- Content Formatters -------------------------------------------------------
# NOTE: Content formatters have been moved to html/ submodules:
#   - format_thinking_content -> html/assistant_formatters.py
#   - format_assistant_text_content -> html/assistant_formatters.py
#   - format_tool_result_content -> html/tool_formatters.py
#   - format_tool_use_content -> html/tool_formatters.py
#   - format_image_content -> html/assistant_formatters.py
#   - format_user_text_model_content -> html/user_formatters.py
#   - parse_user_message_content -> parser.py


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
        ancestry: Optional[list[str]] = None,
        has_children: bool = False,
        uuid: Optional[str] = None,
        parent_uuid: Optional[str] = None,
        agent_id: Optional[str] = None,
        is_sidechain: bool = False,
        content: Optional["MessageContent"] = None,
    ):
        self.type = message_type
        # Structured content for rendering
        self.content = content
        self.formatted_timestamp = formatted_timestamp
        self.is_sidechain = is_sidechain
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
        self.children: list["TemplateMessage"] = []

    def get_immediate_children_label(self) -> str:
        """Generate human-readable label for immediate children."""
        return _format_type_counts(self.immediate_children_by_type)

    def get_total_descendants_label(self) -> str:
        """Generate human-readable label for all descendants."""
        return _format_type_counts(self.total_descendants_by_type)

    def flatten(self) -> list["TemplateMessage"]:
        """Recursively flatten this message and all children into a list.

        Returns a list with this message followed by all descendants in
        depth-first order. This provides backward compatibility with the
        flat-list template rendering approach.
        """
        result: list["TemplateMessage"] = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result

    @staticmethod
    def flatten_all(messages: list["TemplateMessage"]) -> list["TemplateMessage"]:
        """Flatten a list of root messages into a single flat list.

        Useful for converting a tree structure back to a flat list for
        templates that expect the traditional flat message list.
        """
        result: list["TemplateMessage"] = []
        for message in messages:
            result.extend(message.flatten())
        return result


class TemplateProject:
    """Structured project data for template rendering."""

    def __init__(self, project_data: dict[str, Any]):
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
            token_parts: list[str] = []
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

    def __init__(self, project_summaries: list[dict[str, Any]]):
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
            token_parts: list[str] = []
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
    messages: list[TranscriptEntry],
) -> Tuple[list[TemplateMessage], list[dict[str, Any]]]:
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

    # Filter messages (removes summaries, warmup, empty, etc.)
    with log_timing("Filter messages", t_start):
        filtered_messages = _filter_messages(messages)

    # Pass 1: Collect session metadata and token tracking
    with log_timing("Collect session info", t_start):
        sessions, session_order, show_tokens_for_message = _collect_session_info(
            filtered_messages
        )

    # Pass 2: Render messages to TemplateMessage objects
    with log_timing(
        lambda: f"Render messages ({len(template_messages)} messages)", t_start
    ):
        template_messages = _render_messages(
            filtered_messages, sessions, show_tokens_for_message
        )

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

    # Resolve dedup notice targets (needs message_id from hierarchy)
    with log_timing("Resolve dedup targets", t_start):
        _resolve_dedup_targets(template_messages)

    # Mark messages that have children for fold/unfold controls
    with log_timing("Mark messages with children", t_start):
        _mark_messages_with_children(template_messages)

    # Build tree structure by populating children fields
    # Returns root messages (typically session headers) with children populated
    # HtmlRenderer flattens this via pre-order traversal for template rendering
    with log_timing("Build message tree", t_start):
        root_messages = _build_message_tree(template_messages)

    return root_messages, session_nav


# -- Session Utilities --------------------------------------------------------


def prepare_session_summaries(messages: list[TranscriptEntry]) -> None:
    """Pre-process messages to find and attach session summaries.

    Modifies messages in place by attaching _session_summary attribute.
    """
    session_summaries: dict[str, str] = {}
    uuid_to_session: dict[str, str] = {}
    uuid_to_session_backup: dict[str, str] = {}

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
    sessions: dict[str, dict[str, Any]],
    session_order: list[str],
) -> list[dict[str, Any]]:
    """Prepare session navigation data for template rendering.

    Args:
        sessions: Dictionary mapping session_id to session info dict
        session_order: List of session IDs in display order

    Returns:
        List of session navigation dicts for template rendering
    """
    session_nav: list[dict[str, Any]] = []

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
            token_parts: list[str] = []
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
) -> tuple[Optional["MessageContent"], str, str]:
    """Process a slash command message and return (content, message_type, message_title).

    These are user messages containing slash command invocations (e.g., /context, /model).
    The JSONL type is "user", not "system".
    """
    # Parse to content model (formatting happens in HtmlRenderer)
    content = parse_slash_command(text_content)
    # If parsing fails, content will be None and caller will handle fallback

    return content, "user", "Slash Command"


def _process_local_command_output(
    text_content: str,
) -> tuple[Optional["MessageContent"], str, str]:
    """Process slash command output and return (content, message_type, message_title).

    These are user messages containing the output from slash commands (e.g., /context, /model).
    The JSONL type is "user", not "system".
    """
    # Parse to content model (formatting happens in HtmlRenderer)
    content = parse_command_output(text_content)
    # If parsing fails, content will be None and caller will handle fallback

    return content, "user", ""


def _process_bash_input(
    text_content: str,
) -> tuple[Optional["MessageContent"], str, str]:
    """Process bash input command and return (content, message_type, message_title)."""
    # Parse to content model (formatting happens in HtmlRenderer)
    content = parse_bash_input(text_content)
    # If parsing fails, content will be None and caller will handle fallback

    return content, "bash-input", "Bash command"


def _process_bash_output(
    text_content: str,
) -> tuple[Optional["MessageContent"], str, str]:
    """Process bash output and return (content, message_type, message_title)."""
    # Parse to content model (formatting happens in HtmlRenderer)
    content = parse_bash_output(text_content)
    # If parsing fails, content will be None - caller/renderer handles empty output

    return content, "bash-output", ""


def _process_regular_message(
    items: list[ContentItem],
    message_type: str,
    is_sidechain: bool,
    is_meta: bool = False,
) -> tuple[bool, Optional["MessageContent"], str, str]:
    """Process regular message and return (is_sidechain, content_model, message_type, message_title).

    Returns content_model for user messages, None for non-user messages.
    Non-user messages (assistant) are handled by the legacy render_message_content path.

    Note: Sidechain user messages (Sub-assistant prompts) are now skipped entirely
    in the main processing loop since they duplicate the Task tool input prompt.

    Args:
        items: List of text/image content items (no tool_use, tool_result, thinking).
        is_meta: True for slash command expanded prompts (isMeta=True in JSONL)
    """
    message_title = message_type.title()  # Default title
    content_model: Optional["MessageContent"] = None

    # Handle user-specific preprocessing
    if message_type == MessageType.USER:
        # Note: sidechain user messages are skipped before reaching this function
        # Parse user content (is_meta triggers UserSlashCommandMessage creation)
        content_model = parse_user_message_content(items, is_slash_command=is_meta)

        # Determine message_title from content type
        if isinstance(content_model, UserSlashCommandMessage):
            message_title = "User (slash command)"
        elif isinstance(content_model, CompactedSummaryMessage):
            message_title = "User (compacted conversation)"
        elif isinstance(content_model, UserMemoryMessage):
            message_title = "Memory"

    elif message_type == MessageType.ASSISTANT:
        # Create AssistantTextMessage directly from items
        # (empty text already filtered by chunk_message_content)
        if items:
            content_model = AssistantTextMessage(
                items=items  # type: ignore[arg-type]
            )

    if is_sidechain:
        # Update message title for display (only non-user types reach here)
        if not isinstance(content_model, CompactedSummaryMessage):
            message_title = "Sub-assistant"

    return is_sidechain, content_model, message_type, message_title


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
        content = HookSummaryMessage(
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
        content = SystemMessage(level=level, text=message.content)

    # Store parent UUID for hierarchy rebuild (handled by _build_message_hierarchy)
    parent_uuid = getattr(message, "parentUuid", None)

    return TemplateMessage(
        message_type="system",
        formatted_timestamp=formatted_timestamp,
        raw_timestamp=timestamp,
        session_id=session_id,
        message_title=f"System {level.title()}",
        message_id=None,  # Will be assigned by _build_message_hierarchy
        ancestry=[],  # Will be assigned by _build_message_hierarchy
        uuid=message.uuid,
        parent_uuid=parent_uuid,
        content=content,  # Level info is in SystemMessage
    )


# Type alias for chunk output: either a list of regular items or a single special item
ContentChunk = list[ContentItem] | ContentItem


def _is_special_item(item: ContentItem) -> bool:
    """Check if a content item is a 'special' item that should be its own chunk.

    Special items (tool_use, tool_result, thinking) become their own TemplateMessages.
    Regular items (text, image) are accumulated together.
    """
    item_type = getattr(item, "type", None)
    return isinstance(
        item, (ToolUseContent, ToolResultContent, ThinkingContent)
    ) or item_type in ("tool_use", "tool_result", "thinking")


def chunk_message_content(content: list[ContentItem]) -> list[ContentChunk]:
    """Split message content into chunks for TemplateMessage creation.

    This function processes a list of content items and produces chunks where:
    - "Special" items (tool_use, tool_result, thinking) each become their own chunk
    - "Regular" items (text, image) are accumulated into list chunks

    When a special item is encountered, any accumulated regular items are flushed
    as a list chunk first, then the special item is added as a single-item chunk.

    Args:
        content: List of ContentItem from the message

    Returns:
        List of chunks where each chunk is either:
        - A list[ContentItem] of accumulated text/image items
        - A single ContentItem (tool_use, tool_result, or thinking)

    Example:
        Input: [text, image, thinking, text, text, tool_use]
        Output: [[text, image], thinking, [text, text], tool_use]
    """
    if not content:
        return []

    chunks: list[ContentChunk] = []
    accumulated: list[ContentItem] = []

    for item in content:
        if _is_special_item(item):
            # Flush accumulated regular items as a chunk
            if accumulated:
                chunks.append(accumulated)
                accumulated = []
            # Add special item as its own chunk
            chunks.append(item)
        else:
            # Accumulate regular items (text, image), skip empty text
            if hasattr(item, "text"):
                if not getattr(item, "text", "").strip():
                    continue  # Skip empty text
            accumulated.append(item)

    # Flush any remaining accumulated items
    if accumulated:
        chunks.append(accumulated)

    return chunks


@dataclass
class ToolItemResult:
    """Result of processing a single tool/thinking/image item."""

    message_type: str
    message_title: str
    content: Optional["MessageContent"] = None  # Structured content for rendering
    tool_use_id: Optional[str] = None
    title_hint: Optional[str] = None
    pending_dedup: Optional[str] = None  # For Task result deduplication
    is_error: bool = False  # For tool_result error state


def _process_tool_use_item(
    tool_item: ContentItem,
    tool_use_context: dict[str, ToolUseContent],
) -> Optional[ToolItemResult]:
    """Process a tool_use content item.

    Args:
        tool_item: The tool use content item
        tool_use_context: Dict to populate with tool_use_id -> ToolUseContent mapping

    Returns:
        ToolItemResult with tool_use content model, or None if item should be skipped
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

    # Title is computed here but content formatting happens in HtmlRenderer
    tool_message_title = format_tool_use_title(tool_use)
    escaped_id = escape_html(tool_use.id)
    item_tool_use_id = tool_use.id
    tool_title_hint = f"ID: {escaped_id}"

    # Populate tool_use_context for later use when processing tool results
    tool_use_context[item_tool_use_id] = tool_use

    return ToolItemResult(
        message_type="tool_use",
        message_title=tool_message_title,
        content=tool_use,  # ToolUseContent is the model
        tool_use_id=item_tool_use_id,
        title_hint=tool_title_hint,
    )


def _process_tool_result_item(
    tool_item: ContentItem,
    tool_use_context: dict[str, ToolUseContent],
) -> Optional[ToolItemResult]:
    """Process a tool_result content item.

    Args:
        tool_item: The tool result content item
        tool_use_context: Dict with tool_use_id -> ToolUseContent mapping

    Returns:
        ToolItemResult with tool_result content model, or None if item should be skipped
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

    # Create content model with rendering context
    # Pass the whole ToolResultContent as output (generic fallback)
    # TODO: Parse into specialized output types (ReadOutput, EditOutput) when appropriate
    content_model = ToolResultMessage(
        tool_use_id=tool_result.tool_use_id,
        output=tool_result,  # ToolResultContent as ToolOutput
        is_error=tool_result.is_error or False,
        tool_name=result_tool_name,
        file_path=result_file_path,
    )

    # Retroactive deduplication: if Task result, extract content for later matching
    pending_dedup: Optional[str] = None
    if result_tool_name == "Task":
        # Extract text content from tool result
        # Note: tool_result.content can be str or list[dict[str, Any]]
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
        message_title=tool_message_title,
        content=content_model,
        tool_use_id=tool_result.tool_use_id,
        title_hint=tool_title_hint,
        pending_dedup=pending_dedup,
        is_error=tool_result.is_error or False,
    )


def _process_thinking_item(tool_item: ContentItem) -> Optional[ToolItemResult]:
    """Process a thinking content item.

    Returns:
        ToolItemResult with thinking content model
    """
    # Extract thinking text from the content item
    if isinstance(tool_item, ThinkingContent):
        thinking_text = tool_item.thinking.strip()
        signature = getattr(tool_item, "signature", None)
    else:
        thinking_text = getattr(tool_item, "thinking", str(tool_item)).strip()
        signature = None

    # Create the content model (formatting happens in HtmlRenderer)
    thinking_model = ThinkingMessage(thinking=thinking_text, signature=signature)

    return ToolItemResult(
        message_type="thinking",
        message_title="Thinking",
        content=thinking_model,
    )


# -- Message Pairing ----------------------------------------------------------


@dataclass
class PairingIndices:
    """Indices for efficient message pairing lookups.

    All indices are built in a single pass for efficiency.
    """

    # (session_id, tool_use_id) -> message index for tool_use messages
    tool_use: dict[tuple[str, str], int]
    # (session_id, tool_use_id) -> message index for tool_result messages
    tool_result: dict[tuple[str, str], int]
    # uuid -> message index for system messages (parent-child pairing)
    uuid: dict[str, int]
    # parent_uuid -> message index for slash-command messages
    slash_command_by_parent: dict[str, int]


def _build_pairing_indices(messages: list[TemplateMessage]) -> PairingIndices:
    """Build indices for efficient message pairing lookups.

    Single pass through messages to build all indices needed for pairing.
    """
    tool_use_index: dict[tuple[str, str], int] = {}
    tool_result_index: dict[tuple[str, str], int] = {}
    uuid_index: dict[str, int] = {}
    slash_command_by_parent: dict[str, int] = {}

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
        if msg.parent_uuid and isinstance(
            msg.content, (SlashCommandMessage, UserSlashCommandMessage)
        ):
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
    if isinstance(
        current.content, (SlashCommandMessage, UserSlashCommandMessage)
    ) and isinstance(next_msg.content, CommandOutputMessage):
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
    messages: list[TemplateMessage],
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


def _identify_message_pairs(messages: list[TemplateMessage]) -> None:
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


def _reorder_paired_messages(messages: list[TemplateMessage]) -> list[TemplateMessage]:
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
    pair_last_index: dict[
        tuple[str, str], int
    ] = {}  # (session_id, tool_use_id) -> message index
    # Index slash-command pair_last messages by parent_uuid
    slash_command_pair_index: dict[str, int] = {}  # parent_uuid -> message index

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
            and isinstance(msg.content, (SlashCommandMessage, UserSlashCommandMessage))
        ):
            slash_command_pair_index[msg.parent_uuid] = i

    # Create reordered list
    reordered: list[TemplateMessage] = []
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

            # Only append if we haven't already added this pair_last
            # (handles case where multiple pair_firsts match the same pair_last)
            if (
                pair_last is not None
                and last_idx is not None
                and last_idx not in skip_indices
            ):
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
    is_sidechain = msg.is_sidechain

    # User messages at level 1 (under session)
    # Note: sidechain user messages are skipped before reaching this function
    if msg_type == "user" and not is_sidechain:
        return 1

    # System info/warning at level 3 (tool-related, e.g., hook notifications)
    # Get level from SystemMessage if available
    system_level = msg.content.level if isinstance(msg.content, SystemMessage) else None
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


def _build_message_hierarchy(messages: list[TemplateMessage]) -> None:
    """Build message_id and ancestry for all messages based on their current order.

    This should be called after all reordering operations (pair reordering, sidechain
    reordering) to ensure the hierarchy reflects the final display order.

    The hierarchy is determined by message type using _get_message_hierarchy_level(),
    and a stack-based approach builds proper parent-child relationships.

    Args:
        messages: List of template messages in their final order (modified in place)
    """
    hierarchy_stack: list[tuple[int, str]] = []
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


def _mark_messages_with_children(messages: list[TemplateMessage]) -> None:
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


def _build_message_tree(messages: list[TemplateMessage]) -> list[TemplateMessage]:
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
    root_messages: list[TemplateMessage] = []

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
    messages: list[TemplateMessage],
) -> list[TemplateMessage]:
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
    session_headers: list[TemplateMessage] = []
    session_messages_map: dict[str, list[TemplateMessage]] = {}

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
    result: list[TemplateMessage] = []
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
    messages: list[TemplateMessage],
) -> list[TemplateMessage]:
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
    main_messages: list[TemplateMessage] = []
    sidechain_map: dict[str, list[TemplateMessage]] = {}

    for message in messages:
        is_sidechain = message.is_sidechain
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
    result: list[TemplateMessage] = []
    used_agents: set[str] = set()

    for message in main_messages:
        result.append(message)

        # Check if this is a Task tool_result that references a sidechain (via agent_id)
        # We only insert after tool_result (not tool_use) to avoid duplicates if
        # tool_use ever gets agent_id in the future
        agent_id = message.agent_id

        # Only insert sidechain if not already inserted (handles case where
        # multiple tool_results have the same agent_id)
        if (
            agent_id
            and message.type == MessageType.TOOL_RESULT
            and agent_id in sidechain_map
            and agent_id not in used_agents
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
                        sidechain_msg.content = DedupNoticeMessage(
                            notice_text="Task summary — see result above",
                            target_uuid=message.uuid,
                            original_text=sidechain_text,
                        )
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


def _resolve_dedup_targets(messages: list[TemplateMessage]) -> None:
    """Resolve dedup notice target UUIDs to message IDs for anchor links.

    Must be called after _build_message_hierarchy assigns message_id values.
    """
    # Build uuid -> message_id mapping
    uuid_to_id: dict[str, str] = {}
    for msg in messages:
        if msg.uuid and msg.message_id:
            uuid_to_id[msg.uuid] = msg.message_id

    # Resolve dedup notice targets
    for msg in messages:
        if isinstance(msg.content, DedupNoticeMessage) and msg.content.target_uuid:
            msg.content.target_message_id = uuid_to_id.get(msg.content.target_uuid)


def _filter_messages(messages: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Filter messages to those that should be rendered.

    This function filters out:
    - Summary messages (already attached to sessions)
    - Queue operations except 'remove' (steering messages)
    - Messages with no meaningful content (no text and no tool items)
    - Messages matching should_skip_message() (warmup, etc.)
    - Sidechain user messages without tool results (prompts duplicate Task result)

    System messages are included as they need special processing in _render_messages.

    Args:
        messages: List of transcript entries to filter

    Returns:
        Filtered list of messages that should be rendered
    """
    filtered: list[TranscriptEntry] = []

    for message in messages:
        message_type = message.type

        # Skip summary messages
        if isinstance(message, SummaryTranscriptEntry):
            continue

        # Skip most queue operations - only process 'remove' for counts
        if isinstance(message, QueueOperationTranscriptEntry):
            if message.operation != "remove":
                continue

        # System messages bypass other checks but are included
        if isinstance(message, SystemTranscriptEntry):
            filtered.append(message)
            continue

        # Get message content for filtering checks
        message_content: list[ContentItem]
        if isinstance(message, QueueOperationTranscriptEntry):
            content = message.content
            message_content = content if isinstance(content, list) else []
        else:
            message_content = message.message.content  # type: ignore[union-attr]

        text_content = extract_text_content(message_content)

        # Skip if no meaningful content
        if not text_content.strip():
            # Check for tool items
            has_tool_items = any(
                isinstance(item, (ToolUseContent, ToolResultContent, ThinkingContent))
                or getattr(item, "type", None)
                in ("tool_use", "tool_result", "thinking")
                for item in message_content
            )
            if not has_tool_items:
                continue

        # Skip messages that should be filtered out
        if should_skip_message(text_content):
            continue

        # Skip sidechain user messages that are just prompts (no tool results)
        if message_type == MessageType.USER and getattr(message, "isSidechain", False):
            has_tool_results = any(
                getattr(item, "type", None) == "tool_result"
                or isinstance(item, ToolResultContent)
                for item in message_content
            )
            if not has_tool_results:
                continue

        # Message passes all filters
        filtered.append(message)

    return filtered


def _collect_session_info(
    messages: list[TranscriptEntry],
) -> tuple[
    dict[str, dict[str, Any]],  # sessions
    list[str],  # session_order
    set[str],  # show_tokens_for_message
]:
    """Collect session metadata and token tracking from pre-filtered messages.

    This function iterates through messages to:
    - Build session metadata (timestamps, message counts, first user message)
    - Track token usage per session (deduplicating by requestId)
    - Determine which messages should display token usage

    Note: Messages should be pre-filtered by _filter_messages. System messages
    in the input are skipped for session tracking purposes.

    Args:
        messages: Pre-filtered list of transcript entries

    Returns:
        Tuple containing:
        - sessions: Session metadata dict mapping session_id to info
        - session_order: List of session IDs in chronological order
        - show_tokens_for_message: Set of message UUIDs that should display tokens
    """
    sessions: dict[str, dict[str, Any]] = {}
    session_order: list[str] = []

    # Track requestIds to avoid double-counting token usage
    seen_request_ids: set[str] = set()
    # Track which messages should show token usage (first occurrence of each requestId)
    show_tokens_for_message: set[str] = set()

    for message in messages:
        # Skip system messages for session tracking
        if isinstance(message, SystemTranscriptEntry):
            continue

        # Get message content
        if isinstance(message, QueueOperationTranscriptEntry):
            message_content = message.content if message.content else []
        else:
            message_content = message.message.content  # type: ignore

        text_content = extract_text_content(message_content)  # type: ignore[arg-type]

        # Get session info
        session_id = getattr(message, "sessionId", "unknown")

        # Initialize session if new
        if session_id not in sessions:
            current_session_summary = getattr(message, "_session_summary", None)

            # Get first user message content for preview
            first_user_message = ""
            if as_user_entry(message) and should_use_as_session_starter(text_content):
                first_user_message = create_session_preview(text_content)

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

        # Update first user message if this is a user message and we don't have one yet
        elif as_user_entry(message) and not sessions[session_id]["first_user_message"]:
            if should_use_as_session_starter(text_content):
                sessions[session_id]["first_user_message"] = create_session_preview(
                    text_content
                )

        sessions[session_id]["message_count"] += 1

        # Update last timestamp for this session
        current_timestamp = getattr(message, "timestamp", "")
        if current_timestamp:
            sessions[session_id]["last_timestamp"] = current_timestamp

        # Extract and accumulate token usage for assistant messages
        # Only count tokens for the first message with each requestId to avoid duplicates
        if assistant_entry := as_assistant_entry(message):
            assistant_message = assistant_entry.message
            request_id = assistant_entry.requestId
            message_uuid = assistant_entry.uuid

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

    return sessions, session_order, show_tokens_for_message


def _render_messages(
    messages: list[TranscriptEntry],
    sessions: dict[str, dict[str, Any]],
    show_tokens_for_message: set[str],
) -> list[TemplateMessage]:
    """Pass 2: Render pre-filtered messages to TemplateMessage objects.

    This pass creates the actual TemplateMessage objects for rendering:
    - Creates session headers when entering new sessions
    - Processes text content into HTML
    - Handles tool use, tool result, thinking, and image content
    - Collects timing statistics

    Note: Messages are pre-filtered by _collect_session_info, so no additional
    filtering is needed here except for system message processing.

    Args:
        messages: Pre-filtered list of transcript entries from _collect_session_info
        sessions: Session metadata from _collect_session_info
        show_tokens_for_message: Set of message UUIDs that should display tokens

    Returns:
        List of TemplateMessage objects ready for template rendering
    """
    # Track which sessions have had headers added
    seen_sessions: set[str] = set()

    # Build mapping of tool_use_id to ToolUseContent for specialized tool result rendering
    tool_use_context: dict[str, ToolUseContent] = {}

    # Process messages into template-friendly format
    template_messages: list[TemplateMessage] = []

    # Per-message timing tracking
    message_timings: list[
        tuple[float, str, int, str]
    ] = []  # (duration, message_type, index, uuid)

    # Track expensive operations
    markdown_timings: list[tuple[float, str]] = []  # (duration, context_uuid)
    pygments_timings: list[tuple[float, str]] = []  # (duration, context_uuid)

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

        # Handle system messages separately (already filtered in pass 1)
        if isinstance(message, SystemTranscriptEntry):
            system_template_message = _process_system_message(message)
            if system_template_message:
                template_messages.append(system_template_message)
            continue

        # Handle queue-operation 'remove' messages as user messages
        if isinstance(message, QueueOperationTranscriptEntry):
            message_content = message.content if message.content else []
            message_type = MessageType.QUEUE_OPERATION
        else:
            message_content = message.message.content  # type: ignore

        # Track sidechain status for user messages
        # (sidechain user text is skipped to avoid duplicate Task prompts)
        is_sidechain_user = message_type == MessageType.USER and getattr(
            message, "isSidechain", False
        )

        # Chunk content: regular items (text/image) accumulate, special items (tool/thinking) separate
        if isinstance(message_content, list):
            chunks = chunk_message_content(message_content)  # type: ignore[arg-type]
        else:
            # String content - wrap in list with single TextContent
            content_str: str = message_content.strip() if message_content else ""  # type: ignore[union-attr]
            if content_str:
                chunks: list[ContentChunk] = [
                    [TextContent(type="text", text=content_str)]  # pyright: ignore[reportUnknownArgumentType]
                ]
            else:
                chunks = []

        # Skip messages with no content
        if not chunks:
            continue

        # Get session info
        session_id = getattr(message, "sessionId", "unknown")
        session_summary = getattr(message, "_session_summary", None)

        # Add session header if this is a new session
        if session_id not in seen_sessions:
            seen_sessions.add(session_id)
            current_session_summary = sessions.get(session_id, {}).get("summary")
            session_title = (
                f"{current_session_summary} • {session_id[:8]}"
                if current_session_summary
                else session_id[:8]
            )

            session_header = TemplateMessage(
                message_type="session_header",
                formatted_timestamp="",
                raw_timestamp=None,
                session_summary=current_session_summary,
                session_id=session_id,
                is_session_header=True,
                message_id=None,
                ancestry=[],
                content=SessionHeaderMessage(
                    title=session_title,
                    session_id=session_id,
                    summary=current_session_summary,
                ),
            )
            template_messages.append(session_header)

        # Get timestamp (only for non-summary messages)
        timestamp = getattr(message, "timestamp", "")
        formatted_timestamp = format_timestamp(timestamp) if timestamp else ""

        # Extract token usage for assistant messages
        # Only show token usage for the first message with each requestId to avoid duplicates
        token_usage_str: Optional[str] = None
        if assistant_entry := as_assistant_entry(message):
            assistant_message = assistant_entry.message
            message_uuid = assistant_entry.uuid

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

        # Track whether we've shown token usage (only show on first content chunk)
        token_shown = False

        # Process each chunk - regular chunks (list) become text/image messages,
        # special chunks (single item) become tool/thinking messages
        for chunk_idx, chunk in enumerate(chunks):
            # Regular chunk: list of text/image items
            if isinstance(chunk, list):
                # Skip text chunks for sidechain user messages
                # (prompts duplicate Task result; filtering already done in pass 1)
                if is_sidechain_user:
                    continue

                # Extract text for pattern detection
                chunk_text = extract_text_content(chunk)

                # Check for special message patterns
                is_command = is_command_message(chunk_text)
                is_local_output = is_local_command_output(chunk_text)
                is_bash_cmd = is_bash_input(chunk_text)
                is_bash_result = is_bash_output(chunk_text)

                # Determine is_sidechain and content based on message type
                content_model: Optional[MessageContent] = None
                chunk_message_type = message_type
                chunk_is_sidechain = getattr(message, "isSidechain", False)

                if is_command:
                    content_model, chunk_message_type, message_title = (
                        _process_command_message(chunk_text)
                    )
                elif is_local_output:
                    content_model, chunk_message_type, message_title = (
                        _process_local_command_output(chunk_text)
                    )
                elif is_bash_cmd:
                    content_model, chunk_message_type, message_title = (
                        _process_bash_input(chunk_text)
                    )
                elif is_bash_result:
                    content_model, chunk_message_type, message_title = (
                        _process_bash_output(chunk_text)
                    )
                else:
                    # For queue-operation messages, treat them as user messages
                    if isinstance(message, QueueOperationTranscriptEntry):
                        effective_type = "user"
                    else:
                        effective_type = message_type

                    (
                        chunk_is_sidechain,
                        content_model,
                        chunk_message_type,
                        message_title,
                    ) = _process_regular_message(
                        chunk,  # Pass the chunk items
                        effective_type,
                        chunk_is_sidechain,
                        getattr(message, "isMeta", False),
                    )

                    # Convert to UserSteeringMessage for queue-operation 'remove' messages
                    if (
                        isinstance(message, QueueOperationTranscriptEntry)
                        and message.operation == "remove"
                        and isinstance(content_model, UserTextMessage)
                    ):
                        content_model = UserSteeringMessage(items=content_model.items)
                        message_title = "User (steering)"

                # Skip empty chunks
                if not chunk:
                    continue

                # Only show token usage on first chunk
                chunk_token_usage = token_usage_str if not token_shown else None
                token_shown = True

                # Generate UUID for this chunk (append index if multiple chunks)
                chunk_uuid = getattr(message, "uuid", None)
                if chunk_uuid and len(chunks) > 1:
                    chunk_uuid = f"{chunk_uuid}-chunk-{chunk_idx}"

                # Markdown rendering for assistant, thinking, and compacted content
                has_markdown = isinstance(
                    content_model,
                    (
                        AssistantTextMessage,
                        ThinkingMessage,
                        CompactedSummaryMessage,
                    ),
                )

                template_message = TemplateMessage(
                    message_type=chunk_message_type,
                    formatted_timestamp=formatted_timestamp,
                    raw_timestamp=timestamp,
                    session_summary=session_summary,
                    session_id=session_id,
                    token_usage=chunk_token_usage,
                    message_title=message_title,
                    message_id=None,  # Will be assigned by _build_message_hierarchy
                    ancestry=[],  # Will be assigned by _build_message_hierarchy
                    agent_id=getattr(message, "agentId", None),
                    uuid=chunk_uuid,
                    parent_uuid=getattr(message, "parentUuid", None),
                    is_sidechain=chunk_is_sidechain,
                    content=content_model,
                    has_markdown=has_markdown,
                )

                # Store raw text content for potential future use
                template_message.raw_text_content = chunk_text

                template_messages.append(template_message)

            else:
                # Special chunk: single tool_use/tool_result/thinking item
                tool_item = chunk
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
                elif (
                    isinstance(tool_item, ToolResultContent)
                    or item_type == "tool_result"
                ):
                    tool_result = _process_tool_result_item(tool_item, tool_use_context)
                elif isinstance(tool_item, ThinkingContent) or item_type == "thinking":
                    tool_result = _process_thinking_item(tool_item)
                else:
                    # Handle unknown content types
                    tool_result = ToolItemResult(
                        message_type="unknown",
                        content=UnknownMessage(type_name=str(type(tool_item))),
                        message_title="Unknown Content",
                    )

                # Skip if handler returned None (e.g., unsupported image types)
                if tool_result is None:
                    continue

                # Preserve sidechain context for tool/thinking content
                tool_is_sidechain = getattr(message, "isSidechain", False)

                # Generate unique UUID for this tool message
                # Use tool_use_id if available, otherwise fall back to msg UUID + index
                tool_uuid = (
                    tool_result.tool_use_id
                    if tool_result.tool_use_id
                    else f"{msg_uuid}-tool-{len(template_messages)}"
                )

                # Thinking content uses markdown
                tool_has_markdown = isinstance(tool_result.content, ThinkingMessage)

                tool_template_message = TemplateMessage(
                    message_type=tool_result.message_type,
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
                    is_sidechain=tool_is_sidechain,
                    content=tool_result.content,  # Structured content model
                    has_markdown=tool_has_markdown,
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

    return template_messages


# -- Project Index Generation -------------------------------------------------


def prepare_projects_index(
    project_summaries: list[dict[str, Any]],
) -> tuple[list["TemplateProject"], "TemplateSummary"]:
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
    project_summaries: list[dict[str, Any]],
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
        date_range_parts: list[str] = []
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

    The dispatcher pattern enables automatic content formatting based on type:
    - Subclasses override _build_dispatcher() to map content types to formatters
    - format_content() walks the MRO to find the most specific handler
    - Fallback to parent class handlers if no specific handler exists
    """

    def __init__(self):
        self._dispatcher = self._build_dispatcher()

    def _build_dispatcher(
        self,
    ) -> dict[type, Callable[..., str]]:
        """Build the content type to formatter mapping.

        Override in subclasses to register format-specific handlers.
        The dict maps MessageContent subclasses to formatter functions.
        Each formatter receives the content directly (cast to the matched type).

        Returns:
            Dict mapping content types to formatter functions.
        """
        return {}

    def format_content(self, message: "TemplateMessage") -> str:
        """Format message content by dispatching to type-specific handler.

        Walks the content type's MRO to find the most specific registered
        handler. This allows handlers for parent classes to serve as fallbacks.

        Args:
            message: TemplateMessage with content to format.

        Returns:
            Formatted string (e.g., HTML), or empty string if no handler found.
        """
        if message.content is None:
            return ""
        for cls in type(message.content).__mro__:
            if cls is object:
                break
            if fmt := self._dispatcher.get(cls):
                return fmt(message.content)
        return ""

    def generate(
        self,
        messages: list[TranscriptEntry],
        title: Optional[str] = None,
        combined_transcript_link: Optional[str] = None,
    ) -> Optional[str]:
        """Generate output from transcript messages.

        Returns None by default; subclasses override to return formatted output.
        """
        return None

    def generate_session(
        self,
        messages: list[TranscriptEntry],
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
        project_summaries: list[dict[str, Any]],
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
