"""HTML rendering functions for tool use and tool result content.

This module contains all HTML formatters for specific tools:
- AskUserQuestion tool (input + result)
- ExitPlanMode tool (input + result)
- TodoWrite tool
- Read/Write/Edit/Multiedit tools
- Bash tool
- Task tool
- Generic parameter table rendering
- Tool use content dispatcher

These formatters take tool-specific input/output data and generate
HTML for display in transcripts.
"""

import base64
import binascii
import json
import re
from typing import Any, Optional, cast

from .utils import (
    escape_html,
    render_file_content_collapsible,
    render_markdown_collapsible,
)
from ..models import (
    AskUserQuestionInput,
    AskUserQuestionItem,
    BashInput,
    EditInput,
    EditOutput,
    ExitPlanModeInput,
    MultiEditInput,
    ReadInput,
    ReadOutput,
    TaskInput,
    TodoWriteInput,
    ToolInput,
    ToolResultContent,
    ToolUseContent,
    ToolUseMessage,
    WriteInput,
)
from .ansi_colors import convert_ansi_to_html
from .renderer_code import render_single_diff


# -- AskUserQuestion Tool -----------------------------------------------------


def _render_question_item(q: AskUserQuestionItem) -> str:
    """Render a single question item to HTML."""
    html_parts: list[str] = ['<div class="question-block">']

    # Header (if present)
    if q.header:
        escaped_header = escape_html(q.header)
        html_parts.append(f'<div class="question-header">{escaped_header}</div>')

    # Question text with icon
    question_text = escape_html(q.question)
    html_parts.append(f'<div class="question-text">❓ {question_text}</div>')

    # Options (if present)
    if q.options:
        select_hint = "(select multiple)" if q.multiSelect else "(select one)"
        html_parts.append(f'<div class="question-options-hint">{select_hint}</div>')
        html_parts.append('<ul class="question-options">')
        for opt in q.options:
            label = escape_html(opt.label)
            if opt.description:
                desc_html = f'<span class="option-desc"> — {escape_html(opt.description)}</span>'
            else:
                desc_html = ""
            html_parts.append(
                f'<li class="question-option"><strong>{label}</strong>{desc_html}</li>'
            )
        html_parts.append("</ul>")

    html_parts.append("</div>")  # Close question-block
    return "".join(html_parts)


def format_askuserquestion_content(ask_input: AskUserQuestionInput) -> str:
    """Format AskUserQuestion tool use content with prominent question display.

    Args:
        ask_input: Typed AskUserQuestionInput with questions list and/or single question.

    Handles multiple questions in a single tool use, each with optional header,
    options (with label and description), and multiSelect flag.
    """
    # Build list of questions from both formats
    questions: list[AskUserQuestionItem] = list(ask_input.questions)

    # Handle single question format (legacy)
    if not questions and ask_input.question:
        questions.append(AskUserQuestionItem(question=ask_input.question))

    if not questions:
        return '<div class="askuserquestion-content"><em>No question</em></div>'

    # Build HTML for all questions
    html_parts: list[str] = ['<div class="askuserquestion-content">']
    for q in questions:
        html_parts.append(_render_question_item(q))
    html_parts.append("</div>")  # Close askuserquestion-content
    return "".join(html_parts)


def format_askuserquestion_result(content: str) -> str:
    """Format AskUserQuestion tool result with styled question/answer pairs.

    Parses the result format:
    'User has answered your questions: "Q1"="A1", "Q2"="A2". You can now continue...'

    Returns HTML with styled Q&A blocks matching the input styling.
    """
    # Check if this is a successful answer
    if not content.startswith("User has answered your question"):
        # Return as-is for errors or unexpected format
        return ""

    # Extract the Q&A portion between the colon and the final sentence
    # Pattern: 'User has answered your questions: "Q"="A", "Q"="A". You can now...'
    match = re.match(
        r"User has answered your questions?: (.+)\. You can now continue",
        content,
        re.DOTALL,
    )
    if not match:
        return ""

    qa_portion = match.group(1)

    # Parse "Question"="Answer" pairs
    # Pattern: "question text"="answer text"
    qa_pattern = re.compile(r'"([^"]+)"="([^"]+)"')
    pairs = qa_pattern.findall(qa_portion)

    if not pairs:
        return ""

    # Build styled HTML
    html_parts: list[str] = [
        '<div class="askuserquestion-content askuserquestion-result">'
    ]

    for question, answer in pairs:
        escaped_q = escape_html(question)
        escaped_a = escape_html(answer)
        html_parts.append('<div class="question-block answered">')
        html_parts.append(f'<div class="question-text">❓ {escaped_q}</div>')
        html_parts.append(f'<div class="answer-text">✅ {escaped_a}</div>')
        html_parts.append("</div>")

    html_parts.append("</div>")
    return "".join(html_parts)


