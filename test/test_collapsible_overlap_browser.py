"""Playwright regression tests for issue #153: a collapsible body whose
wrapper follows sibling content in a tool_result card must not overlap that
preceding content.

The `.tool_result .collapsible-code` rule applies a large negative top margin
to tuck the *first* collapsible up under the tool's header bar. When a
collapsible instead follows sibling content in the card body — a WebFetch
meta badge, an async-answer label, a plugin-emitted header line — that
pull-up used to drag the collapsible's first row up over the preceding
content. The fix cancels the merge for any collapsible whose wrapper is not
the first child of the card body; these tests assert the geometry directly.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import List

import pytest
from playwright.sync_api import Page

from claude_code_log.converter import load_transcript
from claude_code_log.html.renderer import generate_html
from claude_code_log.models import TranscriptEntry

# Bounding boxes carry sub-pixel/layout variance across environments; a small
# slack keeps these geometry assertions from flaking on CI without changing
# their intent (the real before/after gap is tens of pixels).
GEOMETRY_EPSILON_PX = 1.0


def _webfetch_entries() -> List[dict]:
    """A WebFetch tool_use → tool_result pair whose result is long enough
    (> 20 lines) to render as a collapsible, with a meta badge above it."""
    result_md = "\n".join(
        f"## Section {i}\n\nSome paragraph text describing section {i}."
        for i in range(1, 12)
    )
    return [
        {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00.000Z",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "s",
            "version": "1.0.0",
            "uuid": "a1",
            "message": {
                "role": "assistant",
                "id": "m1",
                "type": "message",
                "model": "claude",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "WebFetch",
                        "input": {
                            "url": "https://example.com/some/long/article/path",
                            "prompt": "Summarise this article",
                        },
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T10:00:02.000Z",
            "parentUuid": "a1",
            "isSidechain": False,
            "userType": "external",
            "cwd": "/tmp",
            "sessionId": "s",
            "version": "1.0.0",
            "uuid": "u1",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": result_md}
                ],
            },
            "toolUseResult": {
                "url": "https://example.com/some/long/article/path",
                "result": result_md,
                "bytes": 572826,
                "code": 200,
                "codeText": "OK",
                "durationMs": 1530,
            },
        },
    ]


class TestCollapsibleOverlapBrowser:
    """Live-browser geometry checks for issue #153."""

    def setup_method(self) -> None:
        self.temp_files: List[Path] = []

    def teardown_method(self) -> None:
        for f in self.temp_files:
            try:
                f.unlink()
            except FileNotFoundError:
                pass

    def _render(self, entries: List[dict], title: str = "Overlap Test") -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
            jsonl_path = Path(f.name)
        self.temp_files.append(jsonl_path)

        messages: List[TranscriptEntry] = load_transcript(jsonl_path)
        html_content = generate_html(messages, title)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html_content)
            html_path = Path(f.name)
        self.temp_files.append(html_path)
        return html_path

    @pytest.mark.browser
    def test_webfetch_meta_does_not_overlap_collapsible(self, page: Page) -> None:
        """The WebFetch meta badge (status / size / duration) must sit fully
        above the collapsible's summary — no vertical overlap (issue #153)."""
        html = self._render(_webfetch_entries())
        page.goto(f"file://{html}")

        meta = page.locator(".webfetch-meta").first
        summary = page.locator(".webfetch-result .collapsible-code summary").first

        meta_box = meta.bounding_box()
        summary_box = summary.bounding_box()
        assert meta_box is not None and summary_box is not None

        meta_bottom = meta_box["y"] + meta_box["height"]
        # The summary's top must be at or below the meta badge's bottom.
        # Before the fix the -2.5em pull-up dragged it ~30px above.
        assert summary_box["y"] >= meta_bottom - GEOMETRY_EPSILON_PX, (
            f"collapsible summary (top={summary_box['y']:.1f}) overlaps the "
            f"meta badge (bottom={meta_bottom:.1f})"
        )

    @pytest.mark.browser
    def test_first_collapsible_still_tucks_under_header(self, page: Page) -> None:
        """Guard the non-regression side: a Read result's collapsible has no
        preceding sibling, so it must KEEP the negative-margin merge that
        tucks its summary up into the header bar (overlapping it by design)."""
        readfile = "\n".join(f"def func_{i}():\n    return {i}" for i in range(1, 20))
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00.000Z",
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s",
                "version": "1.0.0",
                "uuid": "a1",
                "message": {
                    "role": "assistant",
                    "id": "m1",
                    "type": "message",
                    "model": "claude",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "/tmp/foo.py"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:02.000Z",
                "parentUuid": "a1",
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s",
                "version": "1.0.0",
                "uuid": "u1",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": readfile,
                        }
                    ],
                },
                "toolUseResult": {
                    "type": "text",
                    "file": {
                        "filePath": "/tmp/foo.py",
                        "content": readfile,
                        "numLines": 38,
                        "startLine": 1,
                        "totalLines": 38,
                    },
                },
            },
        ]
        html = self._render(entries)
        page.goto(f"file://{html}")

        header = page.locator(".message.tool_result .header").first
        summary = page.locator(".read-tool-result .collapsible-code summary").first

        header_box = header.bounding_box()
        summary_box = summary.bounding_box()
        assert header_box is not None and summary_box is not None

        header_bottom = header_box["y"] + header_box["height"]
        # The merge means the summary starts ABOVE the header's bottom edge.
        assert summary_box["y"] < header_bottom + GEOMETRY_EPSILON_PX, (
            "first collapsible no longer tucks under the header bar "
            f"(summary top={summary_box['y']:.1f}, header bottom={header_bottom:.1f})"
        )
