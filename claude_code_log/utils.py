#!/usr/bin/env python3
"""Utility functions for message filtering and processing."""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

from claude_code_log.cache import SessionCacheData
from .models import ContentItem, TextContent, TranscriptEntry, UserTranscriptEntry
from .parser import (
    IDE_DIAGNOSTICS_PATTERN,
    IDE_OPENED_FILE_PATTERN,
    IDE_SELECTION_PATTERN,
    is_command_message,
    is_local_command_output,
    is_system_message,
)


def format_timestamp(timestamp_str: str | None) -> str:
    """Format ISO timestamp for display, converting to UTC."""
    if timestamp_str is None:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        # Convert to UTC if timezone-aware
        if dt.tzinfo is not None:
            utc_timetuple = dt.utctimetuple()
            dt = datetime(
                utc_timetuple.tm_year,
                utc_timetuple.tm_mon,
                utc_timetuple.tm_mday,
                utc_timetuple.tm_hour,
                utc_timetuple.tm_min,
                utc_timetuple.tm_sec,
            )
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return timestamp_str


def format_timestamp_range(first_timestamp: str, last_timestamp: str) -> str:
    """Format timestamp range for display.

    Args:
        first_timestamp: ISO timestamp for range start
        last_timestamp: ISO timestamp for range end

    Returns:
        Formatted string like "2025-01-01 10:00:00 - 2025-01-01 11:00:00"
        or single timestamp if both are equal, or empty string if neither provided.
    """
    if first_timestamp and last_timestamp:
        if first_timestamp == last_timestamp:
            return format_timestamp(first_timestamp)
        else:
            return f"{format_timestamp(first_timestamp)} - {format_timestamp(last_timestamp)}"
    elif first_timestamp:
        return format_timestamp(first_timestamp)
    else:
        return ""


def get_project_display_name(
    project_dir_name: str, working_directories: Optional[List[str]] = None
) -> str:
    """Get the display name for a project based on working directories.

    Args:
        project_dir_name: The Claude project directory name (e.g., "-Users-dain-workspace-claude-code-log")
        working_directories: List of working directories from cache data

    Returns:
        The project display name (e.g., "claude-code-log")
    """
    if working_directories:
        # Convert to Path objects with their original indices for tracking recency
        paths_with_indices = [(Path(wd), i) for i, wd in enumerate(working_directories)]

        # Sort by: 1) path depth (fewer parts = less nested), 2) recency (lower index = more recent)
        # This gives us the least nested path, with ties broken by recency
        best_path, _ = min(paths_with_indices, key=lambda p: (len(p[0].parts), p[1]))
        return best_path.name
    else:
        # Fall back to converting project directory name
        display_name = project_dir_name
        if display_name.startswith("-"):
            display_name = display_name[1:].replace("-", "/")
        return display_name


def should_skip_message(text_content: str) -> bool:
    """
    Determine if a message should be skipped in transcript rendering.

    This is the centralized logic for filtering out unwanted messages.
    """
    is_system = is_system_message(text_content)
    is_command = is_command_message(text_content)
    is_output = is_local_command_output(text_content)

    # Skip system messages that are not command messages AND not local command output
    return is_system and not is_command and not is_output


def extract_init_command_description(text_content: str) -> str:
    """
    Extract a meaningful description from init command content.

    Returns a user-friendly description for init commands instead of raw XML.
    """
    if "<command-name>init" in text_content and "<command-contents>" in text_content:
        return "Claude Initializes Codebase Documentation Guide (/init command)"
    return text_content


def should_use_as_session_starter(text_content: str) -> bool:
    """
    Determine if a user message should be used as a session starter preview.

    This filters out system messages, warmup messages, and most command messages,
    except for 'init' commands which are typically the start of a new session.
    """
    # Skip warmup messages
    if text_content.strip() == "Warmup":
        return False

    # Skip system messages
    if is_system_message(text_content):
        return False

    # Skip command messages except for 'init' commands
    if "<command-name>" in text_content:
        return "<command-name>init" in text_content

    return True


# Constants
FIRST_USER_MESSAGE_PREVIEW_LENGTH = 1000


def create_session_preview(text_content: str) -> str:
    """Create a truncated preview of first user message for session display.

    Args:
        text_content: The raw text content from the first user message

    Returns:
        A preview string, truncated to FIRST_USER_MESSAGE_PREVIEW_LENGTH with
        ellipsis if needed, with init commands converted to friendly descriptions,
        and IDE tags replaced with compact emoji indicators.
    """
    # Apply init command transformation first
    preview_content = extract_init_command_description(text_content)

    # Apply compact IDE tag indicators BEFORE truncation
    preview_content = _compact_ide_tags_for_preview(preview_content)

    # Then truncate if needed
    if len(preview_content) > FIRST_USER_MESSAGE_PREVIEW_LENGTH:
        return preview_content[:FIRST_USER_MESSAGE_PREVIEW_LENGTH] + "..."
    return preview_content


def extract_text_content_length(content: Union[str, List[ContentItem]]) -> int:
    """Get the length of text content for quick checks without full extraction."""
    if isinstance(content, str):
        return len(content.strip())

    # For list content, count only text items
    total_length = 0
    for item in content:
        # Only count TextContent items, skip tool/thinking/image items
        if isinstance(item, TextContent):
            total_length += len(item.text.strip())

    return total_length