# -- ExitPlanMode Tool --------------------------------------------------------


def format_exitplanmode_content(exit_input: ExitPlanModeInput) -> str:
    """Format ExitPlanMode tool use content with collapsible plan markdown.

    Args:
        exit_input: Typed ExitPlanModeInput with plan content.

    Renders the plan markdown in a collapsible section, similar to Task tool results.
    """
    if not exit_input.plan:
        return '<div class="plan-content"><em>No plan</em></div>'

    return render_markdown_collapsible(exit_input.plan, "plan-content")


def format_exitplanmode_result(content: str) -> str:
    """Format ExitPlanMode tool result, truncating the redundant plan echo.

    When a plan is approved, the result contains:
    1. A confirmation message
    2. Path to saved plan file
    3. "## Approved Plan:" followed by full plan text (redundant)

    We truncate everything after "## Approved Plan:" to avoid duplication.
    For error results (plan not approved), we keep the full content.
    """
    # Check if this is a successful approval
    if "User has approved your plan" in content:
        # Truncate at "## Approved Plan:"
        marker = "## Approved Plan:"
        marker_pos = content.find(marker)
        if marker_pos > 0:
            # Keep everything before the marker, strip trailing whitespace
            return content[:marker_pos].rstrip()

    # For errors or other cases, return as-is
    return content


# -- TodoWrite Tool -----------------------------------------------------------


def format_todowrite_content(todo_input: TodoWriteInput) -> str:
    """Format TodoWrite tool use content as a todo list.

    Args:
        todo_input: Typed TodoWriteInput with list of todo items.
    """
    if not todo_input.todos:
        return """
        <div class="todo-content">
            <p><em>No todos found</em></p>
        </div>
        """

    # Status emojis
    status_emojis = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}

    # Build todo list HTML - todos are typed TodoWriteItem objects
    todo_items: list[str] = []
    for todo in todo_input.todos:
        todo_id = escape_html(todo.id) if todo.id else ""
        content = escape_html(todo.content) if todo.content else ""
        status = todo.status or "pending"
        priority = todo.priority or "medium"
        status_emoji = status_emojis.get(status, "⏳")

        # CSS class for styling
        item_class = f"todo-item {status} {priority}"

        id_html = f'<span class="todo-id">#{todo_id}</span>' if todo.id else ""
        todo_items.append(f"""
            <div class="{item_class}">
                <span class="todo-status">{status_emoji}</span>
                <span class="todo-content">{content}</span>
                {id_html}
            </div>
        """)

    todos_html = "".join(todo_items)

    return f"""
    <div class="todo-list">
        {todos_html}
    </div>
    """


# -- File Tools (Read/Write) --------------------------------------------------


def format_read_tool_content(read_input: ReadInput) -> str:  # noqa: ARG001
    """Format Read tool use content showing file path.

    Args:
        read_input: Typed ReadInput with file_path, offset, and limit.

    Note: File path is now shown in the header, so we skip content here.
    """
    # File path is now shown in header, so no content needed
    # Don't show offset/limit parameters as they'll be visible in the result
    return ""


# -- Tool Result Parsing (cat-n format) ---------------------------------------


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


def parse_read_output(content: str, file_path: str) -> Optional[ReadOutput]:
    """Parse Read tool result into structured content.

    Args:
        content: Raw tool result string
        file_path: Path to the file that was read

    Returns:
        ReadOutput if parsing succeeds, None otherwise
    """
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


def format_read_tool_result(output: ReadOutput) -> str:
    """Format Read tool result as HTML with syntax highlighting.

    Args:
        output: Parsed ReadOutput

    Returns:
        HTML string with syntax-highlighted, collapsible file content
    """
    # Build system reminder suffix if present
    suffix_html = ""
    if output.system_reminder:
        escaped_reminder = escape_html(output.system_reminder)
        suffix_html = (
            f"<div class='system-reminder'>🤖 <em>{escaped_reminder}</em></div>"
        )

    return render_file_content_collapsible(
        output.content,
        output.file_path,
        "read-tool-result",
        linenostart=output.start_line,
        suffix_html=suffix_html,
    )


