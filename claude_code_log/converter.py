#!/usr/bin/env python3
"""Convert Claude transcript JSONL files to HTML."""

from pathlib import Path
import traceback
from typing import List, Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .cache import CacheManager

from .utils import (
    format_timestamp_range,
    get_project_display_name,
    should_use_as_session_starter,
    create_session_preview,
    extract_working_directories,
    get_warmup_session_ids,
)
from .cache import CacheManager, SessionCacheData, get_library_version
from .parser import (
    load_transcript,
    load_directory_transcripts,
    filter_messages_by_date,
)
from .models import (
    TranscriptEntry,
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    UserTranscriptEntry,
    ToolResultContent,
)
from .renderer import get_renderer


def deduplicate_messages(messages: List[TranscriptEntry]) -> List[TranscriptEntry]:
    """Remove duplicate messages based on (type, timestamp, sessionId, content_key).

    Messages with the exact same timestamp are duplicates by definition -
    the differences (like IDE selection tags) are just logging artifacts.

    We need a content-based key to handle two cases:
    1. Version stutter: Same message logged twice during Claude Code upgrade
       -> Same timestamp, same message.id or tool_use_id -> SHOULD deduplicate
    2. Concurrent tool results: Multiple tool results with same timestamp
       -> Same timestamp, different tool_use_ids -> should NOT deduplicate

    Args:
        messages: List of transcript entries to deduplicate

    Returns:
        List of deduplicated messages, preserving order (first occurrence kept)
    """
    # Track seen (message_type, timestamp, is_meta, session_id, content_key) tuples
    seen: set[tuple[str, str, bool, str, str]] = set()
    deduplicated: List[TranscriptEntry] = []

    for message in messages:
        # Get basic message type
        message_type = getattr(message, "type", "unknown")

        # For system messages, include level to differentiate info/warning/error
        if isinstance(message, SystemTranscriptEntry):
            level = getattr(message, "level", "info")
            message_type = f"system-{level}"

        # Get timestamp
        timestamp = getattr(message, "timestamp", "")

        # Get isMeta flag (slash command prompts have isMeta=True with same timestamp as parent)
        is_meta = getattr(message, "isMeta", False)

        # Get sessionId for multi-session report deduplication
        session_id = getattr(message, "sessionId", "")

        # Get content key for differentiating concurrent messages
        # - For assistant messages: use message.id (same for stutters, different for different msgs)
        # - For user messages with tool results: use first tool_use_id
        # - For other messages: use uuid as fallback
        content_key = ""
        if isinstance(message, AssistantTranscriptEntry):
            # For assistant messages, use the message id
            content_key = message.message.id
        elif isinstance(message, UserTranscriptEntry):
            # For user messages, check for tool results
            if isinstance(message.message.content, list):
                for item in message.message.content:
                    if isinstance(item, ToolResultContent):
                        content_key = item.tool_use_id
                        break
        # Fallback to uuid if no content key found
        if not content_key:
            content_key = getattr(message, "uuid", "")

        # Create deduplication key - include content_key for proper handling
        # of both version stutters and concurrent tool results
        dedup_key = (message_type, timestamp, is_meta, session_id, content_key)

        # Keep only first occurrence
        if dedup_key not in seen:
            seen.add(dedup_key)
            deduplicated.append(message)

    return deduplicated


def convert_jsonl_to_html(
    input_path: Path,
    output_path: Optional[Path] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    generate_individual_sessions: bool = True,
    use_cache: bool = True,
    silent: bool = False,
) -> Path:
    """Convert JSONL transcript(s) to HTML file(s).

    Convenience wrapper around convert_jsonl_to() for HTML format.
    """
    return convert_jsonl_to(
        "html",
        input_path,
        output_path,
        from_date,
        to_date,
        generate_individual_sessions,
        use_cache,
        silent,
    )


