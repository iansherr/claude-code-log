#!/usr/bin/env python3
"""Parse and extract data from Claude transcript JSONL files."""

import json
import re
from typing import Any, Dict, List, Optional, Union, cast, TypeGuard
from datetime import datetime

from anthropic.types import Message as AnthropicMessage
from anthropic.types import Usage as AnthropicUsage
from anthropic.types.text_block import TextBlock
from anthropic.types.thinking_block import ThinkingBlock
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
    SlashCommandContent,
    CommandOutputContent,
    BashInputContent,
    # Tool input models
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


def extract_text_content(content: Union[str, List[ContentItem], None]) -> str:
    """Extract text content from Claude message content structure.

    Supports both custom models (TextContent, ThinkingContent) and official
    Anthropic SDK types (TextBlock, ThinkingBlock).
    """
    if content is None:
        return ""
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            # Handle text content (custom TextContent or Anthropic TextBlock)
            if isinstance(item, (TextContent, TextBlock)):
                text_parts.append(item.text)
            # Skip thinking content (custom ThinkingContent or Anthropic ThinkingBlock)
            elif isinstance(item, (ThinkingContent, ThinkingBlock)):
                continue
        return "\n".join(text_parts)
    else:
        return str(content) if content else ""


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse ISO timestamp to datetime object."""
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# =============================================================================
# User Message Content Parsing
# =============================================================================


def parse_slash_command(text: str) -> Optional[SlashCommandContent]:
    """Parse slash command tags from text.

    Args:
        text: Raw text that may contain command-name, command-args, command-contents tags

    Returns:
        SlashCommandContent if tags found, None otherwise
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
                text_dict = cast(Dict[str, Any], contents_json)
                text_value = text_dict["text"]
                command_contents = str(text_value)
            else:
                command_contents = contents_text
        except json.JSONDecodeError:
            command_contents = contents_text

    return SlashCommandContent(
        command_name=command_name,
        command_args=command_args,
        command_contents=command_contents,
    )