def parse_edit_output(content: str, file_path: str) -> Optional[EditOutput]:
    """Parse Edit tool result into structured content.

    Edit tool results typically have format:
    "The file ... has been updated. Here's the result of running `cat -n` on a snippet..."
    followed by cat-n formatted lines.

    Args:
        content: Raw tool result string
        file_path: Path to the file that was edited

    Returns:
        EditOutput if parsing succeeds, None otherwise
    """
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


def format_edit_tool_result(output: EditOutput) -> str:
    """Format Edit tool result as HTML with syntax highlighting.

    Args:
        output: Parsed EditOutput

    Returns:
        HTML string with syntax-highlighted, collapsible file content
    """
    return render_file_content_collapsible(
        output.message,  # message contains the code snippet
        output.file_path,
        "edit-tool-result",
        linenostart=output.start_line,
    )


def format_write_tool_content(write_input: WriteInput) -> str:
    """Format Write tool use content with Pygments syntax highlighting.

    Args:
        write_input: Typed WriteInput with file_path and content.
    Note: File path is now shown in the header, so we skip it here.
    """
    return render_file_content_collapsible(
        write_input.content, write_input.file_path, "write-tool-content"
    )


# -- Edit Tools (Edit/Multiedit) ----------------------------------------------


def format_edit_tool_content(edit_input: EditInput) -> str:
    """Format Edit tool use content as a diff view with intra-line highlighting.

    Args:
        edit_input: Typed EditInput with old_string, new_string, replace_all.
    Note: File path is now shown in the header, so we skip it here.
    """
    html_parts = ["<div class='edit-tool-content'>"]

    if edit_input.replace_all:
        html_parts.append(
            "<div class='edit-replace-all'>🔄 Replace all occurrences</div>"
        )

    # Use shared diff rendering helper
    html_parts.append(render_single_diff(edit_input.old_string, edit_input.new_string))
    html_parts.append("</div>")

    return "".join(html_parts)


def format_multiedit_tool_content(multiedit_input: MultiEditInput) -> str:
    """Format Multiedit tool use content showing multiple diffs.

    Args:
        multiedit_input: Typed MultiEditInput with file_path and list of edits.
    """
    escaped_path = escape_html(multiedit_input.file_path)

    html_parts = ["<div class='multiedit-tool-content'>"]

    # File path header
    html_parts.append(f"<div class='multiedit-file-path'>📝 {escaped_path}</div>")
    html_parts.append(
        f"<div class='multiedit-count'>Applying {len(multiedit_input.edits)} edits</div>"
    )

    # Render each edit as a diff - edits are typed EditItem objects
    for idx, edit in enumerate(multiedit_input.edits, 1):
        html_parts.append(
            f"<div class='multiedit-item'><div class='multiedit-item-header'>Edit #{idx}</div>"
        )
        html_parts.append(render_single_diff(edit.old_string, edit.new_string))
        html_parts.append("</div>")

    html_parts.append("</div>")
    return "".join(html_parts)


# -- Bash Tool ----------------------------------------------------------------


def format_bash_tool_content(bash_input: BashInput) -> str:
    """Format Bash tool use content in VS Code extension style.

    Args:
        bash_input: Typed BashInput with command, description, timeout, etc.
    Note: Description is now shown in the header, so we skip it here.
    """
    escaped_command = escape_html(bash_input.command)

    html_parts = ["<div class='bash-tool-content'>"]
    html_parts.append(f"<pre class='bash-tool-command'>{escaped_command}</pre>")
    html_parts.append("</div>")

    return "".join(html_parts)


# -- Task Tool ----------------------------------------------------------------


def format_task_tool_content(task_input: TaskInput) -> str:
    """Format Task tool content with markdown-rendered prompt.

    Args:
        task_input: Typed TaskInput with prompt, subagent_type, etc.

    Task tool spawns sub-agents. We render the prompt as the main content.
    The sidechain user message (which would duplicate this prompt) is skipped.

    For long prompts (>20 lines), the content is made collapsible with a
    preview of the first few lines to keep the transcript vertically compact.
    """
    return render_markdown_collapsible(task_input.prompt, "task-prompt")


