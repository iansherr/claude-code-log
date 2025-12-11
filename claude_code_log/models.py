"""Pydantic models for Claude Code transcript JSON structures.

Enhanced to leverage official Anthropic types where beneficial.
"""

from dataclasses import dataclass
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


@dataclass
class MessageModifiers:
    """Semantic modifiers that affect message display.

    These are format-neutral flags that renderers can use to determine
    how to display a message. HTML renderer converts these to CSS classes,
    text renderer might use them for indentation or formatting.

    The modifiers capture traits that were previously encoded in the
    css_class string (e.g., "user sidechain slash-command").
    """

    is_sidechain: bool = False
    is_slash_command: bool = False
    is_command_output: bool = False
    is_compacted: bool = False
    is_error: bool = False
    is_steering: bool = False
    # System message level (mutually exclusive: info, warning, error, hook)
    system_level: Optional[str] = None


# =============================================================================
# Message Content Models
# =============================================================================
# Structured content models for format-neutral message representation.
# These replace the direct HTML generation in renderer.py, allowing different
# renderers (HTML, text, etc.) to format the content appropriately.


@dataclass
class MessageContent:
    """Base class for structured message content.

    Subclasses represent specific content types that renderers can format
    appropriately for their output format.
    """

    pass


@dataclass
class SystemContent(MessageContent):
    """System message with level indicator.

    Used for info, warning, and error system messages.
    """

    level: str  # "info", "warning", "error"
    text: str  # Raw text content (may contain ANSI codes)


@dataclass
class HookInfo:
    """Information about a single hook execution."""

    command: str
    # Could add more fields like exit_code, duration, etc.


@dataclass
class HookSummaryContent(MessageContent):
    """Hook execution summary.

    Used for subtype="stop_hook_summary" system messages.
    """

    has_output: bool
    hook_errors: List[str]  # Error messages from hooks
    hook_infos: List[HookInfo]  # Info about each hook executed


# =============================================================================
# Tool Output Content Models
# =============================================================================
# Structured content models for tool results (symmetric with Tool Input Models).
# These provide format-neutral representation of tool outputs that renderers
# can format appropriately.


@dataclass
class ReadOutput(MessageContent):
    """Parsed Read tool output.

    Represents the result of reading a file with optional line range.
    Symmetric with ReadInput for tool_use → tool_result pairing.
    """

    file_path: str
    content: str  # File content (may be truncated)
    start_line: int  # 1-based starting line number
    num_lines: int  # Number of lines in content
    total_lines: int  # Total lines in file
    is_truncated: bool  # Whether content was truncated
    system_reminder: Optional[str] = None  # Embedded system reminder text


@dataclass
class WriteOutput(MessageContent):
    """Parsed Write tool output.

    Symmetric with WriteInput for tool_use → tool_result pairing.
    """

    file_path: str
    success: bool
    message: str  # Success or error message


@dataclass
class EditDiff:
    """Single diff hunk for edit operations."""

    old_text: str
    new_text: str


@dataclass
class EditOutput(MessageContent):
    """Parsed Edit tool output.

    Contains diff information for file edits.
    Symmetric with EditInput for tool_use → tool_result pairing.
    """

    file_path: str
    success: bool
    diffs: List[EditDiff]  # Changes made
    message: str  # Result message or code snippet
    start_line: int = 1  # Starting line number for code display


@dataclass
class BashOutput(MessageContent):
    """Parsed Bash tool output.

    Symmetric with BashInput for tool_use → tool_result pairing.
    """

    stdout: str
    stderr: str
    exit_code: Optional[int]
    interrupted: bool
    is_image: bool  # True if output contains image data


@dataclass
class TaskOutput(MessageContent):
    """Parsed Task (sub-agent) tool output.

    Symmetric with TaskInput for tool_use → tool_result pairing.
    """

    agent_id: Optional[str]
    result: str  # Agent's response
    is_background: bool


@dataclass
class GlobOutput(MessageContent):
    """Parsed Glob tool output.

    Symmetric with GlobInput for tool_use → tool_result pairing.
    """

    pattern: str
    files: List[str]  # Matching file paths
    truncated: bool  # Whether list was truncated


@dataclass
class GrepOutput(MessageContent):
    """Parsed Grep tool output.

    Symmetric with GrepInput for tool_use → tool_result pairing.
    """

    pattern: str
    matches: List[str]  # Matching lines/files
    output_mode: str  # "content", "files_with_matches", or "count"
    truncated: bool


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
    """Single todo item for TodoWrite tool (input format).

    All fields have defaults for lenient parsing of legacy/malformed data.
    """

    content: str = ""
    status: str = "pending"  # Allow any string, not just Literal, for flexibility
    activeForm: str = ""
    id: Optional[str] = None
    priority: Optional[str] = None  # Allow any string for flexibility


class TodoWriteInput(BaseModel):
    """Input parameters for the TodoWrite tool."""

    todos: List[TodoWriteItem]


class AskUserQuestionOption(BaseModel):
    """Option for an AskUserQuestion question.

    All fields have defaults for lenient parsing.
    """

    label: str = ""
    description: Optional[str] = None


class AskUserQuestionItem(BaseModel):
    """Single question in AskUserQuestion input.

    All fields have defaults for lenient parsing.
    """

    question: str = ""
    header: Optional[str] = None
    options: List[AskUserQuestionOption] = []
    multiSelect: bool = False


class AskUserQuestionInput(BaseModel):
    """Input parameters for the AskUserQuestion tool.

    Supports both modern format (questions list) and legacy format (single question).
    """

    questions: List[AskUserQuestionItem] = []
    question: Optional[str] = None  # Legacy single question format


class ExitPlanModeInput(BaseModel):
    """Input parameters for the ExitPlanMode tool."""

    plan: str = ""
    launchSwarm: Optional[bool] = None
    teammateCount: Optional[int] = None


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
    AskUserQuestionInput,
    ExitPlanModeInput,
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
    _parsed_input: Optional["ToolInput"] = None  # Cached parsed input

    @property
    def parsed_input(self) -> "ToolInput":
        """Get typed input model if available, otherwise return raw dict.

        Lazily parses the input dict into a typed model.
        Uses strict validation first, then lenient parsing if available.
        Result is cached for subsequent accesses.
        """
        if self._parsed_input is None:
            object.__setattr__(
                self, "_parsed_input", parse_tool_input(self.name, self.input)
            )
        return self._parsed_input  # type: ignore[return-value]


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
    usage: Optional["UsageInfo"] = None  # For type compatibility with AssistantMessage


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


# Tool result type - flexible to accept various result formats from JSONL
# The specific parsing/formatting happens in tool_formatters.py using
# ReadOutput, EditOutput, etc. (see Tool Output Content Models section)
ToolUseResult = Union[
    str,
    List[Any],  # Covers List[TodoItem], List[ContentItem], etc.
    Dict[str, Any],  # Covers structured results
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
    sessionId: None = None  # Summaries don't have a sessionId


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
        from anthropic.types.text_block import TextBlock

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
