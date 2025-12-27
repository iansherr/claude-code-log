"""Image export utilities for Claude Code transcripts.

This module provides format-agnostic image export functionality that can be used
by both HTML and Markdown renderers.
"""

import base64
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ImageContent


def export_image(
    image: "ImageContent",
    mode: str,
    output_dir: Path | None = None,
    counter: int = 0,
) -> str:
    """Export image content based on the specified mode.

    Args:
        image: ImageContent with base64-encoded image data
        mode: Export mode - "placeholder", "embedded", or "referenced"
        output_dir: Output directory for referenced images (required for "referenced" mode)
        counter: Image counter for generating unique filenames

    Returns:
        Markdown/HTML image reference string based on mode:
        - placeholder: "[Image]"
        - embedded: "![image](data:image/...;base64,...)"
        - referenced: "![image](images/image_0001.png)"
    """
    if mode == "placeholder":
        return "[Image]"

    elif mode == "embedded":
        data_url = f"data:{image.source.media_type};base64,{image.source.data}"
        return f"![image]({data_url})"

    elif mode == "referenced":
        if output_dir is None:
            return "[Image: export directory not set]"

        # Create images subdirectory
        images_dir = output_dir / "images"
        images_dir.mkdir(exist_ok=True)

        # Generate filename based on media type
        ext = _get_extension(image.source.media_type)
        filename = f"image_{counter:04d}{ext}"
        filepath = images_dir / filename

        # Decode and write image
        image_data = base64.b64decode(image.source.data)
        filepath.write_bytes(image_data)

        return f"![image](images/{filename})"

    else:
        return f"[Image: unsupported mode '{mode}']"


def _get_extension(media_type: str) -> str:
    """Get file extension from media type."""
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    return ext_map.get(media_type, ".png")
