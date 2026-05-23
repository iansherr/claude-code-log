"""Tests for per-message timestamps in Markdown output (issue #160).

Exercises the date-on-change state machine in
``claude_code_log.markdown.renderer.MarkdownRenderer._format_message_timestamp``
and the `--no-timestamps` CLI flag.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from claude_code_log.converter import load_transcript
from claude_code_log.markdown.renderer import MarkdownRenderer


def _user_msg(uuid: str, session_id: str, timestamp: str, text: str) -> dict:
    """Build a minimal user-message transcript entry."""
    return {
        "type": "user",
        "timestamp": timestamp,
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "2.1.0",
        "uuid": uuid,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_msg(uuid: str, session_id: str, timestamp: str, text: str) -> dict:
    """Build a minimal assistant-message transcript entry."""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "parentUuid": None,
        "isSidechain": False,
        "userType": "assistant",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "2.1.0",
        "uuid": uuid,
        "requestId": f"req-{uuid}",
        "message": {
            "id": f"msg-{uuid}",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-4-7",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
        },
    }


def _render(
    entries: list[dict],
    no_timestamps: bool = False,
    compact: bool = False,
) -> str:
    """Write ``entries`` to a temp JSONL, load, render to Markdown."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
        path = Path(f.name)
    try:
        messages = load_transcript(path)
        renderer = MarkdownRenderer(no_timestamps=no_timestamps)
        renderer.compact = compact
        return renderer.generate(messages, title="t")
    finally:
        path.unlink()


def test_first_message_emits_full_date_and_time():
    """First message of a session re-emits its full date in bold backticks."""
    out = _render([_user_msg("u1", "s1", "2026-02-03T21:21:20.000Z", "hi")])
    assert "**`2026-02-03`** `21:21:20`" in out


def test_same_day_subsequent_messages_emit_time_only():
    """Subsequent same-day messages emit just the time component."""
    out = _render(
        [
            _user_msg("u1", "s1", "2026-02-03T21:21:20.000Z", "first"),
            _assistant_msg("u2", "s1", "2026-02-03T21:22:18.000Z", "second"),
            _user_msg("u3", "s1", "2026-02-03T21:30:00.000Z", "third"),
        ]
    )
    # Full date+time appears once (the first message).
    assert out.count("**`2026-02-03`**") == 1
    # Subsequent same-day timestamps appear as time-only lines.
    assert "`21:22:18`" in out
    assert "`21:30:00`" in out
    # And those time-only lines must NOT be paired with the date.
    assert "**`2026-02-03`** `21:22:18`" not in out
    assert "**`2026-02-03`** `21:30:00`" not in out


def test_day_rollover_re_emits_full_date():
    """When the calendar date advances, the next message carries the new date."""
    out = _render(
        [
            _user_msg("u1", "s1", "2026-02-03T23:59:50.000Z", "late"),
            _assistant_msg("u2", "s1", "2026-02-04T00:01:45.000Z", "rolled"),
        ]
    )
    assert "**`2026-02-03`** `23:59:50`" in out
    assert "**`2026-02-04`** `00:01:45`" in out


def test_compact_suppresses_timestamp_along_with_heading():
    """When `--compact` collapses a same-category heading, the
    timestamp line under it must collapse too — otherwise a body
    dangles behind a stray timestamp.

    Renders three same-category user messages; expects exactly one
    heading and exactly one timestamp (the first message's).
    """
    out = _render(
        [
            _user_msg("u1", "s1", "2026-02-03T10:00:00.000Z", "first"),
            _user_msg("u2", "s1", "2026-02-03T10:05:00.000Z", "second"),
            _user_msg("u3", "s1", "2026-02-03T10:10:00.000Z", "third"),
        ],
        compact=True,
    )
    # Exactly one user-heading line.
    user_heading_re = re.compile(r"^## .*User:", re.MULTILINE)
    assert len(user_heading_re.findall(out)) == 1
    # Exactly one per-message timestamp line — the first message's.
    # Bold-date line.
    assert out.count("**`2026-02-03`** `10:00:00`") == 1
    # No bare-time lines for the suppressed messages.
    assert "`10:05:00`" not in out
    assert "`10:10:00`" not in out


