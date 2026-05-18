"""Tests for the git-commit linkifier (issue #156).

Covers two modules and their integration:

- ``markdown_plugins.make_sha_plugin`` and ``linkify_shas_in_text``:
  unit-level checks of the inline-parser plugin and the
  Markdown-side text substitution helper. Resolver is mocked.

- ``git_remote.resolve_sha`` + ``render_with_repo_context``: end-to-
  end against the project's own git repo (cheap, deterministic, no
  network — uses local remote-tracking refs only).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from claude_code_log.git_remote import (
    _parse_git_url,
    canonical_cwd_from_messages,
    clear_resolver_caches,
    render_with_repo_context,
    resolve_sha,
    resolve_sha_for_current_render,
)
from claude_code_log.html.utils import render_markdown, render_user_markdown
from claude_code_log.markdown_plugins import (
    SHA_PATTERN,
    linkify_shas_in_text,
    make_codespan_sha_plugin,
    make_sha_plugin,
)


# ---------------------------------------------------------------------------
# Fixture: a deterministic resolver that maps known SHAs to URLs.


_KNOWN_URLS = {
    "abc1234": "https://example.com/abc1234",
    "deadbeefcafe": "https://example.com/deadbeefcafe",
}


def _mock_resolve(sha: str) -> Optional[str]:
    return _KNOWN_URLS.get(sha)


# ---------------------------------------------------------------------------
# SHA_PATTERN regex


class TestShaPattern:
    def test_matches_short_sha(self):
        assert re.fullmatch(SHA_PATTERN, "abc1234")

    def test_matches_full_sha(self):
        assert re.fullmatch(SHA_PATTERN, "0" * 40)

    def test_rejects_too_short(self):
        # 6 chars: below the 7-char minimum.
        assert re.fullmatch(SHA_PATTERN, "abc123") is None

    def test_rejects_too_long(self):
        # 41 chars: above the 40-char maximum.
        assert re.fullmatch(SHA_PATTERN, "0" * 41) is None

    def test_rejects_non_hex(self):
        assert re.fullmatch(SHA_PATTERN, "abcg123") is None

    def test_rejects_uppercase(self):
        # Pattern is lowercase-only; git short SHAs are conventionally
        # lowercase. Tighten if real-world data shows uppercase.
        assert re.fullmatch(SHA_PATTERN, "ABC1234") is None


# ---------------------------------------------------------------------------
# make_sha_plugin: HTML mistune integration


def _render_with_plugin(text: str, resolve=_mock_resolve) -> str:
    """Build a fresh mistune renderer with our plugin and render *text*."""
    import mistune

    md = mistune.create_markdown(plugins=[make_sha_plugin(resolve)])
    return str(md(text)).strip()


class TestShaPluginInline:
    def test_emits_link_for_resolvable_sha(self):
        out = _render_with_plugin("See abc1234 in the diff")
        assert '<a href="https://example.com/abc1234">abc1234</a>' in out

    def test_passes_through_unresolved_sha(self):
        out = _render_with_plugin("Local-only commit ffffeee")
        assert "ffffeee" in out
        assert "<a" not in out

    def test_fires_inside_emphasis(self):
        # Confirms main's research finding: the inline plugin recurses
        # through `parse_emphasis` so registered rules fire inside
        # *…* and **…**.
        out = _render_with_plugin("**before abc1234 after**")
        assert "<strong>" in out
        assert '<a href="https://example.com/abc1234">' in out

    def test_does_not_fire_inside_codespan(self):
        out = _render_with_plugin("`abc1234` stays code")
        assert "<code>abc1234</code>" in out
        assert "<a" not in out

    def test_does_not_fire_inside_fenced_block(self):
        out = _render_with_plugin("```\nabc1234\n```")
        assert "abc1234" in out
        assert "<a" not in out

    def test_does_not_double_wrap_existing_link(self):
        # The existing-link guard (state.in_link) keeps us from emitting
        # a nested <a> when the SHA appears inside a Markdown link.
        out = _render_with_plugin("[abc1234](http://manual.example/x)")
        assert out.count("<a ") == 1
        assert "manual.example" in out

    def test_pluralized_sha_does_not_match(self):
        # 'abc12347' (8 chars) IS a valid SHA shape, but the trailing
        # punctuation cases below shouldn't break the match.
        out = _render_with_plugin("Commit abc1234.")
        assert '<a href="https://example.com/abc1234">abc1234</a>' in out


# ---------------------------------------------------------------------------
# make_codespan_sha_plugin: HTML mistune integration for `sha` codespans


def _render_with_both_plugins(text: str, resolve=_mock_resolve) -> str:
    """Build a fresh mistune renderer with *both* SHA plugins."""
    import mistune

    md = mistune.create_markdown(
        plugins=[
            make_sha_plugin(resolve),
            make_codespan_sha_plugin(resolve),
        ]
    )
    return str(md(text)).strip()


class TestCodespanShaPlugin:
    def test_wraps_codespan_sha_in_link(self):
        out = _render_with_both_plugins("See `abc1234` here")
        assert '<a href="https://example.com/abc1234"><code>abc1234</code></a>' in out

    def test_unresolved_codespan_sha_stays_plain_code(self):
        # Local-only SHA: codespan preserved, no link emitted.
        out = _render_with_both_plugins("Local `ffffeee` commit")
        assert "<code>ffffeee</code>" in out
        assert "<a" not in out

    def test_codespan_with_non_sha_body_unchanged(self):
        out = _render_with_both_plugins("Call `hello` first")
        assert "<code>hello</code>" in out
        assert "<a" not in out

    def test_codespan_with_mixed_body_unchanged(self):
        # `git show abc1234`: body isn't *exactly* a SHA, so we don't
        # fire — exactly as the bare-SHA plugin doesn't either.
        out = _render_with_both_plugins("Run `git show abc1234`")
        assert "<code>git show abc1234</code>" in out
        assert "<a" not in out

    def test_codespan_sha_inside_emphasis(self):
        # Bold codespan-SHA: <strong><a><code>…</code></a></strong>.
        out = _render_with_both_plugins("Bold **`abc1234`** here")
        assert "<strong>" in out
        assert '<a href="https://example.com/abc1234"><code>abc1234</code></a>' in out

    def test_codespan_sha_inside_existing_link_preserves_code(self):
        # `[\`abc1234\`](url)`: don't double-wrap, but the in_link
        # branch still emits a codespan token, so the link content
        # is monospaced (not raw backtick text).
        out = _render_with_both_plugins("[`abc1234`](http://manual.example/x)")
        assert out.count("<a ") == 1
        assert "manual.example" in out
        assert "<code>abc1234</code>" in out

    def test_codespan_sha_inside_fenced_block_unchanged(self):
        out = _render_with_both_plugins("```\n`abc1234`\n```")
        assert "<a" not in out
        assert "abc1234" in out


# ---------------------------------------------------------------------------
# linkify_shas_in_text: Markdown-side text substitution


class TestLinkifyShasInText:
    def test_substitutes_resolvable(self):
        out = linkify_shas_in_text("See abc1234 here", _mock_resolve)
        assert out == "See [abc1234](https://example.com/abc1234) here"

    def test_leaves_unresolved_alone(self):
        out = linkify_shas_in_text("Local commit ffffeee", _mock_resolve)
        assert out == "Local commit ffffeee"

    def test_handles_multiple_shas(self):
        out = linkify_shas_in_text("First abc1234 then deadbeefcafe", _mock_resolve)
        assert "[abc1234](https://example.com/abc1234)" in out
        assert "[deadbeefcafe](https://example.com/deadbeefcafe)" in out

    def test_empty_text_returns_unchanged(self):
        assert linkify_shas_in_text("", _mock_resolve) == ""

    # -- Negative-context tests (regression for monk's review on PR #156) --
    #
    # The HTML side's plugin gets these skips for free from mistune's
    # inline parser; the Markdown side has to enforce them manually
    # via the tokenizer in ``_linkify_inline`` / ``_linkify_block_tokens``.
    # Mirrors the parity contract checked by
    # ``TestShaPluginInline.test_does_not_fire_inside_codespan`` etc.

    def test_skips_inside_inline_codespan(self):
        out = linkify_shas_in_text("Run `git show abc1234` now", _mock_resolve)
        assert out == "Run `git show abc1234` now"

    def test_skips_inside_double_backtick_codespan(self):
        # CommonMark allows ``…`` to embed single backticks; the SHA
        # inside still must not be substituted.
        out = linkify_shas_in_text("``code abc1234 in span`` here", _mock_resolve)
        assert out == "``code abc1234 in span`` here"

    def test_skips_inside_fenced_block_backtick(self):
        out = linkify_shas_in_text("```\nabc1234\n```", _mock_resolve)
        assert out == "```\nabc1234\n```"

    def test_skips_inside_fenced_block_tilde(self):
        out = linkify_shas_in_text("~~~\nabc1234\n~~~", _mock_resolve)
        assert out == "~~~\nabc1234\n~~~"

    def test_skips_inside_indented_code_block(self):
        out = linkify_shas_in_text("    abc1234 indented", _mock_resolve)
        assert out == "    abc1234 indented"

    def test_documents_tab_indent_gap(self):
        # CommonMark treats a leading tab as 4-space-equivalent → an
        # indented code block. Our block tokenizer gates strictly on
        # space-only indent (``line.lstrip(" ")``), so a tab-prefixed
        # SHA does get linkified — in violation of CommonMark. This
        # test pins the current (incorrect-but-documented) behaviour
        # so any future fix flags it explicitly. Revisit if real-world
        # transcripts show tab-indented prose; until then, the cost of
        # widening the indent detector isn't worth it.
        out = linkify_shas_in_text("\tabc1234 tab-indented", _mock_resolve)
        assert out == "\t[abc1234](https://example.com/abc1234) tab-indented"

    def test_skips_existing_markdown_link(self):
        # The HTML plugin's ``state.in_link`` guard's text-helper
        # equivalent: a SHA already inside a ``[text](url)`` must not
        # be double-wrapped.
        out = linkify_shas_in_text("[abc1234](manual.example/x)", _mock_resolve)
        assert out == "[abc1234](manual.example/x)"

    def test_substitutes_around_codespan(self):
        # Prose before / after a codespan still gets substituted; the
        # SHA inside the codespan is preserved verbatim.
        out = linkify_shas_in_text(
            "Before abc1234 then `inline abc1234` after abc1234",
            _mock_resolve,
        )
        assert (
            out == "Before [abc1234](https://example.com/abc1234) "
            "then `inline abc1234` after [abc1234](https://example.com/abc1234)"
        )

    def test_substitutes_around_fenced_block(self):
        out = linkify_shas_in_text(
            "Mention abc1234 then\n```\nabc1234 inside\n```\nthen abc1234 again",
            _mock_resolve,
        )
        assert (
            out == "Mention [abc1234](https://example.com/abc1234) then\n"
            "```\nabc1234 inside\n```\n"
            "then [abc1234](https://example.com/abc1234) again"
        )

    def test_unmatched_backtick_still_substitutes_following_prose(self):
        # A lone ``` ` ``` doesn't open a codespan (no matching close);
        # SHAs after it should still get substituted.
        out = linkify_shas_in_text("Lone ` then abc1234", _mock_resolve)
        assert out == "Lone ` then [abc1234](https://example.com/abc1234)"

    def test_lone_open_bracket_terminates(self):
        # Regression: a ``[`` that doesn't open a valid ``[text](url)``
        # link must be emitted as a literal char. Earlier tokenizer
        # stalled here because the prose-accumulator stopped on the
        # same ``[`` it was meant to consume → infinite loop.
        out = linkify_shas_in_text("Label [INFO] abc1234", _mock_resolve)
        assert out == "Label [INFO] [abc1234](https://example.com/abc1234)"

    def test_bracket_without_closing_paren_still_substitutes(self):
        # ``[text]`` with no following ``(url)`` is not a Markdown link
        # — neither does mistune treat it as one on the HTML side
        # (no matching reference definition) so the SHA plugin fires
        # on the prose inside. The Markdown helper mirrors that: the
        # brackets become literal characters around a substituted SHA.
        out = linkify_shas_in_text("see [abc1234] note", _mock_resolve)
        assert out == "see [[abc1234](https://example.com/abc1234)] note"

    # -- Codespan-wrapped SHA → link (parity with
    # ``make_codespan_sha_plugin`` on the HTML side) --

    def test_codespan_only_sha_becomes_linked_codespan(self):
        # ``\`abc1234\``` body is exactly a SHA → wrap the *whole*
        # span (backticks included) in a link target. The resulting
        # ``[\`abc1234\`](url)`` is valid Markdown that renders as
        # ``<a><code>abc1234</code></a>`` — same as the HTML plugin.
        out = linkify_shas_in_text("See `abc1234` here", _mock_resolve)
        assert out == "See [`abc1234`](https://example.com/abc1234) here"

    def test_unresolved_codespan_sha_stays_opaque(self):
        # Local-only commit: resolver returns None → codespan stays
        # as-is, no link wrapping (no broken URLs in the transcript).
        out = linkify_shas_in_text("Local `ffffeee` commit", _mock_resolve)
        assert out == "Local `ffffeee` commit"

    def test_codespan_with_mixed_body_unchanged(self):
        # Single backticks but body isn't *exactly* a SHA → stays
        # opaque, same contract as the existing
        # ``test_skips_inside_inline_codespan`` case.
        out = linkify_shas_in_text("`git show abc1234`", _mock_resolve)
        assert out == "`git show abc1234`"

    def test_codespan_sha_with_double_backticks_unchanged(self):
        # ``\`\`sha\`\``` is a valid CommonMark codespan but multi-
        # backtick: we only rewrite the single-backtick form. Spans
        # stay opaque, consistent with the conservative scope of
        # ``CODESPAN_SHA_PATTERN``.
        out = linkify_shas_in_text("``abc1234``", _mock_resolve)
        assert out == "``abc1234``"

    def test_bold_codespan_sha_becomes_bold_link(self):
        # ``**\`abc1234\`**``: the ``*`` falls through the prose
        # accumulator; the matched-backtick branch fires on the
        # inner span. Result is a bold link round-tripping to
        # ``<strong><a><code>abc1234</code></a></strong>``.
        out = linkify_shas_in_text("Bold **`abc1234`** here", _mock_resolve)
        assert out == "Bold **[`abc1234`](https://example.com/abc1234)** here"


# ---------------------------------------------------------------------------
# git_remote URL parsing


class TestParseGitUrl:
    def test_https_with_dot_git(self):
        assert _parse_git_url("https://github.com/owner/repo.git") == (
            "github.com",
            "owner/repo",
        )

    def test_https_no_dot_git(self):
        assert _parse_git_url("https://github.com/owner/repo") == (
            "github.com",
            "owner/repo",
        )

    def test_ssh(self):
        assert _parse_git_url("git@github.com:owner/repo.git") == (
            "github.com",
            "owner/repo",
        )

    def test_https_with_token(self):
        assert _parse_git_url("https://x-token@github.com/owner/repo.git") == (
            "github.com",
            "owner/repo",
        )

    def test_https_with_subgroup(self):
        # Multi-segment paths (GitLab subgroups) preserved verbatim.
        assert _parse_git_url("https://gitlab.com/group/sub/repo.git") == (
            "gitlab.com",
            "group/sub/repo",
        )

    def test_rejects_local_path(self):
        assert _parse_git_url("/srv/git/repo.git") is None

    def test_rejects_empty(self):
        assert _parse_git_url("") is None

    def test_rejects_no_owner(self):
        assert _parse_git_url("git@github.com:repo.git") is None


# ---------------------------------------------------------------------------
# canonical_cwd_from_messages


class _M:
    def __init__(self, cwd: str = ""):
        self.cwd = cwd


class TestCanonicalCwd:
    def test_picks_only_cwd(self):
        assert canonical_cwd_from_messages([_M("/a"), _M("/a")]) == "/a"

    def test_picks_most_common(self):
        msgs = [_M("/a"), _M("/b"), _M("/b"), _M("/c")]
        assert canonical_cwd_from_messages(msgs) == "/b"

    def test_returns_none_when_no_cwds(self):
        assert canonical_cwd_from_messages([_M(""), _M("")]) is None

    def test_returns_none_for_empty_list(self):
        assert canonical_cwd_from_messages([]) is None

    def test_skips_messages_without_cwd_attr(self):
        class NoCwd:
            pass

        assert canonical_cwd_from_messages([NoCwd(), _M("/x")]) == "/x"


# ---------------------------------------------------------------------------
# render_with_repo_context: ContextVar plumbing


class TestRenderRepoContext:
    def setup_method(self):
        clear_resolver_caches()

    def teardown_method(self):
        clear_resolver_caches()

    def test_outside_context_resolver_returns_none(self):
        # No active context → resolver returns None for any input.
        assert resolve_sha_for_current_render("abc1234") is None

    def test_context_is_reset_on_exit(self):
        with render_with_repo_context("/some/repo"):
            pass
        assert resolve_sha_for_current_render("abc1234") is None


# ---------------------------------------------------------------------------
# Integration: the project's own git repo
#
# This repo has the commit 7c2e6f6 on origin/main (the sidechain
# filter dashed-border commit, used as a real fixture in main's task
# description). If the test environment doesn't have origin set to
# the daaain/claude-code-log GitHub remote (e.g. a fork), the test
# skips.


def _project_repo_origin_is_daaain_repo() -> bool:
    """Skip integration tests if origin doesn't point at the canonical repo."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            cwd=Path(__file__).parent.parent,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return "daaain/claude-code-log" in result.stdout


