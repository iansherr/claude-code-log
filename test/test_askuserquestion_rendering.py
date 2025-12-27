#!/usr/bin/env python3
"""Test cases for AskUserQuestion tool rendering."""

from claude_code_log.html import (
    format_askuserquestion_input,
    format_askuserquestion_result,
)
from claude_code_log.models import (
    AskUserQuestionInput,
    AskUserQuestionItem,
    AskUserQuestionOption,
)


class TestAskUserQuestionRendering:
    """Test AskUserQuestion tool rendering functionality."""

    def test_format_askuserquestion_multiple_questions(self):
        """Test AskUserQuestion formatting with multiple questions."""
        ask_input = AskUserQuestionInput(
            questions=[
                AskUserQuestionItem(
                    question="Should tar archives be processed recursively?",
                    header="Filesystem tar",
                    options=[
                        AskUserQuestionOption(
                            label="Yes, both filesystem and embedded",
                            description="Treat .tar/.tar.gz like .zip",
                        ),
                        AskUserQuestionOption(
                            label="Only embedded tar archives",
                            description="Only process tar archives inside ZIP files",
                        ),
                    ],
                    multiSelect=False,
                ),
                AskUserQuestionItem(
                    question="Which tar formats should be supported?",
                    header="Tar formats",
                    options=[
                        AskUserQuestionOption(
                            label=".tar and .tar.gz only",
                            description="Most common",
                        ),
                        AskUserQuestionOption(
                            label="Also .tgz",
                            description="Include .tgz alias",
                        ),
                    ],
                    multiSelect=False,
                ),
            ]
        )

        html = format_askuserquestion_input(ask_input)

        # Check overall structure
        assert 'class="askuserquestion-content"' in html
        assert 'class="question-block"' in html

        # Check both questions are rendered
        assert "Should tar archives be processed recursively?" in html
        assert "Which tar formats should be supported?" in html

        # Check headers
        assert "Filesystem tar" in html
        assert "Tar formats" in html

        # Check options
        assert "Yes, both filesystem and embedded" in html
        assert "Only embedded tar archives" in html
        assert ".tar and .tar.gz only" in html

        # Check descriptions
        assert "Treat .tar/.tar.gz like .zip" in html
        assert "Only process tar archives inside ZIP files" in html

        # Check question label
        assert "Q:" in html

        # Check select hint
        assert "(select one)" in html

    def test_format_askuserquestion_single_question(self):
        """Test AskUserQuestion formatting with a single question."""
        ask_input = AskUserQuestionInput(
            questions=[
                AskUserQuestionItem(
                    question="How should errors be reported?",
                    header="Error format",
                    options=[
                        AskUserQuestionOption(
                            label="Option A", description="Comment line"
                        ),
                        AskUserQuestionOption(
                            label="Option B", description="Marker entry"
                        ),
                        AskUserQuestionOption(
                            label="Option C", description="Extra field"
                        ),
                    ],
                    multiSelect=False,
                )
            ]
        )

        html = format_askuserquestion_input(ask_input)

        # Check structure
        assert 'class="askuserquestion-content"' in html
        assert "How should errors be reported?" in html
        assert "Error format" in html

        # Check all three options
        assert "Option A" in html
        assert "Option B" in html
        assert "Option C" in html

    def test_format_askuserquestion_multiselect(self):
        """Test AskUserQuestion formatting with multiSelect enabled."""
        ask_input = AskUserQuestionInput(
            questions=[
                AskUserQuestionItem(
                    question="Which features should be enabled?",
                    options=[
                        AskUserQuestionOption(label="Feature A"),
                        AskUserQuestionOption(label="Feature B"),
                        AskUserQuestionOption(label="Feature C"),
                    ],
                    multiSelect=True,
                )
            ]
        )

        html = format_askuserquestion_input(ask_input)

        # Check multi-select hint
        assert "(select multiple)" in html
        assert "Feature A" in html
        assert "Feature B" in html
        assert "Feature C" in html

    def test_format_askuserquestion_legacy_single_question(self):
        """Test backwards compatibility with single 'question' key format."""
        ask_input = AskUserQuestionInput(question="What is your preference?")

        html = format_askuserquestion_input(ask_input)

        # Should still render the question
        assert 'class="askuserquestion-content"' in html
        assert "What is your preference?" in html
        assert "Q:" in html

    def test_format_askuserquestion_no_options(self):
        """Test AskUserQuestion formatting without options."""
        ask_input = AskUserQuestionInput(
            questions=[
                AskUserQuestionItem(
                    question="Please describe the issue in detail.",
                    header="Issue",
                )
            ]
        )

        html = format_askuserquestion_input(ask_input)

        # Should render without options list
        assert "Please describe the issue in detail." in html
        assert "Issue" in html
        # Should not have options-related elements
        assert "question-options" not in html
        assert "(select" not in html

    def test_format_askuserquestion_empty_input(self):
        """Test AskUserQuestion with empty questions returns 'No question' message."""
        ask_input = AskUserQuestionInput()  # Empty questions list

        html = format_askuserquestion_input(ask_input)

        # Should show 'No question' message
        assert "askuserquestion-content" in html
        assert "No question" in html

    def test_format_askuserquestion_escapes_html(self):
        """Test that HTML special characters are escaped."""
        ask_input = AskUserQuestionInput(
            questions=[
                AskUserQuestionItem(
                    question="Use <script> tag or &amp; symbol?",
                    header="HTML <test>",
                    options=[
                        AskUserQuestionOption(
                            label="<option>", description="Test & verify"
                        )
                    ],
                )
            ]
        )

        html = format_askuserquestion_input(ask_input)

        # HTML entities should be escaped
        assert "&lt;script&gt;" in html
        assert "&lt;test&gt;" in html
        assert "&lt;option&gt;" in html
        # Input "&amp;" should be escaped to "&amp;amp;"
        assert "&amp;amp;" in html