# -- Tool Summary and Title ---------------------------------------------------


def get_tool_summary(parsed: Optional[ToolInput]) -> Optional[str]:
    """Extract a one-line summary from parsed tool input for display in header.

    Returns a brief description or filename that can be shown in the message header
    to save vertical space.

    Args:
        parsed: Parsed tool input, or None if parsing failed/not available
    """
    if isinstance(parsed, BashInput):
        return parsed.description

    if isinstance(parsed, (ReadInput, EditInput, WriteInput)):
        return parsed.file_path if parsed.file_path else None

    if isinstance(parsed, TaskInput):
        return parsed.description if parsed.description else None

    # No summary for other tools or unparsed input
    return None


def format_tool_use_title(tool_name: str, parsed: Optional[ToolInput]) -> str:
    """Generate the title HTML for a tool use message.

    Returns HTML string for the message header, with tool name, icon,
    and optional summary/metadata.

    Args:
        tool_name: The tool name (e.g., "Bash", "Read", "Edit")
        parsed: Parsed tool input, or None if parsing failed/not available
    """
    escaped_name = escape_html(tool_name)
    summary = get_tool_summary(parsed)

    # TodoWrite: fixed title
    if tool_name == "TodoWrite":
        return "📝 Todo List"

    # Task: show subagent_type and description
    if isinstance(parsed, TaskInput):
        escaped_subagent = (
            escape_html(parsed.subagent_type) if parsed.subagent_type else ""
        )
        description = parsed.description

        if description and parsed.subagent_type:
            escaped_desc = escape_html(description)
            return f"🔧 {escaped_name} <span class='tool-summary'>{escaped_desc}</span> <span class='tool-subagent'>({escaped_subagent})</span>"
        elif description:
            escaped_desc = escape_html(description)
            return f"🔧 {escaped_name} <span class='tool-summary'>{escaped_desc}</span>"
        elif parsed.subagent_type:
            return f"🔧 {escaped_name} <span class='tool-subagent'>({escaped_subagent})</span>"
        else:
            return f"🔧 {escaped_name}"

    # Edit/Write: use 📝 icon
    if isinstance(parsed, (EditInput, WriteInput)):
        if summary:
            escaped_summary = escape_html(summary)
            return (
                f"📝 {escaped_name} <span class='tool-summary'>{escaped_summary}</span>"
            )
        else:
            return f"📝 {escaped_name}"

    # Read: use 📄 icon
    if isinstance(parsed, ReadInput):
        if summary:
            escaped_summary = escape_html(summary)
            return (
                f"📄 {escaped_name} <span class='tool-summary'>{escaped_summary}</span>"
            )
        else:
            return f"📄 {escaped_name}"

    # Other tools: append summary if present
    if summary:
        escaped_summary = escape_html(summary)
        return f"{escaped_name} <span class='tool-summary'>{escaped_summary}</span>"

    return escaped_name


# -- Generic Parameter Table --------------------------------------------------


def render_params_table(params: dict[str, Any]) -> str:
    """Render a dictionary of parameters as an HTML table.

    Reusable for tool parameters, diagnostic objects, etc.
    """
    if not params:
        return "<div class='tool-params-empty'>No parameters</div>"

    html_parts = ["<table class='tool-params-table'>"]

    for key, value in params.items():
        escaped_key = escape_html(str(key))

        # If value is structured (dict/list), render as JSON
        if isinstance(value, (dict, list)):
            try:
                formatted_value = json.dumps(value, indent=2, ensure_ascii=False)  # type: ignore[arg-type]
                escaped_value = escape_html(formatted_value)

                # Make long structured values collapsible
                if len(formatted_value) > 200:
                    preview = escape_html(formatted_value[:100]) + "..."
                    value_html = f"""
                        <details class='tool-param-collapsible'>
                            <summary>{preview}</summary>
                            <pre class='tool-param-structured'>{escaped_value}</pre>
                        </details>
                    """
                else:
                    value_html = (
                        f"<pre class='tool-param-structured'>{escaped_value}</pre>"
                    )
            except (TypeError, ValueError):
                escaped_value = escape_html(str(value))  # type: ignore[arg-type]
                value_html = escaped_value
        else:
            # Simple value, render as-is (or collapsible if long)
            escaped_value = escape_html(str(value))

            # Make long string values collapsible
            if len(str(value)) > 100:
                preview = escape_html(str(value)[:80]) + "..."
                value_html = f"""
                    <details class='tool-param-collapsible'>
                        <summary>{preview}</summary>
                        <div class='tool-param-full'>{escaped_value}</div>
                    </details>
                """
            else:
                value_html = escaped_value

        html_parts.append(f"""
            <tr>
                <td class='tool-param-key'>{escaped_key}</td>
                <td class='tool-param-value'>{value_html}</td>
            </tr>
        """)

    html_parts.append("</table>")
    return "".join(html_parts)


