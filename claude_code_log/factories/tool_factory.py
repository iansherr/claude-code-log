"""Factory for tool use and tool result content.

This module handles creation of tool-related content into MessageContent subclasses:
- ToolUseMessage: Tool invocations with typed inputs (BashInput, ReadInput, etc.)
- ToolResultMessage: Tool results with output and context

Also provides creation of tool inputs into typed models:
- create_tool_input(): Create typed tool input from raw dict
- create_tool_use_message(): Process ToolUseContent into ToolItemResult
- create_tool_result_message(): Process ToolResultContent into ToolItemResult
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional, cast

from pydantic import BaseModel

import re

from ..models import (
    # Tool input models
    AskUserQuestionInput,
    AskUserQuestionItem,
    AskUserQuestionOption,
    BashInput,
    EditInput,
    EditItem,
    ExitPlanModeInput,
    GlobInput,
    GrepInput,
    MessageContent,
    MessageMeta,
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
    # Tool output models
    AskUserQuestionAnswer,
    AskUserQuestionOutput,
    BashOutput,
    EditOutput,
    ExitPlanModeOutput,
    ReadOutput,
    TaskOutput,
    ToolOutput,
    WriteOutput,
)
from ..html import escape_html, format_tool_use_title


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
# Tool Input Creation
# =============================================================================


def create_tool_input(
    tool_name: str, input_data: dict[str, Any]
) -> Optional[ToolInput]:
    """Create typed tool input from raw dictionary.

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
# Tool Output Parsing
# =============================================================================
# Parse raw tool result content into typed output models (ReadOutput, EditOutput, etc.)
# Symmetric with Tool Input parsing above.


def _parse_cat_n_snippet(
    lines: list[str], start_idx: int = 0
) -> Optional[tuple[str, Optional[str], int]]:
    """Parse cat-n formatted snippet from lines.

    Args:
        lines: List of lines to parse
        start_idx: Index to start parsing from (default: 0)

    Returns:
        Tuple of (code_content, system_reminder, line_offset) or None if not parseable
    """
    code_lines: list[str] = []
    system_reminder: Optional[str] = None
    in_system_reminder = False
    line_offset = 1  # Default offset

    for line in lines[start_idx:]:
        # Check for system-reminder start
        if "<system-reminder>" in line:
            in_system_reminder = True
            system_reminder = ""
            continue

        # Check for system-reminder end
        if "</system-reminder>" in line:
            in_system_reminder = False
            continue

        # If in system reminder, accumulate reminder text
        if in_system_reminder:
            if system_reminder is not None:
                system_reminder += line + "\n"
            continue

        # Parse regular code line (format: "  123→content")
        match = re.match(r"\s+(\d+)→(.*)$", line)
        if match:
            line_num = int(match.group(1))
            # Capture the first line number as offset
            if not code_lines:
                line_offset = line_num
            code_lines.append(match.group(2))
        elif line.strip() == "":  # Allow empty lines between cat-n lines
            continue
        else:  # Non-matching non-empty line, stop parsing
            break

    if not code_lines:
        return None

    # Join code lines and trim trailing reminder text
    code_content = "\n".join(code_lines)
    if system_reminder:
        system_reminder = system_reminder.strip()

    return (code_content, system_reminder, line_offset)


def parse_read_output(content: str, file_path: Optional[str]) -> Optional[ReadOutput]:
    """Parse Read tool result into structured content.

    Args:
        content: Raw tool result string
        file_path: Path to the file that was read (required for ReadOutput)

    Returns:
        ReadOutput if parsing succeeds, None otherwise
    """
    if not file_path:
        return None

    # Check if content matches the cat-n format pattern (line_number → content)
    lines = content.split("\n")
    if not lines or not re.match(r"\s+\d+→", lines[0]):
        return None

    result = _parse_cat_n_snippet(lines)
    if result is None:
        return None

    code_content, system_reminder, line_offset = result
    num_lines = len(code_content.split("\n"))

    return ReadOutput(
        file_path=file_path,
        content=code_content,
        start_line=line_offset,
        num_lines=num_lines,
        total_lines=num_lines,  # We don't know total from result
        is_truncated=False,  # Can't determine from result
        system_reminder=system_reminder,
    )


