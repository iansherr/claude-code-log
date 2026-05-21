"""Unit tests for the path-projection helpers (issue #151).

Covers ``project_dir_to_real_path`` (three-tier resolution: cache →
JSONL peek → naive last-resort) and ``project_destination`` (the
flag-interaction matrix from ``work/obsidian-friendly-output.md``).
"""

import json
from pathlib import Path

import pytest

from claude_code_log.utils import (
    output_path_is_file,
    project_destination,
    project_dir_to_real_path,
)


def _write_jsonl_with_cwd(jsonl_path: Path, cwd: str) -> None:
    """Write a minimal JSONL line carrying a `cwd` field — enough to
    exercise the JSONL-peek tier of `project_dir_to_real_path`."""
    entry = {
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": "11111111-1111-1111-1111-111111111111",
        "version": "2.1.0",
        "type": "user",
        "uuid": "22222222-2222-2222-2222-222222222222",
        "timestamp": "2026-05-10T10:00:00.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "hi"}],
        },
    }
    jsonl_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------
# project_dir_to_real_path
# -----------------------------------------------------------------------------


class TestProjectDirToRealPath:
    """Three-tier resolution: cache → JSONL peek → naive last-resort."""

    def test_uses_cache_cwd(self, tmp_path: Path):
        """Tier 1: when cached_working_directories is supplied, the
        first absolute entry wins."""
        result = project_dir_to_real_path(
            tmp_path / "-anything",
            cached_working_directories=["/home/joe/x/y"],
        )
        assert result == Path("/home/joe/x/y")

    def test_skips_relative_cache_entries(self, tmp_path: Path):
        """Tier 1 absoluteness guard: relative `cwd` values fall
        through (test fixtures sometimes carry these)."""
        project_dir = tmp_path / "-skipped"
        project_dir.mkdir()
        # Relative cache value should be rejected; with no JSONLs to
        # peek, falls through to naive last-resort.
        result = project_dir_to_real_path(
            project_dir,
            cached_working_directories=["relative-not-absolute"],
        )
        # Naive: -skipped → /skipped
        assert result == Path("/skipped")

    def test_skips_temp_paths_in_cache(self, tmp_path: Path):
        """Tier 1: temp paths (/tmp/, macOS /private/var/folders/)
        are filtered out — they're not the user's authoritative cwd."""
        project_dir = tmp_path / "-orphan"
        project_dir.mkdir()
        result = project_dir_to_real_path(
            project_dir,
            cached_working_directories=["/tmp/pytest-of-cboos/xyz"],
        )
        # Filter dropped the /tmp/ entry → naive last-resort.
        assert result == Path("/orphan")

    def test_peeks_jsonl_when_no_cache(self, tmp_path: Path):
        """Tier 2: with no cache, the first JSONL's `cwd` is read."""
        project_dir = tmp_path / "-home-joe-x-y"
        project_dir.mkdir()
        _write_jsonl_with_cwd(project_dir / "session.jsonl", "/home/joe/x/y")
        result = project_dir_to_real_path(project_dir)
        assert result == Path("/home/joe/x/y")

    def test_peek_disambiguates_cache_collision(self, tmp_path: Path):
        """Two `-home-joe-x-y` dirs with different real cwds: each
        resolves correctly because the cache (or JSONL) is consulted."""
        # Same encoded name, different cwds → different real paths.
        cache_a = ["/home/joe/x/y"]  # subdir interpretation
        cache_b = ["/home/joe/x-y"]  # single-dir interpretation
        result_a = project_dir_to_real_path(
            Path("/anywhere/-home-joe-x-y"),
            cached_working_directories=cache_a,
        )
        result_b = project_dir_to_real_path(
            Path("/anywhere/-home-joe-x-y"),
            cached_working_directories=cache_b,
        )
        assert result_a == Path("/home/joe/x/y")
        assert result_b == Path("/home/joe/x-y")

    def test_peek_skips_agent_files(self, tmp_path: Path):
        """`agent-*.jsonl` files (sidechains) are skipped during peek
        because they may not carry the project's top-level cwd."""
        project_dir = tmp_path / "-peek-test"
        project_dir.mkdir()
        # Agent file FIRST alphabetically — would be picked if not
        # skipped. Real session JSONL has the right cwd.
        _write_jsonl_with_cwd(project_dir / "agent-aaaa.jsonl", "/wrong/path")
        _write_jsonl_with_cwd(project_dir / "session-bbbb.jsonl", "/right/path")
        result = project_dir_to_real_path(project_dir)
        assert result == Path("/right/path")

    @pytest.mark.parametrize(
        "encoded,expected",
        [
            ("-home-cboos-bin", "/home/cboos/bin"),
            # Double-dash → leading-dot dir component (`/.foo`).
            ("-home-cboos--claude", "/home/cboos/.claude"),
            (
                "-home-cboos-Documents-Obsidian-Work--git",
                "/home/cboos/Documents/Obsidian/Work/.git",
            ),
            ("-home-joe-project-A", "/home/joe/project/A"),
        ],
    )
    def test_naive_last_resort(self, tmp_path: Path, encoded: str, expected: str):
        """Tier 3: no cache, no JSONLs, no fallback file. Naive
        `/`-for-`-` inversion with `--` → `/.` for dotfile dirs.
        Sampled from real `~/.claude/projects/` corpus."""
        project_dir = tmp_path / encoded
        # Don't mkdir — `is_dir()` returns False, so peek tier is
        # skipped and we go straight to naive.
        result = project_dir_to_real_path(project_dir)
        assert result == Path(expected)