# -- Tool Use Dispatcher ------------------------------------------------------


def format_tool_use_content(content: ToolUseMessage) -> str:
    """Format ToolUseMessage as HTML.

    Dispatches to specialized formatters based on the parsed input type.
    Falls back to rendering the raw input dict if parsing was incomplete.

    Args:
        content: ToolUseMessage with parsed input and metadata

    Returns:
        HTML string for the tool use content
    """
    parsed_input = content.input

    # Dispatch based on parsed type
    if isinstance(parsed_input, TodoWriteInput):
        return format_todowrite_content(parsed_input)

    if isinstance(parsed_input, BashInput):
        return format_bash_tool_content(parsed_input)

    if isinstance(parsed_input, EditInput):
        return format_edit_tool_content(parsed_input)

    if isinstance(parsed_input, MultiEditInput):
        return format_multiedit_tool_content(parsed_input)

    if isinstance(parsed_input, WriteInput):
        return format_write_tool_content(parsed_input)

    if isinstance(parsed_input, TaskInput):
        return format_task_tool_content(parsed_input)

    if isinstance(parsed_input, ReadInput):
        return format_read_tool_content(parsed_input)

    if isinstance(parsed_input, AskUserQuestionInput):
        return format_askuserquestion_content(parsed_input)

    if isinstance(parsed_input, ExitPlanModeInput):
        return format_exitplanmode_content(parsed_input)

    # Fallback: ToolUseContent - render its input dict as params table
    if isinstance(parsed_input, ToolUseContent):
        return render_params_table(parsed_input.input)

    # Last resort: string representation (shouldn't happen with ToolInput union)
    return f"<pre>{parsed_input}</pre>"


# -- Tool Result Content Formatter -------------------------------------------


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


