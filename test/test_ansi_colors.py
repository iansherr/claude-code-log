"""Tests for ANSI color code conversion to HTML."""

from claude_code_log.html.ansi_colors import convert_ansi_to_html


class TestAnsiColorConversion:
    """Test ANSI escape code to HTML conversion."""

    def test_standard_colors(self):
        """Test standard ANSI color codes."""
        text = "\x1b[31mRed text\x1b[0m"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-red">Red text</span>' in result

        text = "\x1b[32mGreen\x1b[0m and \x1b[34mBlue\x1b[0m"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-green">Green</span>' in result
        assert '<span class="ansi-blue">Blue</span>' in result

    def test_bright_colors(self):
        """Test bright ANSI color codes."""
        text = "\x1b[91mBright red\x1b[0m"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-bright-red">Bright red</span>' in result

    def test_background_colors(self):
        """Test background color codes."""
        text = "\x1b[41mRed background\x1b[0m"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-bg-red">Red background</span>' in result

    def test_text_styles(self):
        """Test text style codes (bold, italic, etc)."""
        text = "\x1b[1mBold text\x1b[0m"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-bold">Bold text</span>' in result

        text = "\x1b[3mItalic\x1b[0m and \x1b[4mUnderline\x1b[0m"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-italic">Italic</span>' in result
        assert '<span class="ansi-underline">Underline</span>' in result

    def test_rgb_colors(self):
        """Test RGB color codes."""
        text = "\x1b[38;2;255;0;0mRGB Red\x1b[0m"
        result = convert_ansi_to_html(text)
        assert 'style="color: rgb(255, 0, 0)"' in result

        text = "\x1b[48;2;0;255;0mRGB Green Background\x1b[0m"
        result = convert_ansi_to_html(text)
        assert 'style="background-color: rgb(0, 255, 0)"' in result

    def test_combined_styles(self):
        """Test combinations of colors and styles."""
        text = "\x1b[1;31mBold Red\x1b[0m"
        result = convert_ansi_to_html(text)
        assert 'class="ansi-red ansi-bold"' in result

        text = "\x1b[4;34;43mUnderlined Blue on Yellow\x1b[0m"
        result = convert_ansi_to_html(text)
        assert "ansi-blue" in result
        assert "ansi-bg-yellow" in result
        assert "ansi-underline" in result

    def test_reset_codes(self):
        """Test various reset codes."""
        text = "\x1b[31mRed\x1b[39m Normal"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-red">Red</span> Normal' in result

        text = "\x1b[1mBold\x1b[22m Normal"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-bold">Bold</span> Normal' in result

        # Test empty params reset form (ESC[m is equivalent to ESC[0m)
        text = "\x1b[31mRed\x1b[m Normal"
        result = convert_ansi_to_html(text)
        assert '<span class="ansi-red">Red</span> Normal' in result
        assert "\x1b" not in result  # No raw escape sequences

    def test_html_escaping(self):
        """Test that HTML special characters are escaped."""
        text = "\x1b[31m<script>alert('test')</script>\x1b[0m"
        result = convert_ansi_to_html(text)
        assert "&lt;script&gt;" in result
        assert "&lt;/script&gt;" in result
        assert "<script>" not in result  # Should be escaped

    def test_no_ansi_codes(self):
        """Test text without ANSI codes."""
        text = "Plain text without colors"
        result = convert_ansi_to_html(text)
        assert result == "Plain text without colors"

    def test_context_command_pattern(self):
        """Test typical /context command output pattern."""
        # Simulating the color pattern from context command
        text = "\x1b[38;2;136;136;136m⛁ \x1b[38;2;153;153;153m⛁ ⛁ ⛁ ⛁ ⛁ ⛁ \x1b[38;2;215;119;87m⛀ \x1b[38;2;147;51;234m⛀ \x1b[38;2;153;153;153m⛶ \x1b[39m"
        result = convert_ansi_to_html(text)

        # Check for RGB colors
        assert "rgb(136, 136, 136)" in result
        assert "rgb(153, 153, 153)" in result
        assert "rgb(215, 119, 87)" in result
        assert "rgb(147, 51, 234)" in result

        # Check that special characters are preserved
        assert "⛁" in result
        assert "⛀" in result
        assert "⛶" in result

    def test_cursor_movement_stripping(self):
        """Test that cursor movement and screen manipulation codes are stripped."""
        # Test cursor movement codes
        text = "Line 1\x1b[1AOverwritten\x1b[2KCleared line"
        result = convert_ansi_to_html(text)
        assert "Line 1OverwrittenCleared line" == result
        assert "\x1b[1A" not in result
        assert "\x1b[2K" not in result

    def test_complex_cursor_and_color_codes(self):
        """Test mixed cursor movement and color codes."""
        text = "\x1b[31mRed\x1b[1A\x1b[2K\x1b[32mGreen\x1b[0m"
        result = convert_ansi_to_html(text)
        # Should preserve colors but strip cursor movement
        assert '<span class="ansi-red">Red</span>' in result
        assert '<span class="ansi-green">Green</span>' in result
        assert "\x1b[1A" not in result
        assert "\x1b[2K" not in result

    def test_hide_show_cursor_codes(self):
        """Test that cursor visibility codes are stripped."""
        text = "\x1b[?25lHidden cursor text\x1b[?25hVisible cursor text"
        result = convert_ansi_to_html(text)
        assert result == "Hidden cursor textVisible cursor text"
        assert "\x1b[?25l" not in result
        assert "\x1b[?25h" not in result
