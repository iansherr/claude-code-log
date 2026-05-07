"""Tests for away_summary (recap) parsing and rendering — issue #111."""

from claude_code_log.factories import create_transcript_entry
from claude_code_log.factories.system_factory import create_system_message
from claude_code_log.html.renderer import generate_html
from claude_code_log.models import AwaySummaryMessage, SystemTranscriptEntry


# Real recap payload from issue #111. Kept inline so the test self-documents
# the JSONL shape; a fixture file lives at test/test_data/away_summary.jsonl
# for end-to-end / cache exploration.
AWAY_SUMMARY_RAW: dict = {
    "parentUuid": "0d4221bb-4b34-42eb-828a-8892e725be2b",
    "isSidechain": False,
    "userType": "external",
    "entrypoint": "cli",
    "cwd": "/app",
    "sessionId": "4520f070-9e99-41bb-9400-2efd7eda4632",
    "version": "2.1.110",
    "gitBranch": "asdf",
    "slug": "vectorized-giggling-sedgewick",
    "type": "system",
    "subtype": "away_summary",
    "content": (
        "We're adding a project-level layout to validate projectId in "
        "the prepare route tree and redirect on invalid IDs. I just "
        "created the layout file and was about to run type-checking "
        "and test it."
    ),
    "timestamp": "2026-04-16T11:52:02.108Z",
    "uuid": "e2066dc9-672a-48ff-b6c9-df29103572bf",
    "isMeta": False,
}


class TestAwaySummaryParsing:
    """Parsing of away_summary system entries."""

    def test_parse_away_summary_entry(self):
        entry = create_transcript_entry(AWAY_SUMMARY_RAW)
        assert isinstance(entry, SystemTranscriptEntry)
        assert entry.subtype == "away_summary"
        assert entry.content is not None
        assert "project-level layout" in entry.content

    def test_factory_produces_away_summary_message(self):
        entry = create_transcript_entry(AWAY_SUMMARY_RAW)
        assert isinstance(entry, SystemTranscriptEntry)
        content = create_system_message(entry)
        assert isinstance(content, AwaySummaryMessage)
        assert "project-level layout" in content.text
        # Content type contract: AwaySummaryMessage shares the "system"
        # type label so existing system-level filtering still applies.
        assert content.message_type == "system"
        # Recaps may include light markdown — has_markdown=True so the
        # template wraps the content div with the .markdown class.
        assert content.has_markdown is True

    def test_factory_skips_empty_away_summary(self):
        """An away_summary with no content is skipped (defensive — shouldn't
        happen in practice, but matches the existing empty-content guard)."""
        empty = dict(AWAY_SUMMARY_RAW)
        empty["content"] = ""
        entry = create_transcript_entry(empty)
        assert isinstance(entry, SystemTranscriptEntry)
        assert create_system_message(entry) is None

    def test_factory_strips_trailing_ui_hint(self):
        """Claude Code appends ' (disable recaps in /config)' to the recap
        body — useful in a live TUI, pure noise in a rendered transcript.
        Strip at the factory seam so HTML / Markdown / JSON renderers all
        inherit the polished form. Suffix-match (not global) so the phrase
        is still preserved if it appears inside a recap body."""
        with_hint = dict(AWAY_SUMMARY_RAW)
        with_hint["content"] = (
            AWAY_SUMMARY_RAW["content"] + " (disable recaps in /config)"
        )
        entry = create_transcript_entry(with_hint)
        assert isinstance(entry, SystemTranscriptEntry)
        content = create_system_message(entry)
        assert isinstance(content, AwaySummaryMessage)
        assert "(disable recaps in /config)" not in content.text
        # The original recap body survived the strip — we only trimmed the suffix.
        assert "project-level layout" in content.text
        # End-to-end: the rendered HTML doesn't carry the hint either.
        html = generate_html([entry])
        assert "(disable recaps in /config)" not in html
        assert "project-level layout" in html