# -----------------------------------------------------------------------------
# project_destination — the flag interaction matrix
# -----------------------------------------------------------------------------


class TestProjectDestination:
    """Per-project destination logic. Six matrix rows."""

    SRC = Path("/proj/-home-joe-project-A")
    OUT = Path("/tmp/obsidian")

    def test_legacy_no_output_dir(self):
        """No `--output` → write into the source dir (current
        behaviour — strict backwards compatibility)."""
        dest = project_destination(
            self.SRC,
            output_dir=None,
            expand_paths=False,
            filter_path=None,
        )
        assert dest == self.SRC

    def test_flat_copy(self):
        """`--output` only → flat copy under output_dir, project
        keeps its encoded name. Closes the previously-implicit gap
        where `--output` was silently ignored in `--all-projects`."""
        dest = project_destination(
            self.SRC,
            output_dir=self.OUT,
            expand_paths=False,
            filter_path=None,
        )
        assert dest == self.OUT / "-home-joe-project-A"

    def test_expand_no_filter(self):
        """`--output --expand-paths` → full real-path expansion
        under output_dir."""
        dest = project_destination(
            self.SRC,
            output_dir=self.OUT,
            expand_paths=True,
            filter_path=None,
            cached_working_directories=["/home/joe/project/A"],
        )
        assert dest == self.OUT / "home/joe/project/A"

    def test_expand_filter_match(self):
        """`--expand-paths --filter-path /home/joe`: filter against
        real path, truncate the prefix from the destination."""
        dest = project_destination(
            self.SRC,
            output_dir=self.OUT,
            expand_paths=True,
            filter_path="/home/joe",
            cached_working_directories=["/home/joe/project/A"],
        )
        assert dest == self.OUT / "project/A"

    def test_expand_filter_miss(self):
        """When the real path doesn't start with the filter prefix,
        the project is excluded (returns None)."""
        dest = project_destination(
            self.SRC,
            output_dir=self.OUT,
            expand_paths=True,
            filter_path="/home/jane",  # different user
            cached_working_directories=["/home/joe/project/A"],
        )
        assert dest is None

    def test_filter_match_flat(self):
        """`--filter-path` without `--expand-paths` matches the flat
        encoded dir name (per Q2 resolution); no truncation."""
        dest = project_destination(
            self.SRC,
            output_dir=self.OUT,
            expand_paths=False,
            filter_path="-home-joe",
        )
        assert dest == self.OUT / "-home-joe-project-A"

    def test_filter_miss_flat(self):
        """Flat-name filter that doesn't match the encoded prefix
        excludes the project."""
        dest = project_destination(
            self.SRC,
            output_dir=self.OUT,
            expand_paths=False,
            filter_path="-home-jane",
        )
        assert dest is None


# -----------------------------------------------------------------------------
# output_path_is_file (--output suffix heuristic, Q4 resolution)
# -----------------------------------------------------------------------------


class TestOutputPathIsFile:
    @pytest.mark.parametrize(
        "value,is_file",
        [
            ("/tmp/out.md", True),
            ("/tmp/out.markdown", True),
            ("/tmp/out.html", True),
            ("/tmp/out.json", True),
            # Case-insensitive
            ("/tmp/Out.HTML", True),
            # No recognised suffix → directory
            ("/tmp/out", False),
            ("/tmp/obsidian-vault", False),
            # Suffix that isn't a recognised output format
            ("/tmp/out.txt", False),
            ("/tmp/out.tar.gz", False),
        ],
    )
    def test_suffix_heuristic(self, value: str, is_file: bool):
        assert output_path_is_file(Path(value)) is is_file
