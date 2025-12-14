"""Pydantic models for Claude Code transcript JSON structures.

Enhanced to leverage official Anthropic types where beneficial.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Union, Optional, Dict, Literal

from anthropic.types import Message as AnthropicMessage
from anthropic.types import StopReason
from anthropic.types import Usage as AnthropicUsage
from anthropic.types.content_block import ContentBlock
from pydantic import BaseModel, PrivateAttr


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


class MessageContent:
    """Base class for structured message content.

    Subclasses represent specific content types that renderers can format
    appropriately for their output format.

    Note: This is a plain class (not dataclass) to allow Pydantic BaseModel
    subclasses like ToolUseContent and ImageContent to inherit from it.
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
# User Message Content Models
# =============================================================================
# Structured content models for user message variants.
# These classify user text based on flags and tag patterns.


@dataclass
class SlashCommandContent(MessageContent):
    """Content for slash command invocations (e.g., /context, /model).

    These are user messages containing command-name, command-args, and
    command-contents tags parsed from the text.
    """

    command_name: str
    command_args: str
    command_contents: str


@dataclass
class CommandOutputContent(MessageContent):
    """Content for local command output (e.g., output from /context).

    These are user messages containing local-command-stdout tags.
    """

    stdout: str
    is_markdown: bool  # True if content appears to be markdown


@dataclass
class BashInputContent(MessageContent):
    """Content for inline bash commands in user messages.

    These are user messages containing bash-input tags.
    """

    command: str


@dataclass
class BashOutputContent(MessageContent):
    """Content for bash command output.

    These are user messages containing bash-stdout and/or bash-stderr tags.
    """

    stdout: Optional[str] = None  # Raw stdout content (may contain ANSI codes)
    stderr: Optional[str] = None  # Raw stderr content (may contain ANSI codes)


@dataclass
class ToolResultContentModel(MessageContent):
    """Content model for tool results with rendering context.

    Wraps ToolResultContent with additional context needed for rendering,
    such as the associated tool name and file path.
    """

    tool_use_id: str
    content: Any  # Union[str, List[Dict[str, Any]]]
    is_error: bool = False
    tool_name: Optional[str] = None  # Name of the tool that produced this result
    file_path: Optional[str] = None  # File path for Read/Edit/Write tools


@dataclass
class CompactedSummaryContent(MessageContent):
    """Content for compacted session summaries.

    These are user messages that contain previous conversation context
    in a compacted format when sessions run out of context.
    Parsed by parse_compacted_summary() in parser.py, formatted by
    format_compacted_summary_content() in html/user_formatters.py.
    """

    summary_text: str


@dataclass
class UserMemoryContent(MessageContent):
    """Content for user memory input.

    These are user messages containing user-memory-input tags.
    Parsed by parse_user_memory() in parser.py, formatted by
    format_user_memory_content() in html/user_formatters.py.
    """

    memory_text: str


@dataclass
class IdeOpenedFile:
    """IDE notification for an opened file."""

    content: str  # Raw content from the tag


@dataclass
class IdeSelection:
    """IDE notification for a code selection."""

    content: str  # Raw selection content


@dataclass
class IdeDiagnostic:
    """IDE diagnostic notification.

    Contains either parsed JSON diagnostics or raw content if parsing failed.
    """

    diagnostics: Optional[List[Dict[str, Any]]] = None  # Parsed diagnostic objects
    raw_content: Optional[str] = None  # Fallback if JSON parsing failed


@dataclass
class IdeNotificationContent(MessageContent):
    """Content for IDE notification tags.

    These are user messages containing IDE notification tags like:
    - <ide_opened_file>: File open notifications
    - <ide_selection>: Code selection notifications
    - <post-tool-use-hook><ide_diagnostics>: Diagnostic JSON arrays

    Format-neutral: stores structured data, not HTML.
    """

    opened_files: List[IdeOpenedFile]
    selections: List[IdeSelection]
    diagnostics: List[IdeDiagnostic]
    remaining_text: str  # Text after notifications extracted


