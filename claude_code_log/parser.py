#!/usr/bin/env python3
"""Parse and extract data from Claude transcript JSONL files."""

import json
import re
from typing import Any, Callable, Optional, Union, cast
from datetime import datetime

from pydantic import BaseModel

from .models import (
    # Content types
    ContentItem,
    TextContent,
    ThinkingContent,
    ToolUseContent,
    ToolResultContent,
    ImageContent,
    # User message content models
    SlashCommandMessage,
    CommandOutputMessage,
    BashInputMessage,
    BashOutputMessage,
    CompactedSummaryMessage,
    UserMemoryMessage,
    UserSlashCommandMessage,
    UserTextMessage,
    IdeNotificationContent,
    IdeOpenedFile,
    IdeSelection,
    IdeDiagnostic,
    # Assistant message content models
    BashInput,
    ReadInput,
    WriteInput,
    EditInput,
    EditItem,
    MultiEditInput,
    GlobInput,
    GrepInput,
    TaskInput,
    TodoWriteInput,
    TodoWriteItem,
    AskUserQuestionInput,
    AskUserQuestionItem,
    AskUserQuestionOption,
    ExitPlanModeInput,
    ToolInput,
    # Usage and transcript entry types
    UsageInfo,
    MessageType,
    TranscriptEntry,
    UserTranscriptEntry,
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    QueueOperationTranscriptEntry,
)


