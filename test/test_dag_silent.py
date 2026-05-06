"""Cycle / multi-root / orphan DAG warnings respect ``silent=True``.

These diagnostics are routed through Python's ``logging`` so debug runs
can still see them, but cache-rebuild and quiet CLI flows shouldn't
emit warnings on every load.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from claude_code_log.converter import load_directory_transcripts


def _write_session(path: Path, sid: str, entries: list[dict[str, object]]) -> None:
    """Write JSONL entries to a session file."""
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _orphan_session(sid: str) -> list[dict[str, object]]:
    """A session with a user message whose parentUuid points nowhere —
    triggers the "Orphan node" warning during DAG construction."""
    return [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": "missing-parent-uuid",
            "sessionId": sid,
            "timestamp": "2026-05-05T10:00:00.000Z",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "version": "0.0.0",
            "message": {"role": "user", "content": "hi"},
        }
    ]


class TestDagWarningsSilent:
    def test_warnings_emitted_when_not_silent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_session(proj / "s.jsonl", "s1", _orphan_session("s1"))

        with caplog.at_level(logging.WARNING, logger="claude_code_log.dag"):
            load_directory_transcripts(proj, silent=False)

        assert any("Orphan node" in r.message for r in caplog.records)

    def test_warnings_suppressed_when_silent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_session(proj / "s.jsonl", "s1", _orphan_session("s1"))

        with caplog.at_level(logging.WARNING, logger="claude_code_log.dag"):
            load_directory_transcripts(proj, silent=True)

        assert not any(
            "Orphan node" in r.message
            for r in caplog.records
            if r.name == "claude_code_log.dag"
        )

    def test_silent_restores_previous_log_level(self, tmp_path: Path) -> None:
        """The context manager must restore the dag logger level after use."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_session(proj / "s.jsonl", "s1", _orphan_session("s1"))

        dag_logger = logging.getLogger("claude_code_log.dag")
        original_level = dag_logger.level
        load_directory_transcripts(proj, silent=True)
        assert dag_logger.level == original_level