def test_compact_date_rollover_across_suppressed_messages():
    """When `--compact` suppresses several headings and the calendar
    date rolls over in the meantime, the next *unsuppressed* heading
    must carry the new date (not the stale one from before the
    rollover)."""
    out = _render(
        [
            _user_msg("u1", "s1", "2026-02-03T23:55:00.000Z", "pre-midnight"),
            # Same-category, suppressed; date rolls over here.
            _user_msg("u2", "s1", "2026-02-04T00:00:30.000Z", "rollover-suppressed"),
            _user_msg("u3", "s1", "2026-02-04T00:05:00.000Z", "rollover-suppressed-2"),
            # Different category breaks the compact run, so this
            # heading is emitted.
            _assistant_msg("u4", "s1", "2026-02-04T00:10:00.000Z", "next-day reply"),
        ],
        compact=True,
    )
    # First message keeps its pre-midnight date.
    assert "**`2026-02-03`** `23:55:00`" in out
    # The assistant heading after the rollover must carry the new
    # date — `_last_date_seen` should still reflect the *last
    # rendered* date (2026-02-03), so the rollover IS detected.
    assert "**`2026-02-04`** `00:10:00`" in out
    # Suppressed messages emit nothing.
    assert "`00:00:30`" not in out
    assert "`00:05:00`" not in out


def test_session_header_resets_last_date_seen_end_to_end():
    """A new session inside the same generate() must re-emit its
    first message's full date even when the calendar date is
    unchanged. Exercises the ``_render_message`` reset path that the
    helper unit test simulates with a manual ``_last_date_seen = None``.
    """
    out = _render(
        [
            _user_msg("u1", "s1", "2026-02-03T10:00:00.000Z", "s1-first"),
            _assistant_msg("u2", "s1", "2026-02-03T10:01:00.000Z", "s1-reply"),
            # New session, same calendar day — the first message of
            # session 2 must re-emit the date.
            _user_msg("u3", "s2", "2026-02-03T10:02:00.000Z", "s2-first"),
            _assistant_msg("u4", "s2", "2026-02-03T10:03:00.000Z", "s2-reply"),
        ]
    )
    # The same date appears twice in bold-backtick form — once per
    # session — even though both sessions are on the same calendar day.
    assert out.count("**`2026-02-03`**") == 2
    # And the in-session continuations are time-only.
    assert "`10:01:00`" in out
    assert "`10:03:00`" in out


def test_no_timestamps_suppresses_all_lines():
    """``--no-timestamps`` skips emission entirely."""
    out = _render(
        [
            _user_msg("u1", "s1", "2026-02-03T21:21:20.000Z", "first"),
            _assistant_msg("u2", "s1", "2026-02-03T21:22:18.000Z", "second"),
        ],
        no_timestamps=True,
    )
    assert "**`2026-02-03`**" not in out
    # No bare-time backtick lines either.
    assert "\n`21:" not in out


def test_format_message_timestamp_helper_state_machine():
    """Direct unit-test of the date-on-change helper, including the
    multi-session reset path (``_last_date_seen = None`` on
    ``SessionHeaderMessage``)."""
    from types import SimpleNamespace
    from typing import cast

    from claude_code_log.renderer import TemplateMessage

    r = MarkdownRenderer()
    r._last_date_seen = None

    def msg(ts: str) -> TemplateMessage:
        # Duck-typed minimal stand-in: the helper only touches
        # ``msg.meta.timestamp``.
        return cast(
            TemplateMessage, SimpleNamespace(meta=SimpleNamespace(timestamp=ts))
        )

    # First message → full date.
    line = r._format_message_timestamp(msg("2026-02-03T10:00:00.000Z"))
    assert line == "**`2026-02-03`** `10:00:00`"

    # Same-day → time only.
    line = r._format_message_timestamp(msg("2026-02-03T10:05:00.000Z"))
    assert line == "`10:05:00`"

    # Day rollover → full date.
    line = r._format_message_timestamp(msg("2026-02-04T00:01:00.000Z"))
    assert line == "**`2026-02-04`** `00:01:00`"

    # Simulate session-header reset: even though the date is identical
    # to what we just emitted, the first message of the new session
    # must re-emit it.
    r._last_date_seen = None
    line = r._format_message_timestamp(msg("2026-02-04T00:02:00.000Z"))
    assert line == "**`2026-02-04`** `00:02:00`"