def extract_text_content(content: Optional[list[ContentItem]]) -> str:
    """Extract text content from Claude message content structure.

    Supports both custom models (TextContent, ThinkingContent) and official
    Anthropic SDK types (TextBlock, ThinkingBlock).
    """
    if not content:
        return ""
    text_parts: list[str] = []
    for item in content:
        # Skip thinking content
        if (
            isinstance(item, ThinkingContent)
            or getattr(item, "type", None) == "thinking"
        ):
            continue
        # Handle text content
        if hasattr(item, "text"):
            text_parts.append(getattr(item, "text"))  # type: ignore[arg-type]
    return "\n".join(text_parts)


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse ISO timestamp to datetime object."""
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# =============================================================================
# User Message Content Parsing
# =============================================================================


def parse_slash_command(text: str) -> Optional[SlashCommandMessage]:
    """Parse slash command tags from text.

    Args:
        text: Raw text that may contain command-name, command-args, command-contents tags

    Returns:
        SlashCommandMessage if tags found, None otherwise
    """
    command_name_match = re.search(r"<command-name>([^<]+)</command-name>", text)
    if not command_name_match:
        return None

    command_name = command_name_match.group(1).strip()

    command_args_match = re.search(r"<command-args>([^<]*)</command-args>", text)
    command_args = command_args_match.group(1).strip() if command_args_match else ""

    # Parse command contents, handling JSON format
    command_contents_match = re.search(
        r"<command-contents>(.+?)</command-contents>", text, re.DOTALL
    )
    command_contents = ""
    if command_contents_match:
        contents_text = command_contents_match.group(1).strip()
        # Try to parse as JSON and extract the text field
        try:
            contents_json: Any = json.loads(contents_text)
            if isinstance(contents_json, dict) and "text" in contents_json:
                text_dict = cast(dict[str, Any], contents_json)
                text_value = text_dict["text"]
                command_contents = str(text_value)
            else:
                command_contents = contents_text
        except json.JSONDecodeError:
            command_contents = contents_text

    return SlashCommandMessage(
        command_name=command_name,
        command_args=command_args,
        command_contents=command_contents,
    )


def parse_command_output(text: str) -> Optional[CommandOutputMessage]:
    """Parse command output tags from text.

    Args:
        text: Raw text that may contain local-command-stdout tags

    Returns:
        CommandOutputMessage if tags found, None otherwise
    """
    stdout_match = re.search(
        r"<local-command-stdout>(.*?)</local-command-stdout>",
        text,
        re.DOTALL,
    )
    if not stdout_match:
        return None

    stdout_content = stdout_match.group(1).strip()
    # Check if content looks like markdown (starts with markdown headers)
    is_markdown = bool(re.match(r"^#+\s+", stdout_content, re.MULTILINE))

    return CommandOutputMessage(stdout=stdout_content, is_markdown=is_markdown)


def parse_bash_input(text: str) -> Optional[BashInputMessage]:
    """Parse bash input tags from text.

    Args:
        text: Raw text that may contain bash-input tags

    Returns:
        BashInputMessage if tags found, None otherwise
    """
    bash_match = re.search(r"<bash-input>(.*?)</bash-input>", text, re.DOTALL)
    if not bash_match:
        return None

    return BashInputMessage(command=bash_match.group(1).strip())


def parse_bash_output(text: str) -> Optional[BashOutputMessage]:
    """Parse bash output tags from text.

    Args:
        text: Raw text that may contain bash-stdout/bash-stderr tags

    Returns:
        BashOutputMessage if tags found, None otherwise
    """
    stdout_match = re.search(r"<bash-stdout>(.*?)</bash-stdout>", text, re.DOTALL)
    stderr_match = re.search(r"<bash-stderr>(.*?)</bash-stderr>", text, re.DOTALL)

    if not stdout_match and not stderr_match:
        return None

    stdout = stdout_match.group(1).strip() if stdout_match else None
    stderr = stderr_match.group(1).strip() if stderr_match else None

    # Convert empty strings to None for cleaner representation
    if stdout == "":
        stdout = None
    if stderr == "":
        stderr = None

    return BashOutputMessage(stdout=stdout, stderr=stderr)


# Shared regex patterns for IDE notification tags
IDE_OPENED_FILE_PATTERN = re.compile(
    r"<ide_opened_file>(.*?)</ide_opened_file>", re.DOTALL
)
IDE_SELECTION_PATTERN = re.compile(r"<ide_selection>(.*?)</ide_selection>", re.DOTALL)
IDE_DIAGNOSTICS_PATTERN = re.compile(
    r"<post-tool-use-hook>\s*<ide_diagnostics>(.*?)</ide_diagnostics>\s*</post-tool-use-hook>",
    re.DOTALL,
)


def parse_ide_notifications(text: str) -> Optional[IdeNotificationContent]:
    """Parse IDE notification tags from text.

    Handles:
    - <ide_opened_file>: Simple file open notifications
    - <ide_selection>: Code selection notifications
    - <post-tool-use-hook><ide_diagnostics>: JSON diagnostic arrays

    Args:
        text: Raw text that may contain IDE notification tags

    Returns:
        IdeNotificationContent if any tags found, None otherwise
    """
    opened_files: list[IdeOpenedFile] = []
    selections: list[IdeSelection] = []
    diagnostics: list[IdeDiagnostic] = []
    remaining_text = text

    # Pattern 1: <ide_opened_file>content</ide_opened_file>
    for match in IDE_OPENED_FILE_PATTERN.finditer(remaining_text):
        content = match.group(1).strip()
        opened_files.append(IdeOpenedFile(content=content))

    remaining_text = IDE_OPENED_FILE_PATTERN.sub("", remaining_text)

    # Pattern 2: <ide_selection>content</ide_selection>
    for match in IDE_SELECTION_PATTERN.finditer(remaining_text):
        content = match.group(1).strip()
        selections.append(IdeSelection(content=content))

    remaining_text = IDE_SELECTION_PATTERN.sub("", remaining_text)

    # Pattern 3: <post-tool-use-hook><ide_diagnostics>JSON</ide_diagnostics></post-tool-use-hook>
    for match in IDE_DIAGNOSTICS_PATTERN.finditer(remaining_text):
        json_content = match.group(1).strip()
        try:
            parsed_diagnostics: Any = json.loads(json_content)
            if isinstance(parsed_diagnostics, list):
                diagnostics.append(
                    IdeDiagnostic(
                        diagnostics=cast(list[dict[str, Any]], parsed_diagnostics)
                    )
                )
            else:
                # Not a list, store as raw content
                diagnostics.append(IdeDiagnostic(raw_content=json_content))
        except (json.JSONDecodeError, ValueError):
            # JSON parsing failed, store raw content
            diagnostics.append(IdeDiagnostic(raw_content=json_content))

    remaining_text = IDE_DIAGNOSTICS_PATTERN.sub("", remaining_text)

    # Only return if we found any IDE tags
    if not opened_files and not selections and not diagnostics:
        return None

    return IdeNotificationContent(
        opened_files=opened_files,
        selections=selections,
        diagnostics=diagnostics,
        remaining_text=remaining_text.strip(),
    )


# Pattern for compacted session summary detection
COMPACTED_SUMMARY_PREFIX = "This session is being continued from a previous conversation that ran out of context"


def parse_compacted_summary(
    content_list: list[ContentItem],
) -> Optional[CompactedSummaryMessage]:
    """Parse compacted session summary from content list.

    Compacted summaries are generated when a session runs out of context and
    needs to be continued. They contain a summary of the previous conversation.

    If the first text item starts with the compacted summary prefix, all text
    items are combined into a single CompactedSummaryMessage.

    Args:
        content_list: List of ContentItem from user message

    Returns:
        CompactedSummaryMessage if first text is a compacted summary, None otherwise
    """
    if not content_list or not hasattr(content_list[0], "text"):
        return None

    first_text = getattr(content_list[0], "text", "")
    if not first_text.startswith(COMPACTED_SUMMARY_PREFIX):
        return None

    # Combine all text content for compacted summaries
    # Use hasattr check to handle both TextContent models and SDK TextBlock objects
    texts = cast(
        list[str],
        [item.text for item in content_list if hasattr(item, "text")],  # type: ignore[union-attr]
    )
    all_text = "\n\n".join(texts)
    return CompactedSummaryMessage(summary_text=all_text)


# Pattern for user memory input tag
USER_MEMORY_PATTERN = re.compile(
    r"<user-memory-input>(.*?)</user-memory-input>", re.DOTALL
)


def parse_user_memory(text: str) -> Optional[UserMemoryMessage]:
    """Parse user memory input tag from text.

    User memory input contains context that the user has provided from
    their CLAUDE.md or other memory sources.

    Args:
        text: Raw text that may contain user memory input tag

    Returns:
        UserMemoryMessage if tag found, None otherwise
    """
    match = USER_MEMORY_PATTERN.search(text)
    if match:
        memory_content = match.group(1).strip()
        return UserMemoryMessage(memory_text=memory_content)
    return None


# Type alias for content models returned by parse_user_message_content
UserMessageContent = Union[
    CompactedSummaryMessage, UserMemoryMessage, UserSlashCommandMessage, UserTextMessage
]


def parse_user_message_content(
    content_list: list[ContentItem],
    is_slash_command: bool = False,
) -> Optional[UserMessageContent]:
    """Parse user message content into a structured content model.

    Returns a content model for HtmlRenderer to format. The caller can use
    isinstance() checks to determine the content type:
    - UserSlashCommandMessage: Slash command expanded prompts (isMeta=True)
    - CompactedSummaryMessage: Session continuation summaries
    - UserMemoryMessage: User memory input from CLAUDE.md
    - UserTextMessage: Normal user text with optional IDE notifications and images

    This function processes content items preserving their original order:
    - TextContent items have IDE notifications extracted, producing
      [IdeNotificationContent, TextContent] pairs
    - ImageContent items are preserved as-is

    Args:
        content_list: List of ContentItem from user message
        is_slash_command: True for slash command expanded prompts (isMeta=True)

    Returns:
        A content model, or None if content_list is empty.
    """
    if not content_list:
        return None

    # Slash command expanded prompts - combine all text as markdown
    if is_slash_command:
        all_text = "\n\n".join(
            getattr(item, "text", "") for item in content_list if hasattr(item, "text")
        )
        return UserSlashCommandMessage(text=all_text) if all_text else None

    # Get first text item for special case detection
    first_text_item = next(
        (item for item in content_list if hasattr(item, "text")),
        None,
    )
    first_text = getattr(first_text_item, "text", "") if first_text_item else ""

    # Check for compacted session summary first (handles text combining internally)
    compacted = parse_compacted_summary(content_list)
    if compacted:
        return compacted

    # Check for user memory input
    user_memory = parse_user_memory(first_text)
    if user_memory:
        return user_memory

    # Build items list preserving order, extracting IDE notifications from text
    items: list[TextContent | ImageContent | IdeNotificationContent] = []

    for item in content_list:
        # Check for text content
        if hasattr(item, "text"):
            item_text: str = getattr(item, "text")  # type: ignore[assignment]
            ide_content = parse_ide_notifications(item_text)

            if ide_content:
                # Add IDE notification item first
                items.append(ide_content)
                remaining_text: str = ide_content.remaining_text
            else:
                remaining_text = item_text

            # Add remaining text as TextContent if non-empty
            if remaining_text.strip():
                items.append(TextContent(type="text", text=remaining_text))
        elif isinstance(item, ImageContent):
            # ImageContent model - use as-is
            items.append(item)
        elif hasattr(item, "source") and getattr(item, "type", None) == "image":
            # Anthropic ImageContent - convert to our model
            items.append(ImageContent.model_validate(item.model_dump()))  # type: ignore[union-attr]

    # Return UserTextMessage with items list
    return UserTextMessage(items=items)


# =============================================================================
# Message Type Detection
# =============================================================================


def is_system_message(text_content: str) -> bool:
    """Check if a message is a system message that should be filtered out."""
    system_message_patterns = [
        "Caveat: The messages below were generated by the user while running local commands. DO NOT respond to these messages or otherwise consider them in your response unless the user explicitly asks you to.",
        "[Request interrupted by user for tool use]",
        "<local-command-stdout>",
    ]

    return any(text_content.startswith(pattern) for pattern in system_message_patterns)


def is_command_message(text_content: str) -> bool:
    """Check if a message contains command information that should be displayed."""
    return "<command-name>" in text_content and "<command-message>" in text_content


def is_local_command_output(text_content: str) -> bool:
    """Check if a message contains local command output."""
    return "<local-command-stdout>" in text_content


def is_bash_input(text_content: str) -> bool:
    """Check if a message contains bash input command."""
    return "<bash-input>" in text_content and "</bash-input>" in text_content


def is_bash_output(text_content: str) -> bool:
    """Check if a message contains bash command output."""
    return "<bash-stdout>" in text_content or "<bash-stderr>" in text_content


def is_warmup_only_session(messages: list[TranscriptEntry], session_id: str) -> bool:
    """Check if a session contains only warmup user messages.

    A warmup session is one where ALL user messages are literally just "Warmup".
    Sessions with no user messages return False (not considered warmup).

    Args:
        messages: List of all transcript entries
        session_id: The session ID to check

    Returns:
        True if ALL user messages in the session are "Warmup", False otherwise
    """
    user_messages_in_session: list[str] = []

    for message in messages:
        if (
            isinstance(message, UserTranscriptEntry)
            and getattr(message, "sessionId", "") == session_id
            and hasattr(message, "message")
        ):
            text_content = extract_text_content(message.message.content).strip()
            user_messages_in_session.append(text_content)

    # No user messages = not a warmup session
    if not user_messages_in_session:
        return False

    # All user messages must be exactly "Warmup"
    return all(msg == "Warmup" for msg in user_messages_in_session)


# =============================================================================
# Type Guards for TranscriptEntry
# =============================================================================


def as_user_entry(entry: TranscriptEntry) -> UserTranscriptEntry | None:
    """Return entry as UserTranscriptEntry if it is one, else None."""
    if entry.type == MessageType.USER:
        return cast(UserTranscriptEntry, entry)
    return None


def as_assistant_entry(entry: TranscriptEntry) -> AssistantTranscriptEntry | None:
    """Return entry as AssistantTranscriptEntry if it is one, else None."""
    if entry.type == MessageType.ASSISTANT:
        return cast(AssistantTranscriptEntry, entry)
    return None


# =============================================================================
# Tool Input Parsing
# =============================================================================

TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "Bash": BashInput,
    "Read": ReadInput,
    "Write": WriteInput,
    "Edit": EditInput,
    "MultiEdit": MultiEditInput,
    "Glob": GlobInput,
    "Grep": GrepInput,
    "Task": TaskInput,
    "TodoWrite": TodoWriteInput,
    "AskUserQuestion": AskUserQuestionInput,
    "ask_user_question": AskUserQuestionInput,  # Legacy tool name
    "ExitPlanMode": ExitPlanModeInput,
}


# -- Lenient Parsing Helpers --------------------------------------------------
# These functions create typed models even when strict validation fails.
# They use defaults for missing fields and skip invalid nested items.


def _parse_todowrite_lenient(data: dict[str, Any]) -> TodoWriteInput:
    """Parse TodoWrite input leniently, handling malformed data."""
    todos_raw = data.get("todos", [])
    valid_todos: list[TodoWriteItem] = []
    for item in todos_raw:
        if isinstance(item, dict):
            try:
                valid_todos.append(TodoWriteItem.model_validate(item))
            except Exception:
                pass
        elif isinstance(item, str):
            valid_todos.append(TodoWriteItem(content=item))
    return TodoWriteInput(todos=valid_todos)


def _parse_bash_lenient(data: dict[str, Any]) -> BashInput:
    """Parse Bash input leniently."""
    return BashInput(
        command=data.get("command", ""),
        description=data.get("description"),
        timeout=data.get("timeout"),
        run_in_background=data.get("run_in_background"),
    )


def _parse_write_lenient(data: dict[str, Any]) -> WriteInput:
    """Parse Write input leniently."""
    return WriteInput(
        file_path=data.get("file_path", ""),
        content=data.get("content", ""),
    )


def _parse_edit_lenient(data: dict[str, Any]) -> EditInput:
    """Parse Edit input leniently."""
    return EditInput(
        file_path=data.get("file_path", ""),
        old_string=data.get("old_string", ""),
        new_string=data.get("new_string", ""),
        replace_all=data.get("replace_all"),
    )


def _parse_multiedit_lenient(data: dict[str, Any]) -> MultiEditInput:
    """Parse Multiedit input leniently."""
    edits_raw = data.get("edits", [])
    valid_edits: list[EditItem] = []
    for edit in edits_raw:
        if isinstance(edit, dict):
            try:
                valid_edits.append(EditItem.model_validate(edit))
            except Exception:
                pass
    return MultiEditInput(file_path=data.get("file_path", ""), edits=valid_edits)


def _parse_task_lenient(data: dict[str, Any]) -> TaskInput:
    """Parse Task input leniently."""
    return TaskInput(
        prompt=data.get("prompt", ""),
        subagent_type=data.get("subagent_type", ""),
        description=data.get("description", ""),
        model=data.get("model"),
        run_in_background=data.get("run_in_background"),
        resume=data.get("resume"),
    )


def _parse_read_lenient(data: dict[str, Any]) -> ReadInput:
    """Parse Read input leniently."""
    return ReadInput(
        file_path=data.get("file_path", ""),
        offset=data.get("offset"),
        limit=data.get("limit"),
    )


def _parse_askuserquestion_lenient(data: dict[str, Any]) -> AskUserQuestionInput:
    """Parse AskUserQuestion input leniently, handling malformed data."""
    questions_raw = data.get("questions", [])
    valid_questions: list[AskUserQuestionItem] = []
    for q in questions_raw:
        if isinstance(q, dict):
            q_dict = cast(dict[str, Any], q)
            try:
                # Parse options leniently
                options_raw = q_dict.get("options", [])
                valid_options: list[AskUserQuestionOption] = []
                for opt in options_raw:
                    if isinstance(opt, dict):
                        try:
                            valid_options.append(
                                AskUserQuestionOption.model_validate(opt)
                            )
                        except Exception:
                            pass
                valid_questions.append(
                    AskUserQuestionItem(
                        question=str(q_dict.get("question", "")),
                        header=q_dict.get("header"),
                        options=valid_options,
                        multiSelect=bool(q_dict.get("multiSelect", False)),
                    )
                )
            except Exception:
                pass
    return AskUserQuestionInput(
        questions=valid_questions,
        question=data.get("question"),
    )


def _parse_exitplanmode_lenient(data: dict[str, Any]) -> ExitPlanModeInput:
    """Parse ExitPlanMode input leniently."""
    return ExitPlanModeInput(
        plan=data.get("plan", ""),
        launchSwarm=data.get("launchSwarm"),
        teammateCount=data.get("teammateCount"),
    )


# Mapping of tool names to their lenient parsers
TOOL_LENIENT_PARSERS: dict[str, Any] = {
    "Bash": _parse_bash_lenient,
    "Write": _parse_write_lenient,
    "Edit": _parse_edit_lenient,
    "MultiEdit": _parse_multiedit_lenient,
    "Task": _parse_task_lenient,
    "TodoWrite": _parse_todowrite_lenient,
    "Read": _parse_read_lenient,
    "AskUserQuestion": _parse_askuserquestion_lenient,
    "ask_user_question": _parse_askuserquestion_lenient,  # Legacy tool name
    "ExitPlanMode": _parse_exitplanmode_lenient,
}


def parse_tool_input(tool_name: str, input_data: dict[str, Any]) -> ToolInput:
    """Parse tool input dictionary into a typed model.

    Uses strict validation first, then lenient parsing if available.

    Args:
        tool_name: The name of the tool (e.g., "Bash", "Read")
        input_data: The raw input dictionary from the tool_use content

    Returns:
        A typed input model if available, otherwise the original dictionary
    """
    model_class = TOOL_INPUT_MODELS.get(tool_name)
    if model_class is not None:
        try:
            return cast(ToolInput, model_class.model_validate(input_data))
        except Exception:
            # Try lenient parsing if available
            lenient_parser = TOOL_LENIENT_PARSERS.get(tool_name)
            if lenient_parser is not None:
                return cast(ToolInput, lenient_parser(input_data))
            return input_data
    return input_data


# =============================================================================
# Usage Info Normalization
# =============================================================================


def normalize_usage_info(usage_data: Any) -> Optional[UsageInfo]:
    """Normalize usage data from various formats to UsageInfo."""
    if usage_data is None:
        return None

    # If it's already a UsageInfo instance, return as-is
    if isinstance(usage_data, UsageInfo):
        return usage_data

    # If it's a dict, validate and convert
    if isinstance(usage_data, dict):
        return UsageInfo.model_validate(usage_data)

    # Handle object-like access (e.g., from SDK types)
    if hasattr(usage_data, "input_tokens"):
        server_tool_use = getattr(usage_data, "server_tool_use", None)
        if server_tool_use is not None and hasattr(server_tool_use, "model_dump"):
            server_tool_use = server_tool_use.model_dump()
        return UsageInfo(
            input_tokens=getattr(usage_data, "input_tokens", None),
            output_tokens=getattr(usage_data, "output_tokens", None),
            cache_creation_input_tokens=getattr(
                usage_data, "cache_creation_input_tokens", None
            ),
            cache_read_input_tokens=getattr(
                usage_data, "cache_read_input_tokens", None
            ),
            service_tier=getattr(usage_data, "service_tier", None),
            server_tool_use=server_tool_use,
        )

    return None


# =============================================================================
# Content Item Parsing
# =============================================================================
# Functions to parse content items from JSONL data. Organized by entry type
# to clarify which content types can appear in which context.


def _parse_text_content(item_data: dict[str, Any]) -> ContentItem:
    """Parse text content.

    Common to both user and assistant messages.
    """
    return TextContent.model_validate(item_data)


def parse_user_content_item(item_data: dict[str, Any]) -> ContentItem:
    """Parse a content item from a UserTranscriptEntry.

    User messages can contain:
    - text: User-typed text
    - tool_result: Results from tool execution
    - image: User-attached images
    """
    try:
        content_type = item_data.get("type", "")

        if content_type == "text":
            return _parse_text_content(item_data)
        elif content_type == "tool_result":
            return ToolResultContent.model_validate(item_data)
        elif content_type == "image":
            return ImageContent.model_validate(item_data)
        else:
            # Fallback to text content for unknown types
            return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def parse_assistant_content_item(item_data: dict[str, Any]) -> ContentItem:
    """Parse a content item from an AssistantTranscriptEntry.

    Assistant messages can contain:
    - text: Assistant's response text
    - tool_use: Tool invocations
    - thinking: Extended thinking blocks
    """
    try:
        content_type = item_data.get("type", "")

        if content_type == "text":
            return _parse_text_content(item_data)
        elif content_type == "tool_use":
            return ToolUseContent.model_validate(item_data)
        elif content_type == "thinking":
            return ThinkingContent.model_validate(item_data)
        else:
            # Fallback to text content for unknown types
            return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def parse_content_item(item_data: dict[str, Any]) -> ContentItem:
    """Parse a content item (generic fallback).

    For cases where the entry type is unknown. Handles all content types.
    Prefer parse_user_content_item or parse_assistant_content_item when
    the entry type is known.
    """
    try:
        content_type = item_data.get("type", "")

        if content_type == "tool_result":
            return ToolResultContent.model_validate(item_data)
        elif content_type == "image":
            return ImageContent.model_validate(item_data)
        elif content_type == "tool_use":
            return ToolUseContent.model_validate(item_data)
        elif content_type == "thinking":
            return ThinkingContent.model_validate(item_data)
        elif content_type == "text":
            return _parse_text_content(item_data)
        else:
            # Fallback to text content for unknown types
            return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def parse_message_content(
    content_data: Any,
    item_parser: Callable[[dict[str, Any]], ContentItem] = parse_content_item,
) -> list[ContentItem]:
    """Parse message content, normalizing to a list of ContentItems.

    Always returns a list for consistent downstream handling. String content
    is wrapped in a TextContent item.

    Args:
        content_data: Raw content data (string or list of items)
        item_parser: Function to parse individual content items. Defaults to
            generic parse_content_item, but can be parse_user_content_item or
            parse_assistant_content_item for type-specific parsing.
    """
    if isinstance(content_data, str):
        return [TextContent(type="text", text=content_data)]
    elif isinstance(content_data, list):
        content_list = cast(list[Any], content_data)
        result: list[ContentItem] = []
        for item in content_list:
            if isinstance(item, dict):
                result.append(item_parser(cast(dict[str, Any], item)))
            else:
                # Non-dict items (e.g., raw strings) become TextContent
                result.append(TextContent(type="text", text=str(item)))
        return result
    else:
        return [TextContent(type="text", text=str(content_data))]


# =============================================================================
# Transcript Entry Parsing
# =============================================================================


def parse_transcript_entry(data: dict[str, Any]) -> TranscriptEntry:
    """
    Parse a JSON dictionary into the appropriate TranscriptEntry type.

    Enhanced to optionally use official Anthropic types for assistant messages.

    Args:
        data: Dictionary parsed from JSON

    Returns:
        The appropriate TranscriptEntry subclass

    Raises:
        ValueError: If the data doesn't match any known transcript entry type
    """
    entry_type = data.get("type")

    if entry_type == "user":
        # Parse message content if present, using user-specific parser
        data_copy = data.copy()
        if "message" in data_copy and "content" in data_copy["message"]:
            data_copy["message"] = data_copy["message"].copy()
            data_copy["message"]["content"] = parse_message_content(
                data_copy["message"]["content"],
                item_parser=parse_user_content_item,
            )
        # Parse toolUseResult if present and it's a list of content items
        if "toolUseResult" in data_copy and isinstance(
            data_copy["toolUseResult"], list
        ):
            # Check if it's a list of content items (MCP tool results)
            tool_use_result = cast(list[Any], data_copy["toolUseResult"])
            if (
                tool_use_result
                and isinstance(tool_use_result[0], dict)
                and "type" in tool_use_result[0]
            ):
                data_copy["toolUseResult"] = [
                    parse_content_item(cast(dict[str, Any], item))
                    for item in tool_use_result
                    if isinstance(item, dict)
                ]
        return UserTranscriptEntry.model_validate(data_copy)

    elif entry_type == "assistant":
        data_copy = data.copy()

        # Parse assistant message content
        if "message" in data_copy and "content" in data_copy["message"]:
            message_copy = data_copy["message"].copy()
            message_copy["content"] = parse_message_content(
                message_copy["content"],
                item_parser=parse_assistant_content_item,
            )

            # Normalize usage data to support both Anthropic and custom formats
            if "usage" in message_copy:
                message_copy["usage"] = normalize_usage_info(message_copy["usage"])

            data_copy["message"] = message_copy
        return AssistantTranscriptEntry.model_validate(data_copy)

    elif entry_type == "summary":
        return SummaryTranscriptEntry.model_validate(data)

    elif entry_type == "system":
        return SystemTranscriptEntry.model_validate(data)

    elif entry_type == "queue-operation":
        # Parse content if present (in enqueue and remove operations)
        data_copy = data.copy()
        if "content" in data_copy and isinstance(data_copy["content"], list):
            data_copy["content"] = parse_message_content(data_copy["content"])
        return QueueOperationTranscriptEntry.model_validate(data_copy)

    else:
        raise ValueError(f"Unknown transcript entry type: {entry_type}")
