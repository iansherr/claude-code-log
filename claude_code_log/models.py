"""Pydantic models for Claude Code transcript JSON structures.

Enhanced to leverage official Anthropic types where beneficial.
"""

from enum import Enum
from typing import Any, List, Union, Optional, Dict, Literal, cast, TypeGuard

from anthropic.types import Message as AnthropicMessage
from anthropic.types import StopReason
from anthropic.types import Usage as AnthropicUsage
from anthropic.types.content_block import ContentBlock
from pydantic import BaseModel


class MessageType(str, Enum):
    """Primary message type classification.

    This enum covers both JSONL entry types and rendering types.
    Using str as base class maintains backward compatibility with string comparisons.

    JSONL Entry Types (from transcript files):
    - USER, ASSISTANT, SYSTEM, SUMMARY, QUEUE_OPERATION

    Rendering Types (derived during processing):
    - TOOL_USE, TOOL_RESULT, THINKING, IMAGE
    - BASH_INPUT, BASH_OUTPUT
    - SESSION_HEADER, UNKNOWN
    """

    # JSONL entry types
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    SUMMARY = "summary"
    QUEUE_OPERATION = "queue-operation"

    # Rendering/display types (derived from content)
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    IMAGE = "image"
    BASH_INPUT = "bash-input"
    BASH_OUTPUT = "bash-output"
    SESSION_HEADER = "session-header"
    UNKNOWN = "unknown"

    # System subtypes (for css_class)
    SYSTEM_INFO = "system-info"
    SYSTEM_WARNING = "system-warning"
    SYSTEM_ERROR = "system-error"


class TodoItem(BaseModel):
    id: str
    content: str
    status: Literal["pending", "in_progress", "completed"]
    priority: Literal["high", "medium", "low"]


# =============================================================================
# Tool Input Models
# =============================================================================
# Typed models for tool inputs (Phase 11 of MESSAGE_REFACTORING.md)
# These provide type safety and IDE autocompletion for tool parameters.


class BashInput(BaseModel):
    """Input parameters for the Bash tool."""

    command: str
    description: Optional[str] = None
    timeout: Optional[int] = None
    run_in_background: Optional[bool] = None
    dangerouslyDisableSandbox: Optional[bool] = None


class ReadInput(BaseModel):
    """Input parameters for the Read tool."""

    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None


class WriteInput(BaseModel):
    """Input parameters for the Write tool."""

    file_path: str
    content: str


class EditInput(BaseModel):
    """Input parameters for the Edit tool."""

    file_path: str
    old_string: str
    new_string: str
    replace_all: Optional[bool] = None


class EditItem(BaseModel):
    """Single edit item for MultiEdit tool."""

    old_string: str
    new_string: str


class MultiEditInput(BaseModel):
    """Input parameters for the MultiEdit tool."""

    file_path: str
    edits: List[EditItem]


class GlobInput(BaseModel):
    """Input parameters for the Glob tool."""

    pattern: str
    path: Optional[str] = None


class GrepInput(BaseModel):
    """Input parameters for the Grep tool.

    Note: Extra fields like -A, -B, -C are allowed for flexibility.
    """

    pattern: str
    path: Optional[str] = None
    glob: Optional[str] = None
    type: Optional[str] = None
    output_mode: Optional[Literal["content", "files_with_matches", "count"]] = None
    multiline: Optional[bool] = None
    head_limit: Optional[int] = None
    offset: Optional[int] = None

    model_config = {"extra": "allow"}  # Allow -A, -B, -C, -i, -n fields


class TaskInput(BaseModel):
    """Input parameters for the Task tool."""

    prompt: str
    subagent_type: str
    description: str
    model: Optional[Literal["sonnet", "opus", "haiku"]] = None
    run_in_background: Optional[bool] = None
    resume: Optional[str] = None


class TodoWriteItem(BaseModel):
    """Single todo item for TodoWrite tool (input format)."""

    content: str
    status: Literal["pending", "in_progress", "completed"]
    activeForm: str


class TodoWriteInput(BaseModel):
    """Input parameters for the TodoWrite tool."""

    todos: List[TodoWriteItem]


# Union of all typed tool inputs
ToolInput = Union[
    BashInput,
    ReadInput,
    WriteInput,
    EditInput,
    MultiEditInput,
    GlobInput,
    GrepInput,
    TaskInput,
    TodoWriteInput,
    Dict[str, Any],  # Fallback for unknown tools
]

# Mapping of tool names to their typed input models
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
}


