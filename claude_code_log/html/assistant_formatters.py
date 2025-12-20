"""HTML formatters for assistant message content.

This module formats assistant message content types to HTML.
Part of the thematic formatter organization:
- system_formatters.py: SystemMessage, HookSummaryMessage
- user_formatters.py: SlashCommandMessage, CommandOutputMessage, BashInputMessage
- assistant_formatters.py: AssistantTextMessage, ThinkingMessage, ImageContent
- tool_formatters.py: tool use/result content

Content models are defined in models.py, this module only handles formatting.
"""

from ..models import (
    AssistantTextMessage,
    ImageContent,
    ThinkingMessage,
    UnknownMessage,
)
from .utils import escape_html, render_markdown_collapsible


# =============================================================================
# Formatting Functions
# =============================================================================


def format_assistant_text_content(
    content: AssistantTextMessage,
    line_threshold: int = 30,
    preview_line_count: int = 10,
) -> str:
    """Format assistant text content as HTML.

    Iterates through content.items preserving order:
    - TextContent: Rendered as markdown with collapsible support
    - ImageContent: Rendered as inline <img> tag with base64 data URL

    Args:
        content: AssistantTextMessage with text/items to render
        line_threshold: Number of lines before content becomes collapsible
        preview_line_count: Number of preview lines to show when collapsed

    Returns:
        HTML string with markdown-rendered, optionally collapsible content
    """
    parts: list[str] = []
    for item in content.items:
        if isinstance(item, ImageContent):
            parts.append(format_image_content(item))
        else:  # TextContent
            if item.text.strip():
                text_html = render_markdown_collapsible(
                    item.text,
                    "assistant-text",
                    line_threshold=line_threshold,
                    preview_line_count=preview_line_count,
                )
                parts.append(text_html)
    return "\n".join(parts)


def format_thinking_content(
    content: ThinkingMessage,
    line_threshold: int = 20,
    preview_line_count: int = 5,
) -> str:
    """Format thinking content as HTML.

    Args:
        content: ThinkingMessage with the thinking text
        line_threshold: Number of lines before content becomes collapsible
        preview_line_count: Number of preview lines to show when collapsed

    Returns:
        HTML string with markdown-rendered, optionally collapsible thinking content
    """
    return render_markdown_collapsible(
        content.thinking,
        "thinking-content",
        line_threshold=line_threshold,
        preview_line_count=preview_line_count,
    )


def format_image_content(image: ImageContent) -> str:
    """Format image content as HTML.

    Args:
        image: ImageContent with base64 image data

    Returns:
        HTML img tag with data URL
    """
    data_url = f"data:{image.source.media_type};base64,{image.source.data}"
    return f'<img src="{data_url}" alt="Uploaded image" class="uploaded-image" />'


def format_unknown_content(content: UnknownMessage) -> str:
    """Format unknown content type as HTML.

    Args:
        content: UnknownMessage with the type name

    Returns:
        HTML paragraph with escaped type name
    """
    escaped_type = escape_html(content.type_name)
    return f"<p>Unknown content type: {escaped_type}</p>"


# =============================================================================
# Public Exports
# =============================================================================

__all__ = [
    # Formatting functions
    "format_assistant_text_content",
    "format_thinking_content",
    "format_image_content",
    "format_unknown_content",
]