def convert_jsonl_to(
    format: str,
    input_path: Path,
    output_path: Optional[Path] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    generate_individual_sessions: bool = True,
    use_cache: bool = True,
    silent: bool = False,
) -> Path:
    """Convert JSONL transcript(s) to the specified format."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")

    # Initialize cache manager for directory mode
    cache_manager = None
    if use_cache and input_path.is_dir():
        try:
            library_version = get_library_version()
            cache_manager = CacheManager(input_path, library_version)
        except Exception as e:
            print(f"Warning: Failed to initialize cache manager: {e}")

    if input_path.is_file():
        # Single file mode - cache only available for directory mode
        if output_path is None:
            output_path = input_path.with_suffix(f".{format}")
        messages = load_transcript(input_path, silent=silent)
        title = f"Claude Transcript - {input_path.stem}"
        cache_was_updated = False  # No cache in single file mode
    else:
        # Directory mode - Cache-First Approach
        if output_path is None:
            output_path = input_path / f"combined_transcripts.{format}"

        # Phase 1: Ensure cache is fresh and populated
        cache_was_updated = ensure_fresh_cache(
            input_path, cache_manager, from_date, to_date, silent
        )

        # Phase 2: Load messages (will use fresh cache when available)
        messages = load_directory_transcripts(
            input_path, cache_manager, from_date, to_date, silent
        )

        # Extract working directories directly from parsed messages
        working_directories = extract_working_directories(messages)

        project_title = get_project_display_name(input_path.name, working_directories)
        title = f"Claude Transcripts - {project_title}"

    # Apply date filtering
    messages = filter_messages_by_date(messages, from_date, to_date)

    # Deduplicate messages (removes version stutters while preserving concurrent tool results)
    messages = deduplicate_messages(messages)

    # Update title to include date range if specified
    if from_date or to_date:
        date_range_parts: List[str] = []
        if from_date:
            date_range_parts.append(f"from {from_date}")
        if to_date:
            date_range_parts.append(f"to {to_date}")
        date_range_str = " ".join(date_range_parts)
        title += f" ({date_range_str})"

    # Generate combined output file (check if regeneration needed)
    assert output_path is not None
    renderer = get_renderer(format)
    should_regenerate = (
        renderer.is_outdated(output_path)
        or from_date is not None
        or to_date is not None
        or not output_path.exists()
        or (
            input_path.is_dir() and cache_was_updated
        )  # Regenerate if JSONL files changed
    )

    if should_regenerate:
        content = renderer.generate(messages, title)
        assert content is not None
        output_path.write_text(content, encoding="utf-8")
    else:
        print(
            f"{format.upper()} file {output_path.name} is current, skipping regeneration"
        )

    # Generate individual session files if requested and in directory mode
    if generate_individual_sessions and input_path.is_dir():
        _generate_individual_session_files(
            format,
            messages,
            input_path,
            from_date,
            to_date,
            cache_manager,
            cache_was_updated,
        )

    return output_path


def ensure_fresh_cache(
    project_dir: Path,
    cache_manager: Optional[CacheManager],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    silent: bool = False,
) -> bool:
    """Ensure cache is fresh and populated. Returns True if cache was updated."""
    if cache_manager is None:
        return False

    # Check if cache needs updating
    jsonl_files = list(project_dir.glob("*.jsonl"))
    if not jsonl_files:
        return False

    # Get cached project data
    cached_project_data = cache_manager.get_cached_project_data()

    # Check various invalidation conditions
    modified_files = cache_manager.get_modified_files(jsonl_files)
    needs_update = (
        cached_project_data is None
        or from_date is not None
        or to_date is not None
        or bool(modified_files)  # Files changed
        or (cached_project_data.total_message_count == 0 and jsonl_files)  # Stale cache
    )

    if not needs_update:
        return False  # Cache is already fresh

    # Load and process messages to populate cache
    if not silent:
        print(f"Updating cache for {project_dir.name}...")
    messages = load_directory_transcripts(
        project_dir, cache_manager, from_date, to_date, silent
    )

    # Update cache with fresh data
    _update_cache_with_session_data(cache_manager, messages)
    return True


def _update_cache_with_session_data(
    cache_manager: CacheManager, messages: List[TranscriptEntry]
) -> None:
    """Update cache with session and project aggregate data."""
    from .parser import extract_text_content

    # Collect session data (similar to _collect_project_sessions but for cache)
    session_summaries: Dict[str, str] = {}
    uuid_to_session: Dict[str, str] = {}
    uuid_to_session_backup: Dict[str, str] = {}

    # Build mapping from message UUID to session ID
    for message in messages:
        if hasattr(message, "uuid") and hasattr(message, "sessionId"):
            message_uuid = getattr(message, "uuid", "")
            session_id = getattr(message, "sessionId", "")
            if message_uuid and session_id:
                if type(message) is AssistantTranscriptEntry:
                    uuid_to_session[message_uuid] = session_id
                else:
                    uuid_to_session_backup[message_uuid] = session_id

    # Map summaries to sessions
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

    # Group messages by session and calculate session data
    sessions_cache_data: Dict[str, SessionCacheData] = {}

    # Track token usage and timestamps for project aggregates
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_creation_tokens = 0
    total_cache_read_tokens = 0
    total_message_count = len(messages)
    earliest_timestamp = ""
    latest_timestamp = ""
    seen_request_ids: set[str] = set()

    for message in messages:
        # Update project-level timestamp tracking
        if hasattr(message, "timestamp"):
            message_timestamp = getattr(message, "timestamp", "")
            if message_timestamp:
                if not latest_timestamp or message_timestamp > latest_timestamp:
                    latest_timestamp = message_timestamp
                if not earliest_timestamp or message_timestamp < earliest_timestamp:
                    earliest_timestamp = message_timestamp

        # Process session-level data (skip summaries)
        if hasattr(message, "sessionId") and not isinstance(
            message, SummaryTranscriptEntry
        ):
            session_id = getattr(message, "sessionId", "")
            if not session_id:
                continue

            if session_id not in sessions_cache_data:
                sessions_cache_data[session_id] = SessionCacheData(
                    session_id=session_id,
                    summary=session_summaries.get(session_id),
                    first_timestamp=getattr(message, "timestamp", ""),
                    last_timestamp=getattr(message, "timestamp", ""),
                    message_count=0,
                    first_user_message="",
                    cwd=getattr(message, "cwd", None),
                )

            session_cache = sessions_cache_data[session_id]
            session_cache.message_count += 1
            current_timestamp = getattr(message, "timestamp", "")
            if current_timestamp:
                session_cache.last_timestamp = current_timestamp

            # Get first user message for preview
            if (
                isinstance(message, UserTranscriptEntry)
                and not session_cache.first_user_message
                and hasattr(message, "message")
            ):
                first_user_content = extract_text_content(message.message.content)
                if should_use_as_session_starter(first_user_content):
                    session_cache.first_user_message = create_session_preview(
                        first_user_content
                    )

        # Calculate token usage for assistant messages
        if message.type == "assistant" and hasattr(message, "message"):
            assistant_message = getattr(message, "message")
            request_id = getattr(message, "requestId", None)
            session_id = getattr(message, "sessionId", "")

            if (
                hasattr(assistant_message, "usage")
                and assistant_message.usage
                and request_id
                and request_id not in seen_request_ids
            ):
                seen_request_ids.add(request_id)
                usage = assistant_message.usage

                # Add to project totals
                total_input_tokens += usage.input_tokens or 0
                total_output_tokens += usage.output_tokens or 0
                if usage.cache_creation_input_tokens:
                    total_cache_creation_tokens += usage.cache_creation_input_tokens
                if usage.cache_read_input_tokens:
                    total_cache_read_tokens += usage.cache_read_input_tokens

                # Add to session totals
                if session_id in sessions_cache_data:
                    session_cache = sessions_cache_data[session_id]
                    session_cache.total_input_tokens += usage.input_tokens or 0
                    session_cache.total_output_tokens += usage.output_tokens or 0
                    if usage.cache_creation_input_tokens:
                        session_cache.total_cache_creation_tokens += (
                            usage.cache_creation_input_tokens
                        )
                    if usage.cache_read_input_tokens:
                        session_cache.total_cache_read_tokens += (
                            usage.cache_read_input_tokens
                        )

    # Filter out warmup-only and empty sessions before caching
    warmup_session_ids = get_warmup_session_ids(messages)
    sessions_cache_data = {
        sid: data
        for sid, data in sessions_cache_data.items()
        if sid not in warmup_session_ids
        and data.first_user_message  # Filter empty sessions (agent-only)
    }

    # Update cache with filtered session data
    cache_manager.update_session_cache(sessions_cache_data)

    # Update cache with working directories (from filtered sessions)
    cache_manager.update_working_directories(
        extract_working_directories(list(sessions_cache_data.values()))
    )

    # Update cache with project aggregates
    cache_manager.update_project_aggregates(
        total_message_count=total_message_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cache_creation_tokens=total_cache_creation_tokens,
        total_cache_read_tokens=total_cache_read_tokens,
        earliest_timestamp=earliest_timestamp,
        latest_timestamp=latest_timestamp,
    )


def _collect_project_sessions(messages: List[TranscriptEntry]) -> List[Dict[str, Any]]:
    """Collect session data for project index navigation."""
    from .parser import extract_text_content

    # Pre-compute warmup session IDs to filter them out
    warmup_session_ids = get_warmup_session_ids(messages)

    # Pre-process to find and attach session summaries
    # This matches the logic from renderer.py generate_html() exactly
    session_summaries: Dict[str, str] = {}
    uuid_to_session: Dict[str, str] = {}
    uuid_to_session_backup: Dict[str, str] = {}

    # Build mapping from message UUID to session ID across ALL messages
    # This allows summaries from later sessions to be matched to earlier sessions
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
    # Summaries can be in different sessions than the messages they summarize
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

    # Group messages by session (excluding warmup-only sessions)
    sessions: Dict[str, Dict[str, Any]] = {}
    for message in messages:
        if hasattr(message, "sessionId") and not isinstance(
            message, SummaryTranscriptEntry
        ):
            session_id = getattr(message, "sessionId", "")
            if not session_id or session_id in warmup_session_ids:
                continue

            if session_id not in sessions:
                sessions[session_id] = {
                    "id": session_id,
                    "summary": session_summaries.get(session_id),
                    "first_timestamp": getattr(message, "timestamp", ""),
                    "last_timestamp": getattr(message, "timestamp", ""),
                    "message_count": 0,
                    "first_user_message": "",
                }

            sessions[session_id]["message_count"] += 1
            current_timestamp = getattr(message, "timestamp", "")
            if current_timestamp:
                sessions[session_id]["last_timestamp"] = current_timestamp

            # Get first user message for preview (skip system messages)
            if (
                isinstance(message, UserTranscriptEntry)
                and not sessions[session_id]["first_user_message"]
                and hasattr(message, "message")
            ):
                first_user_content = extract_text_content(message.message.content)
                if should_use_as_session_starter(first_user_content):
                    sessions[session_id]["first_user_message"] = create_session_preview(
                        first_user_content
                    )

    # Convert to list format with formatted timestamps
    session_list: List[Dict[str, Any]] = []
    for session_data in sessions.values():
        from .utils import format_timestamp

        first_ts = session_data["first_timestamp"]
        last_ts = session_data["last_timestamp"]
        timestamp_range = ""
        if first_ts and last_ts:
            if first_ts == last_ts:
                timestamp_range = format_timestamp(first_ts)
            else:
                timestamp_range = (
                    f"{format_timestamp(first_ts)} - {format_timestamp(last_ts)}"
                )
        elif first_ts:
            timestamp_range = format_timestamp(first_ts)

        session_dict: Dict[str, Any] = {
            "id": session_data["id"],
            "summary": session_data["summary"],
            "timestamp_range": timestamp_range,
            "message_count": session_data["message_count"],
            "first_user_message": session_data["first_user_message"]
            if session_data["first_user_message"] != ""
            else "[No user message found in session.]",
        }
        # Skip sessions with no user messages (empty sessions / agent-only)
        if session_data["first_user_message"] == "":
            continue
        session_list.append(session_dict)

    # Sort by first timestamp (ascending order, oldest first like transcript page)
    return sorted(
        session_list, key=lambda s: s.get("timestamp_range", ""), reverse=False
    )


def _generate_individual_session_files(
    format: str,
    messages: List[TranscriptEntry],
    output_dir: Path,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    cache_manager: Optional["CacheManager"] = None,
    cache_was_updated: bool = False,
) -> None:
    """Generate individual files for each session in the specified format."""
    # Pre-compute warmup sessions to exclude them
    warmup_session_ids = get_warmup_session_ids(messages)

    # Find all unique session IDs (excluding warmup sessions)
    session_ids: set[str] = set()
    for message in messages:
        if hasattr(message, "sessionId"):
            session_id: str = getattr(message, "sessionId")
            if session_id and session_id not in warmup_session_ids:
                session_ids.add(session_id)

    # Get session data from cache for better titles
    session_data: Dict[str, Any] = {}
    working_directories = None
    if cache_manager is not None:
        project_cache = cache_manager.get_cached_project_data()
        if project_cache:
            session_data = {s.session_id: s for s in project_cache.sessions.values()}
            # Get working directories for project title
            if project_cache.working_directories:
                working_directories = project_cache.working_directories

    project_title = get_project_display_name(output_dir.name, working_directories)

    # Generate HTML file for each session
    for session_id in session_ids:
        # Create session-specific title using cache data if available
        if session_id in session_data:
            session_cache = session_data[session_id]
            if session_cache.summary:
                session_title = f"{project_title}: {session_cache.summary}"
            else:
                # Fall back to first user message preview
                preview = session_cache.first_user_message
                if preview and len(preview) > 50:
                    preview = preview[:50] + "..."
                session_title = (
                    f"{project_title}: {preview}"
                    if preview
                    else f"{project_title}: Session {session_id[:8]}"
                )
        else:
            # Fall back to basic session title
            session_title = f"{project_title}: Session {session_id[:8]}"

        # Add date range if specified
        if from_date or to_date:
            date_range_parts: List[str] = []
            if from_date:
                date_range_parts.append(f"from {from_date}")
            if to_date:
                date_range_parts.append(f"to {to_date}")
            date_range_str = " ".join(date_range_parts)
            session_title += f" ({date_range_str})"

        # Check if session file needs regeneration
        session_file_path = output_dir / f"session-{session_id}.{format}"
        renderer = get_renderer(format)

        # Only regenerate if outdated, doesn't exist, or date filtering is active
        should_regenerate_session = (
            renderer.is_outdated(session_file_path)
            or from_date is not None
            or to_date is not None
            or not session_file_path.exists()
            or cache_was_updated  # Regenerate if JSONL files changed
        )

        if should_regenerate_session:
            # Generate session content
            session_content = renderer.generate_session(
                messages, session_id, session_title, cache_manager
            )
            assert session_content is not None
            # Write session file
            session_file_path.write_text(session_content, encoding="utf-8")
        else:
            print(
                f"Session file {session_file_path.name} is current, skipping regeneration"
            )


def process_projects_hierarchy(
    projects_path: Path,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    use_cache: bool = True,
    generate_individual_sessions: bool = True,
) -> Path:
    """Process the entire ~/.claude/projects/ hierarchy and create linked HTML files."""
    if not projects_path.exists():
        raise FileNotFoundError(f"Projects path not found: {projects_path}")

    # Find all project directories (those with JSONL files)
    project_dirs: List[Path] = []
    for child in projects_path.iterdir():
        if child.is_dir() and list(child.glob("*.jsonl")):
            project_dirs.append(child)

    if not project_dirs:
        raise FileNotFoundError(
            f"No project directories with JSONL files found in {projects_path}"
        )

    # Get library version for cache management
    library_version = get_library_version()

    # Process each project directory
    project_summaries: List[Dict[str, Any]] = []
    any_cache_updated = False  # Track if any project had cache updates
    for project_dir in sorted(project_dirs):
        try:
            # Initialize cache manager for this project
            cache_manager = None
            if use_cache:
                try:
                    cache_manager = CacheManager(project_dir, library_version)
                except Exception as e:
                    print(f"Warning: Failed to initialize cache for {project_dir}: {e}")

            # Phase 1: Ensure cache is fresh and populated
            cache_was_updated = ensure_fresh_cache(
                project_dir, cache_manager, from_date, to_date
            )
            if cache_was_updated:
                any_cache_updated = True

            # Phase 2: Generate HTML for this project (optionally individual session files)
            output_path = convert_jsonl_to_html(
                project_dir,
                None,
                from_date,
                to_date,
                generate_individual_sessions,
                use_cache,
            )

            # Get project info for index - use cached data if available
            jsonl_files = list(project_dir.glob("*.jsonl"))
            jsonl_count = len(jsonl_files)
            last_modified: float = (
                max(f.stat().st_mtime for f in jsonl_files) if jsonl_files else 0.0
            )

            # Phase 3: Use fresh cached data for index aggregation
            if cache_manager is not None:
                cached_project_data = cache_manager.get_cached_project_data()
                if cached_project_data is not None:
                    # Use cached aggregation data
                    project_summaries.append(
                        {
                            "name": project_dir.name,
                            "path": project_dir,
                            "html_file": f"{project_dir.name}/{output_path.name}",
                            "jsonl_count": jsonl_count,
                            "message_count": cached_project_data.total_message_count,
                            "last_modified": last_modified,
                            "total_input_tokens": cached_project_data.total_input_tokens,
                            "total_output_tokens": cached_project_data.total_output_tokens,
                            "total_cache_creation_tokens": cached_project_data.total_cache_creation_tokens,
                            "total_cache_read_tokens": cached_project_data.total_cache_read_tokens,
                            "latest_timestamp": cached_project_data.latest_timestamp,
                            "earliest_timestamp": cached_project_data.earliest_timestamp,
                            "working_directories": cached_project_data.working_directories,
                            "sessions": [
                                {
                                    "id": session_data.session_id,
                                    "summary": session_data.summary,
                                    "timestamp_range": format_timestamp_range(
                                        session_data.first_timestamp,
                                        session_data.last_timestamp,
                                    ),
                                    "first_timestamp": session_data.first_timestamp,
                                    "last_timestamp": session_data.last_timestamp,
                                    "message_count": session_data.message_count,
                                    "first_user_message": session_data.first_user_message
                                    or "[No user message found in session.]",
                                }
                                for session_data in cached_project_data.sessions.values()
                                # Filter out warmup-only and empty sessions (agent-only)
                                if session_data.first_user_message
                                and session_data.first_user_message != "Warmup"
                            ],
                        }
                    )
                    continue

            # Fallback for when cache is not available (should be rare)
            print(
                f"Warning: No cached data available for {project_dir.name}, using fallback processing"
            )
            messages = load_directory_transcripts(
                project_dir, cache_manager, from_date, to_date
            )
            if from_date or to_date:
                messages = filter_messages_by_date(messages, from_date, to_date)

            # Calculate token usage aggregation and find first/last interaction timestamps
            total_input_tokens = 0
            total_output_tokens = 0
            total_cache_creation_tokens = 0
            total_cache_read_tokens = 0
            latest_timestamp = ""
            earliest_timestamp = ""

            # Track requestIds to avoid double-counting tokens
            seen_request_ids: set[str] = set()

            # Collect session data for this project
            sessions_data = _collect_project_sessions(messages)

            for message in messages:
                # Track latest and earliest timestamps across all messages
                if hasattr(message, "timestamp"):
                    message_timestamp = getattr(message, "timestamp", "")
                    if message_timestamp:
                        # Track latest timestamp
                        if not latest_timestamp or message_timestamp > latest_timestamp:
                            latest_timestamp = message_timestamp

                        # Track earliest timestamp
                        if (
                            not earliest_timestamp
                            or message_timestamp < earliest_timestamp
                        ):
                            earliest_timestamp = message_timestamp

                # Calculate token usage for assistant messages
                if message.type == "assistant" and hasattr(message, "message"):
                    assistant_message = getattr(message, "message")
                    request_id = getattr(message, "requestId", None)

                    if (
                        hasattr(assistant_message, "usage")
                        and assistant_message.usage
                        and request_id
                        and request_id not in seen_request_ids
                    ):
                        # Mark requestId as seen to avoid double-counting
                        seen_request_ids.add(request_id)

                        usage = assistant_message.usage
                        total_input_tokens += usage.input_tokens or 0
                        total_output_tokens += usage.output_tokens or 0
                        if usage.cache_creation_input_tokens:
                            total_cache_creation_tokens += (
                                usage.cache_creation_input_tokens
                            )
                        if usage.cache_read_input_tokens:
                            total_cache_read_tokens += usage.cache_read_input_tokens

            project_summaries.append(
                {
                    "name": project_dir.name,
                    "path": project_dir,
                    "html_file": f"{project_dir.name}/{output_path.name}",
                    "jsonl_count": jsonl_count,
                    "message_count": len(messages),
                    "last_modified": last_modified,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "total_cache_creation_tokens": total_cache_creation_tokens,
                    "total_cache_read_tokens": total_cache_read_tokens,
                    "latest_timestamp": latest_timestamp,
                    "earliest_timestamp": earliest_timestamp,
                    "working_directories": extract_working_directories(messages),
                    "sessions": sessions_data,
                }
            )
        except Exception as e:
            print(
                f"Warning: Failed to process {project_dir}: {e}\n"
                f"Previous (in alphabetical order) file before error: {project_summaries[-1]}"
                f"\n{traceback.format_exc()}"
            )
            continue

    # Generate index HTML (always regenerate if outdated)
    index_path = projects_path / "index.html"
    renderer = get_renderer("html")
    if renderer.is_outdated(index_path) or from_date or to_date or any_cache_updated:
        index_html = renderer.generate_projects_index(
            project_summaries, from_date, to_date
        )
        assert index_html is not None
        index_path.write_text(index_html, encoding="utf-8")
    else:
        print("Index HTML is current, skipping regeneration")

    return index_path