def parse_edit_output(content: str, file_path: Optional[str]) -> Optional[EditOutput]:
    """Parse Edit tool result into structured content.

    Edit tool results typically have format:
    "The file ... has been updated. Here's the result of running `cat -n` on a snippet..."
    followed by cat-n formatted lines.

    Args:
        content: Raw tool result string
        file_path: Path to the file that was edited (required for EditOutput)

    Returns:
        EditOutput if parsing succeeds, None otherwise
    """
    if not file_path:
        return None

    # Look for the cat-n snippet after the preamble
    # Pattern: look for first line that matches the cat-n format
    lines = content.split("\n")
    code_start_idx = None

    for i, line in enumerate(lines):
        if re.match(r"\s+\d+→", line):
            code_start_idx = i
            break

    if code_start_idx is None:
        return None

    result = _parse_cat_n_snippet(lines, code_start_idx)
    if result is None:
        return None

    code_content, _system_reminder, line_offset = result
    # Edit tool doesn't use system_reminder

    return EditOutput(
        file_path=file_path,
        success=True,  # If we got here, edit succeeded
        diffs=[],  # We don't have diff info from result
        message=code_content,
        start_line=line_offset,
    )


def parse_write_output(content: str, file_path: Optional[str]) -> Optional[WriteOutput]:
    """Parse Write tool result into structured content.

    Write tool results contain an acknowledgment on the first line.
    We extract just the first line for display.

    Args:
        content: Raw tool result string
        file_path: Path to the file that was written (required for WriteOutput)

    Returns:
        WriteOutput if parsing succeeds, None otherwise
    """
    if not file_path:
        return None

    lines = content.split("\n")
    if not lines:
        return None

    first_line = lines[0]
    return WriteOutput(
        file_path=file_path,
        success=True,  # If we got content, write succeeded
        message=first_line,
    )


def parse_task_output(content: str, file_path: Optional[str]) -> Optional[TaskOutput]:
    """Parse Task tool result into structured content.

    Task tool results contain the agent's response as markdown.

    Args:
        content: Raw tool result string (agent's response)
        file_path: Unused for Task tool

    Returns:
        TaskOutput with the agent's response
    """
    del file_path  # Unused
    if not content:
        return None

    return TaskOutput(result=content)


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
    if re.search(r"/[a-zA-Z0-9_-]+(/[a-zA-Z0-9_.-]+)*", content):  # Unix-style paths
        return True

    # Check for common command output patterns
    if any(indicator in content for indicator in bash_indicators):
        return True

    return False


def parse_bash_output(content: str, file_path: Optional[str]) -> Optional[BashOutput]:
    """Parse Bash tool result into structured content.

    Detects ANSI escape sequences for terminal formatting.

    Args:
        content: Raw tool result string
        file_path: Unused for Bash tool

    Returns:
        BashOutput with content and ANSI flag
    """
    del file_path  # Unused
    if not content:
        return None

    has_ansi = _looks_like_bash_output(content)
    return BashOutput(content=content, has_ansi=has_ansi)


def parse_askuserquestion_output(
    content: str, file_path: Optional[str]
) -> Optional[AskUserQuestionOutput]:
    """Parse AskUserQuestion tool result into structured content.

    Parses the result format:
    'User has answered your questions: "Q1"="A1", "Q2"="A2". You can now continue...'

    Args:
        content: Raw tool result string
        file_path: Unused for AskUserQuestion tool

    Returns:
        AskUserQuestionOutput with Q&A pairs
    """
    del file_path  # Unused
    if not content:
        return None

    # Check if this is a successful answer
    if not content.startswith("User has answered your question"):
        return None

    # Extract the Q&A portion between the colon and the final sentence
    match = re.match(
        r"User has answered your questions?: (.+)\. You can now continue",
        content,
        re.DOTALL,
    )
    if not match:
        return None

    qa_portion = match.group(1)

    # Parse "Question"="Answer" pairs
    qa_pattern = re.compile(r'"([^"]+)"="([^"]+)"')
    pairs = qa_pattern.findall(qa_portion)

    if not pairs:
        return None

    answers = [AskUserQuestionAnswer(question=q, answer=a) for q, a in pairs]
    return AskUserQuestionOutput(answers=answers, raw_message=content)


