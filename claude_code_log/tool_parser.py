"""Parser for tool use and tool result content.

This module handles parsing of tool-related content into MessageContent subclasses:
- ToolUseMessage: Tool invocations with typed inputs (BashInput, ReadInput, etc.)
- ToolResultMessage: Tool results with output and context

Also provides parsing of tool inputs into typed models:
- parse_tool_input(): Parse raw tool input dict into typed model
- parse_tool_use_item(): Process ToolUseContent into ToolUseMessage
- parse_tool_result_item(): Process ToolResultContent into ToolResultMessage
"""

from dataclasses import dataclass
from typing import Any, Optional, cast

from pydantic import BaseModel

from .models import (
    # Tool input models
    AskUserQuestionInput,
    AskUserQuestionItem,
    AskUserQuestionOption,
    BashInput,
    ContentItem,
    EditInput,
    EditItem,
    ExitPlanModeInput,
    GlobInput,
    GrepInput,
    MessageContent,
    MultiEditInput,
    ReadInput,
    TaskInput,
    TodoWriteInput,
    TodoWriteItem,
    ToolInput,
    ToolResultContent,
    ToolResultMessage,
    ToolUseContent,
    ToolUseMessage,
    WriteInput,
)
from .html import escape_html, format_tool_use_title


# =============================================================================
# Tool Input Models Mapping
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


# =============================================================================
# Lenient Parsing Helpers
# =============================================================================
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


# =============================================================================
# Tool Input Parsing
# =============================================================================


def parse_tool_input(tool_name: str, input_data: dict[str, Any]) -> Optional[ToolInput]:
    """Parse tool input dictionary into a typed model.

    Uses strict validation first, then lenient parsing if available.

    Args:
        tool_name: The name of the tool (e.g., "Bash", "Read")
        input_data: The raw input dictionary from the tool_use content

    Returns:
        A typed input model if parsing succeeds, None otherwise.
        When None is returned, the caller should use ToolUseContent itself
        as the fallback (it's part of the ToolInput union).
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
            return None
    return None


# =============================================================================
# Tool Item Processing
# =============================================================================


@dataclass
class ToolItemResult:
    """Result of processing a single tool/thinking/image item."""

    message_type: str
    message_title: str
    content: Optional[MessageContent] = None  # Structured content for rendering
    tool_use_id: Optional[str] = None
    title_hint: Optional[str] = None
    pending_dedup: Optional[str] = None  # For Task result deduplication
    is_error: bool = False  # For tool_result error state


def parse_tool_use_item(
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

    # Parse tool input once, use for both title and message content
    parsed = parse_tool_input(tool_use.name, tool_use.input)

    # Title is computed here but content formatting happens in HtmlRenderer
    tool_message_title = format_tool_use_title(tool_use.name, parsed)
    escaped_id = escape_html(tool_use.id)
    item_tool_use_id = tool_use.id
    tool_title_hint = f"ID: {escaped_id}"

    # Populate tool_use_context for later use when processing tool results
    tool_use_context[item_tool_use_id] = tool_use

    # Create ToolUseMessage wrapper with parsed input for specialized formatting
    # Use ToolUseContent as fallback when no specialized parser exists
    tool_use_message = ToolUseMessage(
        input=parsed if parsed is not None else tool_use,
        tool_use_id=tool_use.id,
        tool_name=tool_use.name,
    )

    return ToolItemResult(
        message_type="tool_use",
        message_title=tool_message_title,
        content=tool_use_message,
        tool_use_id=item_tool_use_id,
        title_hint=tool_title_hint,
    )


def parse_tool_result_item(
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
