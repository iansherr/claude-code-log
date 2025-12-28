"""Tests for image_export.py."""

import pytest
from pathlib import Path

from claude_code_log.image_export import export_image
from claude_code_log.models import ImageContent, ImageSource


@pytest.fixture
def sample_image() -> ImageContent:
    """Create a sample ImageContent for testing."""
    # Minimal valid PNG: 1x1 transparent pixel
    png_data = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAA"
        "BJRU5ErkJggg=="
    )
    return ImageContent(
        type="image",
        source=ImageSource(type="base64", media_type="image/png", data=png_data),
    )


class TestExportImagePlaceholder:
    """Tests for placeholder mode."""

    def test_placeholder_returns_none(self, sample_image: ImageContent):
        """Placeholder mode returns None (caller renders placeholder text)."""
        result = export_image(sample_image, mode="placeholder")
        assert result is None


class TestExportImageEmbedded:
    """Tests for embedded mode."""

    def test_embedded_returns_data_url(self, sample_image: ImageContent):
        """Embedded mode returns data URL."""
        result = export_image(sample_image, mode="embedded")
        assert result is not None
        assert result.startswith("data:image/png;base64,")


class TestExportImageReferenced:
    """Tests for referenced mode."""

    def test_referenced_without_output_dir_returns_none(
        self, sample_image: ImageContent
    ):
        """Referenced mode without output_dir returns None."""
        result = export_image(sample_image, mode="referenced", output_dir=None)
        assert result is None

    def test_referenced_creates_image_file(
        self, sample_image: ImageContent, tmp_path: Path
    ):
        """Referenced mode creates image file and returns relative path."""
        result = export_image(
            sample_image,
            mode="referenced",
            output_dir=tmp_path,
            counter=1,
        )

        assert result == "images/image_0001.png"
        assert (tmp_path / "images" / "image_0001.png").exists()

    def test_referenced_with_different_counter(
        self, sample_image: ImageContent, tmp_path: Path
    ):
        """Referenced mode uses counter for filename."""
        result = export_image(
            sample_image,
            mode="referenced",
            output_dir=tmp_path,
            counter=42,
        )

        assert result == "images/image_0042.png"
        assert (tmp_path / "images" / "image_0042.png").exists()


class TestExportImageUnsupportedMode:
    """Tests for unsupported mode."""

    def test_unsupported_mode_returns_none(self, sample_image: ImageContent):
        """Unsupported mode returns None."""
        result = export_image(sample_image, mode="unknown_mode")
        assert result is None
