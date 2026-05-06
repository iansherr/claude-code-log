"""Tests for `ai-title` JSONL entry handling.

`ai-title` lines are session-level metadata (no uuid, no timestamp) that
Claude Code emits to record an AI-generated short title for the session.
Multiple entries may appear per session as the title is refined; the
last one wins. They participate in the existing session-title selection
chain (`build_session_title`) and override `summary` for display.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_code_log.converter import (
    build_session_title,
    deduplicate_messages,
    load_transcript,
)
from claude_code_log.cache import CacheManager, SessionCacheData
from claude_code_log.models import AiTitleTranscriptEntry


def _write_jsonl(path: Path, entries: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestAiTitleParsing:
    def test_parsed_as_dedicated_entry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """ai-title is a known type — parses cleanly with no warning."""
        jsonl = tmp_path / "s.jsonl"
        _write_jsonl(
            jsonl,
            [
                {
                    "type": "ai-title",
                    "aiTitle": "Activate Python virtual environment",
                    "sessionId": "327fac9d-8b0b-4f8a-88c7-d8fea5e354d3",
                }
            ],
        )

        messages = load_transcript(jsonl, silent=False)
        captured = capsys.readouterr()

        assert len(messages) == 1
        entry = messages[0]
        assert isinstance(entry, AiTitleTranscriptEntry)
        assert entry.aiTitle == "Activate Python virtual environment"
        assert entry.sessionId == "327fac9d-8b0b-4f8a-88c7-d8fea5e354d3"
        assert "unrecognized message type" not in captured.out

    def test_multiple_entries_collapsed_to_last(self, tmp_path: Path) -> None:
        """Multiple ai-title entries per session collapse to the latest one
        after deduplication, since Claude Code may refine the title."""
        jsonl = tmp_path / "s.jsonl"
        _write_jsonl(
            jsonl,
            [
                {"type": "ai-title", "aiTitle": "First draft", "sessionId": "s1"},
                {"type": "ai-title", "aiTitle": "Second draft", "sessionId": "s1"},
                {"type": "ai-title", "aiTitle": "Final title", "sessionId": "s1"},
            ],
        )

        messages = load_transcript(jsonl, silent=True)
        deduped = deduplicate_messages(messages)
        ai_titles = [m for m in deduped if isinstance(m, AiTitleTranscriptEntry)]

        assert len(ai_titles) == 1
        assert ai_titles[0].aiTitle == "Final title"

    def test_distinct_sessions_kept_separately(self, tmp_path: Path) -> None:
        """Different sessions each keep their own ai-title."""
        jsonl = tmp_path / "s.jsonl"
        _write_jsonl(
            jsonl,
            [
                {"type": "ai-title", "aiTitle": "Title A", "sessionId": "sA"},
                {"type": "ai-title", "aiTitle": "Title B", "sessionId": "sB"},
                {"type": "ai-title", "aiTitle": "Title A v2", "sessionId": "sA"},
            ],
        )

        messages = load_transcript(jsonl, silent=True)
        deduped = deduplicate_messages(messages)
        titles_by_session = {
            m.sessionId: m.aiTitle
            for m in deduped
            if isinstance(m, AiTitleTranscriptEntry)
        }

        assert titles_by_session == {"sA": "Title A v2", "sB": "Title B"}


class TestBuildSessionTitlePriority:
    """``build_session_title`` priority: ai_title > summary > preview > id."""

    def _make(self, **overrides: object) -> SessionCacheData:
        defaults: dict[str, object] = {
            "session_id": "abc12345",
            "first_timestamp": "",
            "last_timestamp": "",
            "message_count": 0,
            "first_user_message": "",
        }
        defaults.update(overrides)
        return SessionCacheData(**defaults)  # type: ignore[arg-type]

    def test_ai_title_wins_over_summary(self) -> None:
        cache = self._make(
            ai_title="Curated AI title",
            summary="Long-form summary",
            first_user_message="Some preview",
        )
        assert (
            build_session_title("Project", "abc12345", cache)
            == "Project: Curated AI title"
        )

    def test_summary_wins_when_no_ai_title(self) -> None:
        cache = self._make(
            summary="Long-form summary", first_user_message="Some preview"
        )
        assert (
            build_session_title("Project", "abc12345", cache)
            == "Project: Long-form summary"
        )

    def test_preview_used_when_only_user_message(self) -> None:
        cache = self._make(first_user_message="Some preview")
        assert (
            build_session_title("Project", "abc12345", cache) == "Project: Some preview"
        )

    def test_session_id_fallback_when_cache_empty(self) -> None:
        cache = self._make()
        assert (
            build_session_title("Project", "abc12345", cache)
            == "Project: Session abc12345"
        )

    def test_no_cache_falls_back_to_session_id(self) -> None:
        assert (
            build_session_title("Project", "abc12345", None)
            == "Project: Session abc12345"
        )


class TestAiTitleCacheRoundTrip:
    """Persisting ai_title through SQLite must survive a reload — guards
    against schema/binding regressions in update_session_cache and the
    SELECT in get_cached_project_data."""

    def test_ai_title_persisted_and_reloaded(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        with patch(
            "claude_code_log.cache.get_library_version", return_value="1.0.0-test"
        ):
            writer = CacheManager(project_dir, "1.0.0-test")
            writer.update_session_cache(
                {
                    "s1": SessionCacheData(
                        session_id="s1",
                        ai_title="Saved AI title",
                        first_timestamp="2026-05-05T10:00:00Z",
                        last_timestamp="2026-05-05T10:05:00Z",
                        message_count=1,
                        first_user_message="hi",
                    )
                }
            )

            # Fresh manager forces a SELECT path through SQLite, not memory.
            reader = CacheManager(project_dir, "1.0.0-test")
            cached = reader.get_cached_project_data()

        assert cached is not None
        assert "s1" in cached.sessions
        reloaded = cached.sessions["s1"]
        assert reloaded.ai_title == "Saved AI title"
        assert (
            build_session_title("Project", "s1", reloaded) == "Project: Saved AI title"
        )