def test_format_message_timestamp_returns_empty_on_missing_or_unparseable():
    """Empty / unparseable timestamps yield ``""`` so the caller can
    append unconditionally."""
    from types import SimpleNamespace
    from typing import cast

    from claude_code_log.renderer import TemplateMessage

    def stub(ts: str) -> TemplateMessage:
        return cast(
            TemplateMessage, SimpleNamespace(meta=SimpleNamespace(timestamp=ts))
        )

    r = MarkdownRenderer()
    r._last_date_seen = None
    assert r._format_message_timestamp(stub("")) == ""
    # ``format_timestamp`` returns the raw string on parse failure;
    # without a space, the helper must bail out instead of producing
    # malformed output.
    assert r._format_message_timestamp(stub("not-a-timestamp")) == ""
    # Regression for monk's review on #165: a raw fallback string
    # that happens to contain a space (e.g. ``"not a timestamp"``)
    # used to slip past the presence-of-space check and produce
    # garbage like ``**`not`** `a timestamp` ``. The shape-validating
    # regex rejects it.
    assert r._format_message_timestamp(stub("not a timestamp")) == ""


def test_variant_suffix_no_timestamps_markdown_only():
    """`--no-timestamps` participates in the filename infix only for
    Markdown output, so toggling it produces a distinct
    `combined_transcripts.no-timestamps.md` and per-session file. HTML
    / JSON don't honour the flag (a warning is emitted), so the
    suffix stays empty for them and we avoid orphaned variant files.

    Regression for CodeRabbit's finding on PR #165: previously the
    flag was not part of the variant key, so the path-existence /
    cache lookup treated the prior export as up-to-date and skipped
    regeneration when the user toggled the flag.
    """
    from claude_code_log.utils import variant_suffix

    # Default → empty.
    assert variant_suffix() == ""
    # Markdown + no_timestamps → distinct suffix.
    assert variant_suffix(format="md", no_timestamps=True) == ".no-timestamps"
    assert variant_suffix(format="markdown", no_timestamps=True) == ".no-timestamps"
    # Composable with `--compact` (markdown only).
    assert (
        variant_suffix(format="md", compact=True, no_timestamps=True)
        == ".compact.no-timestamps"
    )
    # HTML / JSON ignore the flag (no orphaned `.no-timestamps.html`).
    assert variant_suffix(format="html", no_timestamps=True) == ""
    assert variant_suffix(format="json", no_timestamps=True) == ""


@pytest.mark.parametrize("fmt", ["html", "json"])
def test_no_timestamps_with_non_markdown_format_warns_not_errors(
    fmt: str, tmp_path: Path
):
    """`--no-timestamps` paired with --format html|json should warn
    (stderr) but not error (exit 0). Matches the existing
    `--expand-paths` / `--filter-path` warning contract."""
    from click.testing import CliRunner

    from claude_code_log.cli import main

    # Minimal valid JSONL project (1 user message).
    project = tmp_path / "proj"
    project.mkdir()
    (project / "session.jsonl").write_text(
        json.dumps(_user_msg("u1", "s1", "2026-02-03T21:21:20.000Z", "hi")) + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            str(project),
            "--no-cache",
            "--no-timestamps",
            "--format",
            fmt,
        ],
    )
    assert result.exit_code == 0, result.output
    # Warning lands on stderr in production but the test runner mixes
    # streams by default; assert on the combined output.
    assert "--no-timestamps is Markdown-only" in result.output