@dataclass
class UserTextContent(MessageContent):
    """Content for plain user text with optional IDE notifications.

    Wraps user text that may have been preprocessed to extract
    IDE notifications, compacted summaries, or memory input markers.

    TODO: Not currently instantiated - formatter exists but pipeline uses
    separate IdeNotificationContent and plain text instead.
    """

    text: str
    ide_notifications: Optional[IdeNotificationContent] = None
    is_compacted: bool = False
    is_memory_input: bool = False


# =============================================================================
# Assistant Message Content Models
# =============================================================================
# Structured content models for assistant message variants.
# These classify assistant message parts for format-neutral rendering.


@dataclass
class AssistantTextContent(MessageContent):
    """Content for assistant text messages.

    These are the text portions of assistant messages that get
    rendered as markdown with syntax highlighting.
    """

    text: str


@dataclass
class ThinkingContentModel(MessageContent):
    """Content for assistant thinking/reasoning blocks.

    These are the <thinking> blocks that show the assistant's
    internal reasoning process.

    Note: This is distinct from ThinkingContent (the Pydantic model
    for parsing JSONL). This dataclass is for rendering purposes.
    """

    thinking: str
    signature: Optional[str] = None


@dataclass
class UnknownContent(MessageContent):
    """Content for unknown/unrecognized content types.

    Used as a fallback when encountering content types that don't have
    specific handlers. Stores the type name for display purposes.
    """

    type_name: str  # The name/description of the unknown type


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

    TODO: Not currently used - tool results handled as raw strings.
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

    TODO: Not currently used - tool results handled as raw strings.
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

    TODO: Not currently used - tool results handled as raw strings.
    """

    agent_id: Optional[str]
    result: str  # Agent's response
    is_background: bool


@dataclass
class GlobOutput(MessageContent):
    """Parsed Glob tool output.

    Symmetric with GlobInput for tool_use → tool_result pairing.

    TODO: Not currently used - tool results handled as raw strings.
    """

    pattern: str
    files: List[str]  # Matching file paths
    truncated: bool  # Whether list was truncated


@dataclass
class GrepOutput(MessageContent):
    """Parsed Grep tool output.

    Symmetric with GrepInput for tool_use → tool_result pairing.

    TODO: Not currently used - tool results handled as raw strings.
    """

    pattern: str
    matches: List[str]  # Matching lines/files
    output_mode: str  # "content", "files_with_matches", or "count"
    truncated: bool


# =============================================================================
# Renderer Content Models
# =============================================================================
# Structured content models for renderer-specific elements.
# These are used by the HTML renderer but represent format-neutral data.


@dataclass
class SessionHeaderContent(MessageContent):
    """Content for session headers in transcript rendering.

    Represents the header displayed at the start of each session
    with session title and optional summary.
    """

    title: str
    session_id: str
    summary: Optional[str] = None


@dataclass
class DedupNoticeContent(MessageContent):
    """Content for deduplication notices.

    Displayed when content is deduplicated (e.g., sidechain assistant
    text that duplicates the Task tool result).
    """

    notice_text: str


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


class ToolUseContent(BaseModel, MessageContent):
    type: Literal["tool_use"]
    id: str
    name: str
    input: Dict[str, Any]
    _parsed_input: Optional["ToolInput"] = PrivateAttr(
        default=None
    )  # Cached parsed input

    @property
    def parsed_input(self) -> "ToolInput":
        """Get typed input model if available, otherwise return raw dict.

        Lazily parses the input dict into a typed model.
        Uses strict validation first, then lenient parsing if available.
        Result is cached for subsequent accesses.
        """
        if self._parsed_input is None:
            from .parser import parse_tool_input

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


class ImageContent(BaseModel, MessageContent):
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
        from .parser import normalize_usage_info

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
    List[Any],  # Covers List[TodoWriteItem], List[ContentItem], etc.
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