class TestAskUserQuestionResultRendering:
    """Test AskUserQuestion tool result rendering functionality."""

    def test_format_result_single_qa(self):
        """Test formatting a result with a single Q&A pair."""
        content = (
            'User has answered your question: "What is your preference?"="Option A". '
            "You can now continue with the user's answers in mind."
        )

        html = format_askuserquestion_result(content)

        # Should render styled HTML
        assert 'class="askuserquestion-content askuserquestion-result"' in html
        assert 'class="question-block answered"' in html
        assert "What is your preference?" in html
        assert "Option A" in html
        assert "Q:" in html
        assert "A:" in html

    def test_format_result_multiple_qa(self):
        """Test formatting a result with multiple Q&A pairs."""
        content = (
            "User has answered your questions: "
            '"Should tar archives be processed recursively?"="Yes, both filesystem and embedded", '
            '"Which tar formats should be supported?"="Also .tgz". '
            "You can now continue with the user's answers in mind."
        )

        html = format_askuserquestion_result(content)

        # Should have two question blocks
        assert html.count('class="question-block answered"') == 2

        # Check both Q&A pairs
        assert "Should tar archives be processed recursively?" in html
        assert "Yes, both filesystem and embedded" in html
        assert "Which tar formats should be supported?" in html
        assert "Also .tgz" in html

    def test_format_result_not_answered(self):
        """Test that non-answer results return empty string."""
        content = "User cancelled the question."

        html = format_askuserquestion_result(content)

        # Should return empty to fall through to default handling
        assert html == ""

    def test_format_result_error_message(self):
        """Test that error messages return empty string."""
        content = "Error: Could not parse user response."

        html = format_askuserquestion_result(content)

        assert html == ""

    def test_format_result_escapes_html(self):
        """Test that HTML in questions/answers is escaped."""
        content = (
            'User has answered your question: "Use <script> tag?"="Yes, use <b>bold</b>". '
            "You can now continue with the user's answers in mind."
        )

        html = format_askuserquestion_result(content)

        # HTML should be escaped
        assert "&lt;script&gt;" in html
        assert "&lt;b&gt;bold&lt;/b&gt;" in html
        # Should not have raw HTML tags
        assert "<script>" not in html
        assert "<b>" not in html

    def test_format_result_malformed_no_closing(self):
        """Test handling of malformed result without closing sentence."""
        content = 'User has answered your question: "Q"="A"'

        html = format_askuserquestion_result(content)

        # Should return empty due to missing closing sentence
        assert html == ""

    def test_format_result_with_quotes_in_answer(self):
        """Test handling answers that might contain special characters."""
        # Note: This tests the regex pattern's ability to handle the format
        content = (
            'User has answered your question: "Preference?"="Option with comma, here". '
            "You can now continue with the user's answers in mind."
        )

        html = format_askuserquestion_result(content)

        assert "Preference?" in html
        assert "Option with comma, here" in html