def extract_working_directories(
    entries: List[TranscriptEntry] | List[SessionCacheData],
) -> List[str]:
    """Extract unique working directories from a list of entries.

    Ordered by timestamp (most recent first).

    Args:
        entries: List of entries to extract working directories from

    Returns:
        List of unique working directory paths found in the entries
    """
    working_directories: dict[str, str] = {}

    for entry in entries:
        cwd = getattr(entry, "cwd", None)
        if not cwd:
            continue

        # Get appropriate timestamp based on entry type
        if isinstance(entry, SessionCacheData):
            timestamp = entry.last_timestamp
        elif hasattr(entry, "timestamp"):
            timestamp = getattr(entry, "timestamp", "")
        else:
            timestamp = ""

        working_directories[cwd] = timestamp

    # Sort by timestamp (most recent first) and return just the paths
    sorted_dirs = sorted(working_directories.items(), key=lambda x: x[1], reverse=True)
    return [path for path, _ in sorted_dirs]


def _compact_ide_tags_for_preview(text_content: str) -> str:
    """Replace verbose IDE/system tags with compact emoji indicators for previews.

    Only processes tags at the START of the content (where VS Code places them).
    Tags appearing later in the text (e.g., inside quoted JSONL) are left unchanged.

    Transforms:
    - <ide_opened_file>...path/to/file...</ide_opened_file> -> 📎 /path/to/file
    - <ide_selection>...path/to/file...</ide_selection> -> ✂️ /path/to/file
    - <ide_diagnostics>...</ide_diagnostics> -> 🩺 diagnostics
    - <bash-input>command</bash-input> -> 💻 command

    Args:
        text_content: Raw text content that may contain IDE/system tags

    Returns:
        Text with leading tags replaced by compact indicators
    """

    def _extract_file_path(content: str) -> str | None:
        """Extract file path from IDE tag content."""
        # Try to find an absolute path (starts with /)
        # Stop at: whitespace, colon followed by newline, or "in the IDE"
        path_match = re.search(
            r"(/[^\s:]+(?:\.[^\s:]+)?)(?::\s|\s+in\s+the\s+IDE|\s*$|\s)", content
        )
        if path_match:
            return path_match.group(1).rstrip(".:")

        # Fallback: look for "file" or "from" followed by a path
        path_match = re.search(r"(?:file|from)\s+(/[^\s:]+)", content)
        if path_match:
            return path_match.group(1).rstrip(".:")

        return None

    # Process only LEADING IDE tags - stop when we hit non-IDE content
    # This prevents replacing tags inside quoted strings/JSONL content
    # Uses shared patterns from parser.py for consistency
    compact_parts: list[str] = []
    remaining = text_content

    # Compiled pattern for bash-input (not in parser.py as it's preview-specific)
    bash_input_pattern = re.compile(r"<bash-input>(.*?)</bash-input>", re.DOTALL)

    while remaining:
        # Strip leading whitespace for matching
        stripped = remaining.lstrip()

        # Try to match each IDE tag type at the start of stripped text
        # Check for <ide_opened_file> at start (using shared pattern)
        match = IDE_OPENED_FILE_PATTERN.match(stripped)
        if match:
            content = match.group(1).strip()
            filepath = _extract_file_path(content)
            compact_parts.append(f"📎 {filepath}" if filepath else "📎 file")
            remaining = stripped[match.end() :]
            continue

        # Check for <ide_selection> at start (using shared pattern)
        match = IDE_SELECTION_PATTERN.match(stripped)
        if match:
            content = match.group(1).strip()
            filepath = _extract_file_path(content)
            compact_parts.append(f"✂️ {filepath}" if filepath else "✂️ selection")
            remaining = stripped[match.end() :]
            continue

        # Check for <post-tool-use-hook><ide_diagnostics>... (using shared pattern)
        match = IDE_DIAGNOSTICS_PATTERN.match(stripped)
        if match:
            compact_parts.append("🩺 diagnostics")
            remaining = stripped[match.end() :]
            continue

        # Check for <bash-input>command</bash-input> at start
        match = bash_input_pattern.match(stripped)
        if match:
            command = match.group(1).strip()
            # Truncate very long commands
            if len(command) > 50:
                command = command[:47] + "..."
            compact_parts.append(f"💻 {command}")
            remaining = stripped[match.end() :]
            continue

        # No more tags at start - stop processing
        break

    # Combine compact indicators with remaining content
    if compact_parts:
        # Add newline between indicators and content if there's remaining text
        prefix = "\n".join(compact_parts)
        if remaining.strip():
            return f"{prefix}\n{remaining.lstrip()}"
        return prefix

    return text_content


def get_warmup_session_ids(messages: List[TranscriptEntry]) -> set[str]:
    """Get set of session IDs that are warmup-only sessions.

    Pre-computes warmup status for all sessions for efficiency (O(n) once,
    then O(1) lookup per session).

    Args:
        messages: List of all transcript entries

    Returns:
        Set of session IDs that contain only warmup messages
    """
    from .parser import extract_text_content

    # Group user message text by session
    session_user_messages: Dict[str, List[str]] = {}

    for message in messages:
        if isinstance(message, UserTranscriptEntry) and hasattr(message, "message"):
            session_id = getattr(message, "sessionId", "")
            if session_id:
                text_content = extract_text_content(message.message.content).strip()
                if session_id not in session_user_messages:
                    session_user_messages[session_id] = []
                session_user_messages[session_id].append(text_content)

    # Find sessions where ALL user messages are "Warmup"
    warmup_sessions: set[str] = set()
    for session_id, user_msgs in session_user_messages.items():
        if user_msgs and all(msg == "Warmup" for msg in user_msgs):
            warmup_sessions.add(session_id)

    return warmup_sessions