def parse_exitplanmode_output(
    content: str, file_path: Optional[str]
) -> Optional[ExitPlanModeOutput]:
    """Parse ExitPlanMode tool result into structured content.

    Truncates redundant plan echo on success.
    When a plan is approved, the result contains:
    1. A confirmation message
    2. Path to saved plan file
    3. "## Approved Plan:" followed by full plan text (redundant)

    Args:
        content: Raw tool result string
        file_path: Unused for ExitPlanMode tool

    Returns:
        ExitPlanModeOutput with truncated message
    """
    del file_path  # Unused
    if not content:
        return None

    approved = "User has approved your plan" in content

    if approved:
        # Truncate at "## Approved Plan:"
        marker = "## Approved Plan:"
        marker_pos = content.find(marker)
        if marker_pos > 0:
            message = content[:marker_pos].rstrip()
        else:
            message = content
    else:
        message = content

    return ExitPlanModeOutput(message=message, approved=approved)


# Registry of tool output parsers: tool_name -> parser(content, file_path) -> Optional[ToolOutput]
TOOL_OUTPUT_PARSERS: dict[str, Callable[[str, Optional[str]], Optional[ToolOutput]]] = {
    "Read": parse_read_output,
    "Edit": parse_edit_output,
    "Write": parse_write_output,
    "Bash": parse_bash_output,
    "Task": parse_task_output,
    "AskUserQuestion": parse_askuserquestion_output,
    "ExitPlanMode": parse_exitplanmode_output,
}


def create_tool_output(
    tool_name: str,
    tool_result: ToolResultContent,
    file_path: Optional[str] = None,
) -> ToolOutput:
    """Create typed tool output from raw ToolResultContent.

    Parses the raw content into specialized output types when possible,
    using the TOOL_OUTPUT_PARSERS registry.

    Args:
        tool_name: The name of the tool (e.g., "Bash", "Read")
        tool_result: The raw tool result content
        file_path: Optional file path for file-based tools (Read, Edit, Write)

    Returns:
        A typed output model if parsing succeeds, ToolResultContent as fallback.
    """
    # Handle both string and structured content
    if not isinstance(tool_result.content, str):
        # Structured content (list of dicts) - use generic fallback
        return tool_result

    raw_content = tool_result.content

    # Look up parser in registry and parse if available
    if (parser := TOOL_OUTPUT_PARSERS.get(tool_name)) and (
        parsed := parser(raw_content, file_path)
    ):
        return parsed

    # Fallback to raw ToolResultContent
    return tool_result


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
    is_error: bool = False  # For tool_result error state


def create_tool_use_message(
    meta: MessageMeta,
    tool_use: ToolUseContent,
    tool_use_context: dict[str, ToolUseContent],
) -> ToolItemResult:
    """Create ToolItemResult from a tool_use content item.

    Args:
        tool_use: The tool use content item
        tool_use_context: Dict to populate with tool_use_id -> ToolUseContent mapping
        meta: Message metadata

    Returns:
        ToolItemResult with tool_use content model
    """

    # Parse tool input once, use for both title and message content
    parsed = create_tool_input(tool_use.name, tool_use.input)

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
        meta,
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


def create_tool_result_message(
    meta: MessageMeta,
    tool_result: ToolResultContent,
    tool_use_context: dict[str, ToolUseContent],
) -> ToolItemResult:
    """Create ToolItemResult from a tool_result content item.

    Args:
        tool_result: The tool result content item
        tool_use_context: Dict with tool_use_id -> ToolUseContent mapping
        meta: Message metadata

    Returns:
        ToolItemResult with tool_result content model
    """

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

    # Parse into typed output (ReadOutput, EditOutput, etc.) when possible
    parsed_output = create_tool_output(
        result_tool_name or "",
        tool_result,
        result_file_path,
    )

    # Create content model with rendering context
    content_model = ToolResultMessage(
        meta,
        tool_use_id=tool_result.tool_use_id,
        output=parsed_output,
        is_error=tool_result.is_error or False,
        tool_name=result_tool_name,
        file_path=result_file_path,
    )

    escaped_id = escape_html(tool_result.tool_use_id)
    tool_title_hint = f"ID: {escaped_id}"
    tool_message_title = "Error" if tool_result.is_error else ""

    return ToolItemResult(
        message_type="tool_result",
        message_title=tool_message_title,
        content=content_model,
        tool_use_id=tool_result.tool_use_id,
        title_hint=tool_title_hint,
        is_error=tool_result.is_error or False,
    )