def parse_tool_input(tool_name: str, input_data: Dict[str, Any]) -> ToolInput:
    """Parse tool input dictionary into a typed model if available.

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
            # Fall back to raw dict if validation fails
            return input_data
    return input_data


class UsageInfo(BaseModel):
    """Token usage information that extends Anthropic's Usage type to handle optional fields."""

    input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    service_tier: Optional[str] = None
    server_tool_use: Optional[Dict[str, Any]] = None

    def to_anthropic_usage(self) -> Optional[AnthropicUsage]:
        """Convert to Anthropic Usage type if both required fields are present."""
        if self.input_tokens is not None and self.output_tokens is not None:
            return AnthropicUsage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_creation_input_tokens=self.cache_creation_input_tokens,
                cache_read_input_tokens=self.cache_read_input_tokens,
                service_tier=self.service_tier,  # type: ignore
                server_tool_use=self.server_tool_use,  # type: ignore
            )
        return None

    @classmethod
    def from_anthropic_usage(cls, usage: AnthropicUsage) -> "UsageInfo":
        """Create UsageInfo from Anthropic Usage."""
        return cls(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            service_tier=usage.service_tier,
            server_tool_use=usage.server_tool_use.model_dump()
            if usage.server_tool_use
            else None,
        )


class TextContent(BaseModel):
    type: Literal["text"]
    text: str


