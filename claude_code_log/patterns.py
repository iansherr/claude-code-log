"""Shared regex patterns for IDE tag detection and filtering.

These patterns are used both for:
1. HTML rendering (renderer.py) - extracts and renders IDE tags nicely
2. Text filtering (utils.py) - strips IDE tags from summaries/previews
"""

import re

# Pattern 1: <ide_opened_file>content</ide_opened_file>
IDE_OPENED_FILE_PATTERN = re.compile(
    r"<ide_opened_file>(.*?)</ide_opened_file>", flags=re.DOTALL
)

# Pattern 2: <ide_selection>content</ide_selection>
IDE_SELECTION_PATTERN = re.compile(
    r"<ide_selection>(.*?)</ide_selection>", flags=re.DOTALL
)

# Pattern 3: <post-tool-use-hook><ide_diagnostics>JSON</ide_diagnostics></post-tool-use-hook>
IDE_DIAGNOSTICS_PATTERN = re.compile(
    r"<post-tool-use-hook>\s*<ide_diagnostics>(.*?)</ide_diagnostics>\s*</post-tool-use-hook>",
    flags=re.DOTALL,
)


def strip_ide_tags(text: str) -> str:
    """Remove all IDE notification tags from text, keeping only user content.

    This is a lightweight text-only version used for summaries and previews.
    For HTML rendering with IDE tag extraction, see renderer.extract_ide_notifications().

    Args:
        text: User message text potentially containing IDE tags

    Returns:
        Text with all IDE tags removed and whitespace normalized
    """
    result = text

    # Remove all IDE tag patterns
    result = IDE_OPENED_FILE_PATTERN.sub("", result)
    result = IDE_SELECTION_PATTERN.sub("", result)
    result = IDE_DIAGNOSTICS_PATTERN.sub("", result)

    return result.strip()
