"""HTML-specific rendering utilities package.

Re-exports all functions from utils and formatter modules for backward compatibility.
"""

from .utils import (
    css_class_from_message,
    escape_html,
    get_message_emoji,
    get_template_environment,
    render_collapsible_code,
    render_file_content_collapsible,
    render_markdown,
    render_markdown_collapsible,
    starts_with_emoji,
)
from .tool_formatters import (
    format_askuserquestion_content,
    format_askuserquestion_result,
    format_bash_tool_content,
    format_edit_tool_content,
    format_edit_tool_result,
    format_exitplanmode_content,
    format_exitplanmode_result,
    format_multiedit_tool_content,
    format_read_tool_content,
    format_read_tool_result,
    format_task_tool_content,
    format_todowrite_content,
    format_tool_result_content,
    format_tool_use_content,
    format_tool_use_title,
    format_write_tool_content,
    get_tool_summary,
    parse_edit_output,
    parse_read_output,
    render_params_table,
)
from .system_formatters import (
    format_hook_summary_content,
    format_system_content,
)
from ..models import (
    AssistantTextContent,
    BashInputContent,
    BashOutputContent,
    CommandOutputContent,
    CompactedSummaryContent,
    IdeDiagnostic,
    IdeNotificationContent,
    IdeOpenedFile,
    IdeSelection,
    SlashCommandContent,
    ThinkingContentModel,
    UserMemoryContent,
)
from ..parser import (
    parse_bash_input,
    parse_bash_output,
    parse_command_output,
    parse_ide_notifications,
    parse_slash_command,
)
from .user_formatters import (
    format_bash_input_content,
    format_bash_output_content,
    format_command_output_content,
    format_ide_notification_content,
    format_slash_command_content,
    format_user_text_content,
)
from .assistant_formatters import (
    format_assistant_text_content,
    format_image_content,
    format_thinking_content,
)

__all__ = [
    # utils
    "css_class_from_message",
    "escape_html",
    "get_message_emoji",
    "get_template_environment",
    "render_collapsible_code",
    "render_file_content_collapsible",
    "render_markdown",
    "render_markdown_collapsible",
    "starts_with_emoji",
    # tool_formatters (input)
    "format_askuserquestion_content",
    "format_askuserquestion_result",
    "format_bash_tool_content",
    "format_edit_tool_content",
    "format_exitplanmode_content",
    "format_exitplanmode_result",
    "format_multiedit_tool_content",
    "format_read_tool_content",
    "format_task_tool_content",
    "format_todowrite_content",
    "format_tool_use_content",
    "format_tool_use_title",
    "format_write_tool_content",
    "get_tool_summary",
    "render_params_table",
    # tool_formatters (output/result)
    "parse_read_output",
    "format_read_tool_result",
    "parse_edit_output",
    "format_edit_tool_result",
    "format_tool_result_content",
    # system_formatters
    "format_hook_summary_content",
    "format_system_content",
    # user_formatters (content models)
    "SlashCommandContent",
    "CommandOutputContent",
    "BashInputContent",
    "BashOutputContent",
    "CompactedSummaryContent",
    "UserMemoryContent",
    "IdeNotificationContent",
    "IdeOpenedFile",
    "IdeSelection",
    "IdeDiagnostic",
    # user_formatters (formatting)
    "format_slash_command_content",
    "format_command_output_content",
    "format_bash_input_content",
    "format_bash_output_content",
    "format_user_text_content",
    "format_ide_notification_content",
    # user_formatters (parsing)
    "parse_slash_command",
    "parse_command_output",
    "parse_bash_input",
    "parse_bash_output",
    "parse_ide_notifications",
    # assistant_formatters (content models)
    "AssistantTextContent",
    "ThinkingContentModel",
    # assistant_formatters (formatting)
    "format_assistant_text_content",
    "format_thinking_content",
    "format_image_content",
]