class ToolUseContent(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: Dict[str, Any]


class ToolResultContent(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: str
    content: Union[str, List[Dict[str, Any]]]
    is_error: Optional[bool] = None
    agentId: Optional[str] = None  # Reference to agent file for sub-agent messages


class ThinkingContent(BaseModel):
    type: Literal["thinking"]
    thinking: str
    signature: Optional[str] = None


class ImageSource(BaseModel):
    type: Literal["base64"]
    media_type: str
    data: str


class ImageContent(BaseModel):
    type: Literal["image"]
    source: ImageSource


# Enhanced ContentItem to include official Anthropic ContentBlock types
ContentItem = Union[
    TextContent,
    ToolUseContent,
    ToolResultContent,
    ThinkingContent,
    ImageContent,
    ContentBlock,  # Official Anthropic content block types
]


class UserMessage(BaseModel):
    role: Literal["user"]
    content: Union[str, List[ContentItem]]


class AssistantMessage(BaseModel):
    """Assistant message model compatible with Anthropic's Message type."""

    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    model: str
    content: List[ContentItem]
    stop_reason: Optional[StopReason] = None
    stop_sequence: Optional[str] = None
    usage: Optional[UsageInfo] = None

    @classmethod
    def from_anthropic_message(
        cls, anthropic_msg: AnthropicMessage
    ) -> "AssistantMessage":
        """Create AssistantMessage from official Anthropic Message."""
        # Convert Anthropic Message to our format, preserving official types where possible
        return cls(
            id=anthropic_msg.id,
            type=anthropic_msg.type,
            role=anthropic_msg.role,
            model=anthropic_msg.model,
            content=list(
                anthropic_msg.content
            ),  # Convert to list for ContentItem compatibility
            stop_reason=anthropic_msg.stop_reason,
            stop_sequence=anthropic_msg.stop_sequence,
            usage=normalize_usage_info(anthropic_msg.usage),
        )


class FileInfo(BaseModel):
    filePath: str
    content: str
    numLines: int
    startLine: int
    totalLines: int


class FileReadResult(BaseModel):
    type: Literal["text"]
    file: FileInfo


class CommandResult(BaseModel):
    stdout: str
    stderr: str
    interrupted: bool
    isImage: bool


class TodoResult(BaseModel):
    oldTodos: List[TodoItem]
    newTodos: List[TodoItem]


class EditResult(BaseModel):
    oldString: Optional[str] = None
    newString: Optional[str] = None
    replaceAll: Optional[bool] = None
    originalFile: Optional[str] = None
    structuredPatch: Optional[Any] = None
    userModified: Optional[bool] = None


ToolUseResult = Union[
    str,
    List[TodoItem],
    FileReadResult,
    CommandResult,
    TodoResult,
    EditResult,
    List[ContentItem],
]


class BaseTranscriptEntry(BaseModel):
    parentUuid: Optional[str]
    isSidechain: bool
    userType: str
    cwd: str
    sessionId: str
    version: str
    uuid: str
    timestamp: str
    isMeta: Optional[bool] = None
    agentId: Optional[str] = None  # Agent ID for sidechain messages


class UserTranscriptEntry(BaseTranscriptEntry):
    type: Literal["user"]
    message: UserMessage
    toolUseResult: Optional[ToolUseResult] = None
    agentId: Optional[str] = None  # From toolUseResult when present


class AssistantTranscriptEntry(BaseTranscriptEntry):
    type: Literal["assistant"]
    message: AssistantMessage
    requestId: Optional[str] = None


class SummaryTranscriptEntry(BaseModel):
    type: Literal["summary"]
    summary: str
    leafUuid: str
    cwd: Optional[str] = None


class SystemTranscriptEntry(BaseTranscriptEntry):
    """System messages like warnings, notifications, hook summaries, etc."""

    type: Literal["system"]
    content: Optional[str] = None
    subtype: Optional[str] = None  # e.g., "stop_hook_summary"
    level: Optional[str] = None  # e.g., "warning", "info", "error"
    # Hook summary fields (for subtype="stop_hook_summary")
    hasOutput: Optional[bool] = None
    hookErrors: Optional[List[str]] = None
    hookInfos: Optional[List[Dict[str, Any]]] = None
    preventedContinuation: Optional[bool] = None


class QueueOperationTranscriptEntry(BaseModel):
    """Queue operations (enqueue/dequeue/remove) for message queueing tracking.

    enqueue/dequeue are internal operations that track when messages are queued and dequeued.
    They are parsed but not rendered, as the content duplicates actual user messages.

    'remove' operations are out-of-band user inputs made visible to the agent while working
    for "steering" purposes. These should be rendered as user messages with a 'steering' CSS class.
    Content can be a list of ContentItems or a simple string (for 'remove' operations).
    """

    type: Literal["queue-operation"]
    operation: Literal["enqueue", "dequeue", "remove", "popAll"]
    timestamp: str
    sessionId: str
    content: Optional[Union[List[ContentItem], str]] = (
        None  # List for enqueue, str for remove/popAll
    )


TranscriptEntry = Union[
    UserTranscriptEntry,
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    QueueOperationTranscriptEntry,
]


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


def parse_content_item(item_data: Dict[str, Any]) -> ContentItem:
    """Parse a content item using enhanced approach with Anthropic types."""
    try:
        content_type = item_data.get("type", "")

        # Try official Anthropic types first for better future compatibility
        if content_type == "text":
            try:
                from anthropic.types.text_block import TextBlock

                return TextBlock.model_validate(item_data)
            except Exception:
                return TextContent.model_validate(item_data)
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
        elif content_type == "tool_result":
            return ToolResultContent.model_validate(item_data)
        elif content_type == "image":
            return ImageContent.model_validate(item_data)
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


# Type guards for TranscriptEntry union narrowing
# These enable type-safe access to entry-specific fields after type checking


def is_user_entry(entry: TranscriptEntry) -> TypeGuard[UserTranscriptEntry]:
    """Check if entry is a user transcript entry."""
    return entry.type == MessageType.USER


def is_assistant_entry(entry: TranscriptEntry) -> TypeGuard[AssistantTranscriptEntry]:
    """Check if entry is an assistant transcript entry."""
    return entry.type == MessageType.ASSISTANT


def is_system_entry(entry: TranscriptEntry) -> TypeGuard[SystemTranscriptEntry]:
    """Check if entry is a system transcript entry."""
    return entry.type == MessageType.SYSTEM


def is_summary_entry(entry: TranscriptEntry) -> TypeGuard[SummaryTranscriptEntry]:
    """Check if entry is a summary transcript entry."""
    return entry.type == MessageType.SUMMARY


def is_queue_operation_entry(
    entry: TranscriptEntry,
) -> TypeGuard[QueueOperationTranscriptEntry]:
    """Check if entry is a queue operation transcript entry."""
    return entry.type == MessageType.QUEUE_OPERATION


# Content item type guards


def is_tool_use_content(item: ContentItem) -> TypeGuard[ToolUseContent]:
    """Check if content item is a tool use."""
    return getattr(item, "type", None) == "tool_use"


def is_tool_result_content(item: ContentItem) -> TypeGuard[ToolResultContent]:
    """Check if content item is a tool result."""
    return getattr(item, "type", None) == "tool_result"


def is_thinking_content(item: ContentItem) -> TypeGuard[ThinkingContent]:
    """Check if content item is thinking content."""
    return getattr(item, "type", None) == "thinking"


def is_image_content(item: ContentItem) -> TypeGuard[ImageContent]:
    """Check if content item is an image."""
    return getattr(item, "type", None) == "image"


def is_text_content(item: ContentItem) -> TypeGuard[TextContent]:
    """Check if content item is text content."""
    return getattr(item, "type", None) == "text"