_KNOWN_LOCAL_SHA = "7c2e6f6"  # On origin/main as of writing.


@pytest.mark.skipif(
    not _project_repo_origin_is_daaain_repo(),
    reason="origin not set to daaain/claude-code-log; skipping integration",
)
class TestIntegrationLocalRepo:
    def setup_method(self):
        clear_resolver_caches()

    def teardown_method(self):
        clear_resolver_caches()

    def test_resolve_sha_returns_github_url(self):
        cwd = str(Path(__file__).parent.parent)
        url = resolve_sha(cwd, _KNOWN_LOCAL_SHA)
        assert url is not None
        assert url.startswith("https://github.com/daaain/claude-code-log/commit/")
        # Full 40-char SHA in the URL.
        assert len(url.rsplit("/", 1)[-1]) == 40

    def test_resolve_sha_returns_none_for_unknown(self):
        cwd = str(Path(__file__).parent.parent)
        # Plausible-looking SHA that doesn't exist in the repo.
        assert resolve_sha(cwd, "deadbeefcafe1234567890abcdef1234567890ab") is None

    def test_html_render_with_repo_context_produces_anchor(self):
        cwd = str(Path(__file__).parent.parent)
        with render_with_repo_context(cwd):
            html = render_markdown(f"See commit {_KNOWN_LOCAL_SHA} for details.")
        assert '<a href="https://github.com/daaain/claude-code-log/commit/' in html
        assert f">{_KNOWN_LOCAL_SHA}</a>" in html

    def test_user_html_render_with_repo_context_produces_anchor(self):
        cwd = str(Path(__file__).parent.parent)
        with render_with_repo_context(cwd):
            html = render_user_markdown(f"Check {_KNOWN_LOCAL_SHA} please")
        assert '<a href="https://github.com/daaain/claude-code-log/commit/' in html

    def test_markdown_text_helper_with_repo_context(self):
        cwd = str(Path(__file__).parent.parent)
        with render_with_repo_context(cwd):
            md = linkify_shas_in_text(
                f"diff at {_KNOWN_LOCAL_SHA} introduces it",
                resolve_sha_for_current_render,
            )
        assert (
            f"[{_KNOWN_LOCAL_SHA}](https://github.com/daaain/claude-code-log/commit/"
            in md
        )

    def test_codespan_sha_becomes_linked_codespan(self):
        # Updated contract (codespan-SHA feature): a single-backtick
        # codespan whose body is exactly a resolvable SHA gets the
        # codespan preserved *and* wrapped in a link. The earlier
        # contract — codespans stay opaque — was inverted on purpose
        # so authors who write ``\`5baac35\``` to typographically
        # quote a SHA still get a clickable commit link.
        cwd = str(Path(__file__).parent.parent)
        with render_with_repo_context(cwd):
            html = render_markdown(f"`{_KNOWN_LOCAL_SHA}` is a code span")
        assert f"<code>{_KNOWN_LOCAL_SHA}</code>" in html
        assert '<a href="https://github.com/daaain/claude-code-log/commit/' in html
        # Mixed-body codespan still stays opaque (regression guard for
        # the "only exact SHAs" half of the contract).
        with render_with_repo_context(cwd):
            mixed = render_markdown(f"`git show {_KNOWN_LOCAL_SHA}` should stay code")
        assert "<a" not in mixed

    def test_no_context_produces_plain_text(self):
        # Without entering the context, even a real reachable SHA
        # renders as plain text. This is the behavioural baseline:
        # rendering paths that don't bind a cwd must not surprise
        # the user with surprise links.
        html = render_markdown(f"See commit {_KNOWN_LOCAL_SHA} for details.")
        assert "<a " not in html
        assert _KNOWN_LOCAL_SHA in html