class TestAwaySummaryRendering:
    """End-to-end HTML rendering."""

    def test_renders_with_recap_label_and_class(self):
        entry = create_transcript_entry(AWAY_SUMMARY_RAW)
        html = generate_html([entry])
        # The 📝 Recap header chrome from format_away_summary_content.
        assert "📝 Recap" in html
        # CSS modifier — present in both message div and the registry-driven
        # message_styles.css rules; absence indicates the registry wiring broke.
        assert "system-away-summary" in html
        # The recap text itself made it through.
        assert "project-level layout" in html

    def test_does_not_render_as_xml_soup(self):
        """Issue #111's minimum bar: don't render as raw XML soup or drop
        the entry on the floor. Verifies we don't accidentally fall through
        to the SystemMessage path."""
        entry = create_transcript_entry(AWAY_SUMMARY_RAW)
        html = generate_html([entry])
        # No raw subtype / XML leaking into the output.
        assert "away_summary" not in html
        assert "&lt;system&gt;" not in html
        # The recap div carries our modifier, not a level modifier — confirms
        # the away_summary subtype branch fired instead of falling through to
        # the level-bearing SystemMessage path (which would have stamped
        # `system-info` onto the message div).
        assert "system-away-summary" in html
        assert 'class="message system system-info' not in html

    def test_loads_jsonl_fixture(self, test_data_dir):
        """Smoke test: the example payload from the issue, loaded from disk
        end-to-end via the same path as production."""
        from claude_code_log.converter import load_transcript

        messages = load_transcript(test_data_dir / "away_summary.jsonl")
        html = generate_html(messages)
        assert "📝 Recap" in html
        assert "project-level layout" in html

    def test_recap_label_appears_once(self):
        """The 'Recap' label lives in the message header (icon + title), not
        twice (header + inline body chrome). Regression guard for the duplicate
        '⚙️ Recap' / '📝 Recap' pair monk caught in review."""
        entry = create_transcript_entry(AWAY_SUMMARY_RAW)
        html = generate_html([entry])
        # Header reads "📝 Recap" — exactly once. The body is plain markdown
        # without an inline <strong>📝 Recap</strong> tag.
        assert html.count("Recap") == 1
        # The generic system "⚙️" must not be applied to recaps; the emoji
        # helper picks "📝" specifically for AwaySummaryMessage.
        assert "⚙️ Recap" not in html
        assert "📝 Recap" in html


class TestAwaySummaryDetailLevels:
    """Detail-level filtering: recaps are content (kept at HIGH), not noise
    (dropped at LOW and below — same tier as bash/thinking).

    The CSS rules for `.system-away-summary` ship with every page regardless
    of detail level, so these tests check whether a recap *message div* is
    present (`class='message ...system-away-summary` substring) rather than
    just the bare modifier name."""

    # Message-div presence marker. The transcript template emits each message
    # as `<div class='message {csses} ...' ...>`; for an AwaySummaryMessage,
    # `system-away-summary` is in {csses}, so this substring is unique to
    # rendered recap entries (vs. the standalone CSS rule selectors).
    RECAP_DIV_MARKER = "message system system-away-summary"

    def _render_at(self, detail):
        """Render the fixture at a given detail level and return the HTML."""
        from claude_code_log.html.renderer import HtmlRenderer

        entry = create_transcript_entry(AWAY_SUMMARY_RAW)
        renderer = HtmlRenderer()
        renderer.detail = detail
        return renderer.generate([entry], "Detail Test")

    def test_full_keeps_recap(self):
        from claude_code_log.models import DetailLevel

        html = self._render_at(DetailLevel.FULL)
        assert self.RECAP_DIV_MARKER in html
        assert "project-level layout" in html

    def test_high_keeps_recap(self):
        """Monk's #1 review note: recap is narrative content, must survive
        the 'detailed but cleaned' HIGH level."""
        from claude_code_log.models import DetailLevel

        html = self._render_at(DetailLevel.HIGH)
        assert self.RECAP_DIV_MARKER in html
        assert "project-level layout" in html

    def test_low_drops_recap(self):
        """LOW is interaction-focused; recap (background narrative) is
        dropped here alongside bash/thinking."""
        from claude_code_log.models import DetailLevel

        html = self._render_at(DetailLevel.LOW)
        assert self.RECAP_DIV_MARKER not in html
        assert "project-level layout" not in html

    def test_minimal_drops_recap(self):
        from claude_code_log.models import DetailLevel

        html = self._render_at(DetailLevel.MINIMAL)
        assert self.RECAP_DIV_MARKER not in html

    def test_user_only_drops_recap(self):
        from claude_code_log.models import DetailLevel

        html = self._render_at(DetailLevel.USER_ONLY)
        assert self.RECAP_DIV_MARKER not in html