def format_tool_result_content(
    tool_result: ToolResultContent,
    file_path: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> str:
    """Format tool result content as HTML, including images.

    Args:
        tool_result: The tool result content
        file_path: Optional file path for context (used for Read/Edit/Write tool rendering)
        tool_name: Optional tool name for specialized rendering (e.g., "Write", "Read", "Edit", "Task")
    """
    # Handle both string and structured content
    if isinstance(tool_result.content, str):
        raw_content = tool_result.content
        has_images = False
        image_html_parts: list[str] = []
    else:
        # Content is a list of structured items, extract text and images
        content_parts: list[str] = []
        image_html_parts: list[str] = []
        for item in tool_result.content:
            item_type = item.get("type")
            if item_type == "text":
                text_value = item.get("text")
                if isinstance(text_value, str):
                    content_parts.append(text_value)
            elif item_type == "image":
                # Handle image content within tool results
                source = cast(dict[str, Any], item.get("source", {}))
                if source:
                    media_type: str = str(source.get("media_type", "image/png"))
                    # Restrict to safe image types to prevent XSS via SVG
                    allowed_media_types = {
                        "image/png",
                        "image/jpeg",
                        "image/gif",
                        "image/webp",
                    }
                    if media_type not in allowed_media_types:
                        continue
                    data: str = str(source.get("data", ""))
                    if data:
                        # Validate base64 data to prevent corruption/injection
                        try:
                            base64.b64decode(data, validate=True)
                        except (binascii.Error, ValueError):
                            continue
                        data_url = f"data:{media_type};base64,{data}"
                        image_html_parts.append(
                            f'<img src="{escape_html(data_url)}" alt="Tool result image" '
                            f'class="tool-result-image" />'
                        )
        raw_content = "\n".join(content_parts)
        has_images = len(image_html_parts) > 0

    # Strip <tool_use_error> XML tags but keep the content inside
    # Also strip redundant "String: ..." portions that echo the input
    if raw_content:
        # Remove <tool_use_error>...</tool_use_error> tags but keep inner content
        raw_content = re.sub(
            r"<tool_use_error>(.*?)</tool_use_error>",
            r"\1",
            raw_content,
            flags=re.DOTALL,
        )
        # Remove "String: ..." portions that echo the input (everything after "String:" to end)
        raw_content = re.sub(r"\nString:.*$", "", raw_content, flags=re.DOTALL)

    # Special handling for Write tool: only show first line (acknowledgment) on success
    if tool_name == "Write" and not tool_result.is_error and not has_images:
        lines = raw_content.split("\n")
        if lines:
            # Keep only the first acknowledgment line and add ellipsis
            first_line = lines[0]
            escaped_html = escape_html(first_line)
            return f"<pre>{escaped_html} ...</pre>"

    # Try to parse as Read tool result if file_path is provided
    if file_path and tool_name == "Read" and not has_images:
        read_output = parse_read_output(raw_content, file_path)
        if read_output:
            return format_read_tool_result(read_output)

    # Try to parse as Edit tool result if file_path is provided
    if file_path and tool_name == "Edit" and not has_images:
        edit_output = parse_edit_output(raw_content, file_path)
        if edit_output:
            return format_edit_tool_result(edit_output)

    # Special handling for Task tool: render result as markdown with Pygments (agent's final message)
    # Deduplication is now handled retroactively by replacing the sub-assistant content
    if tool_name == "Task" and not has_images:
        return render_markdown_collapsible(raw_content, "task-result")

    # Special handling for ExitPlanMode tool: truncate redundant plan echo on success
    if tool_name == "ExitPlanMode" and not has_images:
        processed_content = format_exitplanmode_result(raw_content)
        escaped_content = escape_html(processed_content)
        return f"<pre>{escaped_content}</pre>"

    # Special handling for AskUserQuestion tool: render Q&A pairs with styling
    if tool_name == "AskUserQuestion" and not has_images:
        styled_result = format_askuserquestion_result(raw_content)
        if styled_result:
            return styled_result
        # Fall through to default handling if parsing fails

    # Check if this looks like Bash tool output and process ANSI codes
    # Bash tool results often contain ANSI escape sequences and terminal output
    is_ansi = _looks_like_bash_output(raw_content)
    full_html = (
        convert_ansi_to_html(raw_content) if is_ansi else escape_html(raw_content)
    )
    # For preview, always use plain escaped text (don't truncate HTML with tags)
    preview_html = (
        escape_html(raw_content[:200]) + "..."
        if len(raw_content) > 200
        else escape_html(raw_content)
    )

    # Build final HTML based on content length and presence of images
    if has_images:
        # Combine text and images
        text_html = f"<pre>{full_html}</pre>" if full_html else ""
        images_html = "".join(image_html_parts)
        combined_content = f"{text_html}{images_html}"

        # Always make collapsible when images are present
        preview_text = "Text and image content"
        return f"""
    <details class="collapsible-details">
        <summary>
            <span class='preview-text'>{preview_text}</span>
        </summary>
        <div class="details-content">
            {combined_content}
        </div>
    </details>
    """
    else:
        # Text-only content (existing behavior)
        # For simple content, show directly without collapsible wrapper
        if len(raw_content) <= 200:
            return f"<pre>{full_html}</pre>"

        # For longer content, use collapsible details but no extra wrapper
        return f"""
    <details class="collapsible-details">
        <summary>
            <div class="preview-content"><pre>{preview_html}</pre></div>
        </summary>
        <div class="details-content">
            <pre>{full_html}</pre>
        </div>
    </details>
    """


# -- Public Exports -----------------------------------------------------------

__all__ = [
    # AskUserQuestion
    "format_askuserquestion_content",
    "format_askuserquestion_result",
    # ExitPlanMode
    "format_exitplanmode_content",
    "format_exitplanmode_result",
    # TodoWrite
    "format_todowrite_content",
    # File tools (input)
    "format_read_tool_content",
    "format_write_tool_content",
    # File tools (output/result)
    "parse_read_output",
    "format_read_tool_result",
    "parse_edit_output",
    "format_edit_tool_result",
    # Edit tools
    "format_edit_tool_content",
    "format_multiedit_tool_content",
    # Bash
    "format_bash_tool_content",
    # Task
    "format_task_tool_content",
    # Tool summary and title
    "get_tool_summary",
    "format_tool_use_title",
    # Generic
    "render_params_table",
    # Dispatcher
    "format_tool_use_content",
    # Tool result
    "format_tool_result_content",
]
