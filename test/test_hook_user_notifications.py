"""Tests for filtering hook-injected synthetic user turns.

External tooling (clmail, clmail-monitor, ...) uses Claude Code's
``UserPromptSubmit`` hook to inject single-line notifications such as
``[monitor] alice idle`` or ``[clmail] You've got a new mail (#3017)``.
These arrive in the JSONL as full ``type: user`` entries; the renderer
must recognise them, render them compactly at ``DetailLevel.FULL``, and
drop them entirely at ``HIGH`` and below.
"""

from pathlib import Path

from claude_code_log.converter import load_transcript
from claude_code_log.factories.user_factory import detect_hook_notification
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import DetailLevel, UserHookNotificationMessage


FIXTURE = Path(__file__).parent / "test_data" / "hook_user_notifications.jsonl"


# -----------------------------------------------------------------------------
# detect_hook_notification — content-prefix detection
# -----------------------------------------------------------------------------


class TestDetectHookNotification:
    """Direct tests for the bracket-prefix detector."""

    def test_monitor_one_liner_matches(self):
        assert detect_hook_notification("[monitor] alice idle") == (
            "monitor",
            "alice idle",
        )

    def test_clmail_with_punctuation_matches(self):
        assert detect_hook_notification("[clmail] You've got a new mail (#3017)") == (
            "clmail",
            "You've got a new mail (#3017)",
        )

    def test_leading_whitespace_tolerated(self):
        assert detect_hook_notification("  [monitor] foo") == ("monitor", "foo")

    def test_real_user_prompt_not_matched(self):
        assert detect_hook_notification("Hello, please run the tests.") is None

    def test_image_placeholder_not_matched(self):
        assert detect_hook_notification("[Image #1]") is None

    def test_interruption_marker_not_matched(self):
        assert detect_hook_notification("[Request interrupted by user]") is None

    def test_file_reference_not_matched(self):
        assert detect_hook_notification("[@filename.md]") is None

    def test_multiline_with_hook_prefix_not_matched(self):
        # A user typing "[monitor] foo\n\nActually do X" must be preserved.
        text = "[monitor] alice idle\n\nActually, please continue with the next step."
        assert detect_hook_notification(text) is None


# -----------------------------------------------------------------------------
# Factory: typed content is produced
# -----------------------------------------------------------------------------


class TestFactoryProducesTypedContent:
    """The user_factory must wrap hook one-liners in UserHookNotificationMessage."""

    def test_hook_turns_become_typed_content(self):
        from claude_code_log.factories.user_factory import create_user_message
        from claude_code_log.models import MessageMeta, UserTranscriptEntry
        from claude_code_log.parser import extract_text_content

        entries = load_transcript(FIXTURE)
        hook_messages: list[UserHookNotificationMessage] = []
        for entry in entries:
            if not isinstance(entry, UserTranscriptEntry):
                continue
            text = extract_text_content(entry.message.content)
            meta = MessageMeta(
                session_id=entry.sessionId,
                uuid=entry.uuid,
                parent_uuid=entry.parentUuid or "",
                timestamp=entry.timestamp,
                is_sidechain=entry.isSidechain,
            )
            content = create_user_message(meta, entry.message.content, text)
            if isinstance(content, UserHookNotificationMessage):
                hook_messages.append(content)

        # Fixture contains 3 plain hook one-liners; the multi-line
        # "[monitor] ... \n\nActually" entry must NOT be wrapped.
        sources_and_bodies = [(m.source, m.text) for m in hook_messages]
        assert ("monitor", "alice idle") in sources_and_bodies
        assert ("clmail", "You've got a new mail (#3017)") in sources_and_bodies
        assert (
            "monitor",
            "alice still idle after 2 minutes (check, unblock, or unmonitor)",
        ) in sources_and_bodies
        assert len(hook_messages) == 3


# -----------------------------------------------------------------------------
# Renderer: filtered at HIGH and below, kept at FULL
# -----------------------------------------------------------------------------


def _render_markdown(detail: DetailLevel) -> str:
    renderer = MarkdownRenderer()
    renderer.detail = detail
    entries = load_transcript(FIXTURE)
    return renderer.generate(entries, title="Hook Filter Fixture")


def _render_html(detail: DetailLevel) -> str:
    renderer = HtmlRenderer()
    renderer.detail = detail
    entries = load_transcript(FIXTURE)
    return renderer.generate(entries, title="Hook Filter Fixture")


class TestMarkdownFiltering:
    def test_full_keeps_hook_lines_compact(self):
        md = _render_markdown(DetailLevel.FULL)
        # The clmail line only appears as a pure hook in the fixture —
        # safe content marker for the standalone-italic rendering.
        assert "*[clmail] You've got a new mail (#3017)*" in md
        # That line must NOT carry a ``## 🤷 User`` heading.
        assert "🤷 User: *[clmail]" not in md
        # The clmail body must NOT appear as a heading excerpt either.
        assert "🤷 User: *You've got a new mail" not in md
        # The "still idle after 2 minutes" body is also hook-only in
        # the fixture; expect the standalone italic form.
        assert (
            "*[monitor] alice still idle after 2 minutes "
            "(check, unblock, or unmonitor)*"
        ) in md
        # Real user turns still render with full headings.
        assert "🤷 User: *Real user turn" in md
        assert "🤷 User: *Real follow-up prompt" in md

    def test_high_drops_hook_lines(self):
        md = _render_markdown(DetailLevel.HIGH)
        # Pure hook one-liners are gone (clmail / "still idle after 2
        # minutes" are hook-only bodies).
        assert "[clmail]" not in md
        assert "still idle after 2 minutes" not in md
        # Real user content survives, including the multi-line
        # "[monitor] ... \n\nActually" prompt (a real human turn that
        # happens to start with the recognised bracket prefix).
        assert "Real user turn" in md
        assert "Real follow-up prompt" in md
        assert "Actually, please continue" in md

    def test_low_drops_hook_lines(self):
        md = _render_markdown(DetailLevel.LOW)
        assert "[clmail]" not in md
        assert "still idle after 2 minutes" not in md
        assert "Real user turn" in md


class TestHtmlFiltering:
    def test_full_renders_compact_marker(self):
        html = _render_html(DetailLevel.FULL)
        # The rendered message element carries the ``hook-notification``
        # class (alongside the ``message user`` base and a ``d-N``
        # depth-ancestry suffix). Anchor on the start of the class
        # attribute to avoid matching the bare class name in the inline
        # <style> block.
        assert "class='message user hook-notification " in html
        # The clmail body is unique to the hook fixture entries.
        assert "You&#x27;ve got a new mail (#3017)" in html

    def test_high_drops_hook_notifications(self):
        html = _render_html(DetailLevel.HIGH)
        # No rendered hook-notification message blocks remain (the CSS
        # rule is still in the <style> head; class-on-element check
        # avoids that false-positive).
        assert "class='message user hook-notification " not in html
        # Clmail body is unique to hook entries — must be gone.
        assert "You&#x27;ve got a new mail (#3017)" not in html
        assert "still idle after 2 minutes" not in html
        # Real content survives.
        assert "Real user turn" in html
        assert "Real follow-up prompt" in html