def parse_command_output(text: str) -> Optional[CommandOutputContent]:
    """Parse command output tags from text.

    Args:
        text: Raw text that may contain local-command-stdout tags

    Returns:
        CommandOutputContent if tags found, None otherwise
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

    return CommandOutputContent(stdout=stdout_content, is_markdown=is_markdown)


def parse_bash_input(text: str) -> Optional[BashInputContent]:
    """Parse bash input tags from text.

    Args:
        text: Raw text that may contain bash-input tags

    Returns:
        BashInputContent if tags found, None otherwise
    """
    bash_match = re.search(r"<bash-input>(.*?)</bash-input>", text, re.DOTALL)
    if not bash_match:
        return None

    return BashInputContent(command=bash_match.group(1).strip())


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


def is_warmup_only_session(messages: List[TranscriptEntry], session_id: str) -> bool:
    """Check if a session contains only warmup user messages.

    A warmup session is one where ALL user messages are literally just "Warmup".
    Sessions with no user messages return False (not considered warmup).

    Args:
        messages: List of all transcript entries
        session_id: The session ID to check

    Returns:
        True if ALL user messages in the session are "Warmup", False otherwise
    """
    user_messages_in_session: List[str] = []

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


def is_user_entry(entry: TranscriptEntry) -> TypeGuard[UserTranscriptEntry]:
    """Check if entry is a user transcript entry."""
    return entry.type == MessageType.USER


def is_assistant_entry(entry: TranscriptEntry) -> TypeGuard[AssistantTranscriptEntry]:
    """Check if entry is an assistant transcript entry."""
    return entry.type == MessageType.ASSISTANT


# =============================================================================
# Tool Input Parsing
# =============================================================================

TOOL_INPUT_MODELS: Dict[str, type[BaseModel]] = {
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


def _parse_todowrite_lenient(data: Dict[str, Any]) -> TodoWriteInput:
    """Parse TodoWrite input leniently, handling malformed data."""
    todos_raw = data.get("todos", [])
    valid_todos: List[TodoWriteItem] = []
    for item in todos_raw:
        if isinstance(item, dict):
            try:
                valid_todos.append(TodoWriteItem.model_validate(item))
            except Exception:
                pass
        elif isinstance(item, str):
            valid_todos.append(TodoWriteItem(content=item))
    return TodoWriteInput(todos=valid_todos)


def _parse_bash_lenient(data: Dict[str, Any]) -> BashInput:
    """Parse Bash input leniently."""
    return BashInput(
        command=data.get("command", ""),
        description=data.get("description"),
        timeout=data.get("timeout"),
        run_in_background=data.get("run_in_background"),
    )


def _parse_write_lenient(data: Dict[str, Any]) -> WriteInput:
    """Parse Write input leniently."""
    return WriteInput(
        file_path=data.get("file_path", ""),
        content=data.get("content", ""),
    )


def _parse_edit_lenient(data: Dict[str, Any]) -> EditInput:
    """Parse Edit input leniently."""
    return EditInput(
        file_path=data.get("file_path", ""),
        old_string=data.get("old_string", ""),
        new_string=data.get("new_string", ""),
        replace_all=data.get("replace_all"),
    )


def _parse_multiedit_lenient(data: Dict[str, Any]) -> MultiEditInput:
    """Parse Multiedit input leniently."""
    edits_raw = data.get("edits", [])
    valid_edits: List[EditItem] = []
    for edit in edits_raw:
        if isinstance(edit, dict):
            try:
                valid_edits.append(EditItem.model_validate(edit))
            except Exception:
                pass
    return MultiEditInput(file_path=data.get("file_path", ""), edits=valid_edits)


def _parse_task_lenient(data: Dict[str, Any]) -> TaskInput:
    """Parse Task input leniently."""
    return TaskInput(
        prompt=data.get("prompt", ""),
        subagent_type=data.get("subagent_type", ""),
        description=data.get("description", ""),
        model=data.get("model"),
        run_in_background=data.get("run_in_background"),
        resume=data.get("resume"),
    )


def _parse_read_lenient(data: Dict[str, Any]) -> ReadInput:
    """Parse Read input leniently."""
    return ReadInput(
        file_path=data.get("file_path", ""),
        offset=data.get("offset"),
        limit=data.get("limit"),
    )


def _parse_askuserquestion_lenient(data: Dict[str, Any]) -> AskUserQuestionInput:
    """Parse AskUserQuestion input leniently, handling malformed data."""
    questions_raw = data.get("questions", [])
    valid_questions: List[AskUserQuestionItem] = []
    for q in questions_raw:
        if isinstance(q, dict):
            q_dict = cast(Dict[str, Any], q)
            try:
                # Parse options leniently
                options_raw = q_dict.get("options", [])
                valid_options: List[AskUserQuestionOption] = []
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


def _parse_exitplanmode_lenient(data: Dict[str, Any]) -> ExitPlanModeInput:
    """Parse ExitPlanMode input leniently."""
    return ExitPlanModeInput(
        plan=data.get("plan", ""),
        launchSwarm=data.get("launchSwarm"),
        teammateCount=data.get("teammateCount"),
    )


# Mapping of tool names to their lenient parsers
TOOL_LENIENT_PARSERS: Dict[str, Any] = {
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


def parse_tool_input(tool_name: str, input_data: Dict[str, Any]) -> ToolInput:
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
    """Normalize usage data to be compatible with both custom and Anthropic formats."""
    if usage_data is None:
        return None

    # If it's already a UsageInfo instance, return as-is
    if isinstance(usage_data, UsageInfo):
        return usage_data

    # If it's an Anthropic Usage instance, convert using our method
    if isinstance(usage_data, AnthropicUsage):
        return UsageInfo.from_anthropic_usage(usage_data)

    # If it has the shape of an Anthropic Usage, try to construct it first
    if hasattr(usage_data, "input_tokens") and hasattr(usage_data, "output_tokens"):
        try:
            # Try to create an Anthropic Usage first
            anthropic_usage = AnthropicUsage.model_validate(usage_data)
            return UsageInfo.from_anthropic_usage(anthropic_usage)
        except Exception:
            # Fall back to direct conversion
            return UsageInfo(
                input_tokens=getattr(usage_data, "input_tokens", None),
                cache_creation_input_tokens=getattr(
                    usage_data, "cache_creation_input_tokens", None
                ),
                cache_read_input_tokens=getattr(
                    usage_data, "cache_read_input_tokens", None
                ),
                output_tokens=getattr(usage_data, "output_tokens", None),
                service_tier=getattr(usage_data, "service_tier", None),
                server_tool_use=getattr(usage_data, "server_tool_use", None),
            )

    # If it's a dict, validate and convert to our format
    if isinstance(usage_data, dict):
        return UsageInfo.model_validate(usage_data)

    return None


# =============================================================================
# Content Item Parsing
# =============================================================================
# Functions to parse content items from JSONL data. Organized by entry type
# to clarify which content types can appear in which context.


def _parse_text_content(item_data: Dict[str, Any]) -> ContentItem:
    """Parse text content, trying Anthropic types first.

    Common to both user and assistant messages.
    """
    try:
        return TextBlock.model_validate(item_data)
    except Exception:
        return TextContent.model_validate(item_data)


def parse_user_content_item(item_data: Dict[str, Any]) -> ContentItem:
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


def parse_assistant_content_item(item_data: Dict[str, Any]) -> ContentItem:
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
            try:
                from anthropic.types.tool_use_block import ToolUseBlock

                return ToolUseBlock.model_validate(item_data)
            except Exception:
                return ToolUseContent.model_validate(item_data)
        elif content_type == "thinking":
            try:
                from anthropic.types.thinking_block import ThinkingBlock

                return ThinkingBlock.model_validate(item_data)
            except Exception:
                return ThinkingContent.model_validate(item_data)
        else:
            # Fallback to text content for unknown types
            return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def parse_content_item(item_data: Dict[str, Any]) -> ContentItem:
    """Parse a content item (generic fallback).

    For cases where the entry type is unknown. Handles all content types.
    Prefer parse_user_content_item or parse_assistant_content_item when
    the entry type is known.
    """
    try:
        content_type = item_data.get("type", "")

        # User-specific content types
        if content_type == "tool_result":
            return ToolResultContent.model_validate(item_data)
        elif content_type == "image":
            return ImageContent.model_validate(item_data)

        # Assistant-specific content types
        elif content_type == "tool_use":
            try:
                from anthropic.types.tool_use_block import ToolUseBlock

                return ToolUseBlock.model_validate(item_data)
            except Exception:
                return ToolUseContent.model_validate(item_data)
        elif content_type == "thinking":
            try:
                from anthropic.types.thinking_block import ThinkingBlock

                return ThinkingBlock.model_validate(item_data)
            except Exception:
                return ThinkingContent.model_validate(item_data)

        # Common content types
        elif content_type == "text":
            return _parse_text_content(item_data)
        else:
            # Fallback to text content for unknown types
            return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def parse_message_content(content_data: Any) -> Union[str, List[ContentItem]]:
    """Parse message content, handling both string and list formats."""
    if isinstance(content_data, str):
        return content_data
    elif isinstance(content_data, list):
        content_list = cast(List[Dict[str, Any]], content_data)
        return [parse_content_item(item) for item in content_list]
    else:
        return str(content_data)


# =============================================================================
# Transcript Entry Parsing
# =============================================================================


def parse_transcript_entry(data: Dict[str, Any]) -> TranscriptEntry:
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
        # Parse message content if present
        data_copy = data.copy()
        if "message" in data_copy and "content" in data_copy["message"]:
            data_copy["message"] = data_copy["message"].copy()
            data_copy["message"]["content"] = parse_message_content(
                data_copy["message"]["content"]
            )
        # Parse toolUseResult if present and it's a list of content items
        if "toolUseResult" in data_copy and isinstance(
            data_copy["toolUseResult"], list
        ):
            # Check if it's a list of content items (MCP tool results)
            tool_use_result = cast(List[Any], data_copy["toolUseResult"])
            if (
                tool_use_result
                and isinstance(tool_use_result[0], dict)
                and "type" in tool_use_result[0]
            ):
                data_copy["toolUseResult"] = [
                    parse_content_item(cast(Dict[str, Any], item))
                    for item in tool_use_result
                    if isinstance(item, dict)
                ]
        return UserTranscriptEntry.model_validate(data_copy)

    elif entry_type == "assistant":
        # Enhanced assistant message parsing with optional Anthropic types
        data_copy = data.copy()

        # Validate compatibility with official Anthropic Message type
        if "message" in data_copy:
            try:
                message_data = data_copy["message"]
                AnthropicMessage.model_validate(message_data)
                # Successfully validated - our data is compatible with official Anthropic types
            except Exception:
                # Validation failed - continue with standard parsing
                pass

        # Standard parsing path (works for all cases)
        if "message" in data_copy and "content" in data_copy["message"]:
            message_copy = data_copy["message"].copy()
            message_copy["content"] = parse_message_content(message_copy["content"])

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
