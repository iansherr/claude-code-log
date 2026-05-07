"""Tests for adjacent SlashCommand ↔ UserSlashCommand pairing (issue #126).

A `Slash Command` (the typed `/cmd`) and the corresponding
`User (slash command)` (expanded prompt or system caveat) represent a
single logical event and must render as a paired unit. They can appear
in either order:

    `/init`  →  Slash Command  then  User (slash command)
    `/exit`  →  User (slash command)  (caveat)  then  Slash Command
"""

from __future__ import annotations

import pytest

from claude_code_log.models import (
    CommandOutputMessage,
    MessageMeta,
    SlashCommandMessage,
    UserSlashCommandMessage,
)
from claude_code_log.renderer import (
    RenderingContext,
    TemplateMessage,
    _identify_message_pairs,
    _try_pair_adjacent,
)


# ----------------------------- helpers ---------------------------------------


def _meta(uuid: str, *, ts: str = "2026-01-01T00:00:00Z") -> MessageMeta:
    return MessageMeta(session_id="s", timestamp=ts, uuid=uuid)


def _slash(ctx: RenderingContext, uuid: str, name: str = "init") -> TemplateMessage:
    msg = TemplateMessage(
        SlashCommandMessage(
            meta=_meta(uuid),
            command_name=name,
            command_args="",
            command_contents="",
        )
    )
    ctx.register(msg)
    return msg


def _user_slash(
    ctx: RenderingContext, uuid: str, text: str = "expanded prompt"
) -> TemplateMessage:
    meta = _meta(uuid)
    meta.is_meta = True
    msg = TemplateMessage(UserSlashCommandMessage(meta=meta, text=text))
    ctx.register(msg)
    return msg


def _cmd_output(
    ctx: RenderingContext, uuid: str, stdout: str = "ok"
) -> TemplateMessage:
    msg = TemplateMessage(
        CommandOutputMessage(meta=_meta(uuid), stdout=stdout, is_markdown=False)
    )
    ctx.register(msg)
    return msg


@pytest.fixture
def ctx() -> RenderingContext:
    return RenderingContext()


# ----------------------------- _try_pair_adjacent ----------------------------


class TestSlashCommandAdjacentPairing:
    """Pair the slash invocation and its expanded prompt — symmetric in order."""

    def test_slash_then_user_slash_pairs(self, ctx: RenderingContext) -> None:
        """`/init` flow: Slash invocation followed by expanded prompt."""
        slash = _slash(ctx, "u1", name="init")
        user_slash = _user_slash(ctx, "u2", text="Please analyze...")

        assert _try_pair_adjacent(slash, user_slash) is True
        assert slash.pair_last == user_slash.message_index
        assert user_slash.pair_first == slash.message_index

    def test_user_slash_then_slash_pairs(self, ctx: RenderingContext) -> None:
        """`/exit` flow: caveat (User slash command) then Slash invocation."""
        caveat = _user_slash(ctx, "v1", text="Caveat: messages below were generated...")
        slash = _slash(ctx, "v2", name="exit")

        assert _try_pair_adjacent(caveat, slash) is True
        assert caveat.pair_last == slash.message_index
        assert slash.pair_first == caveat.message_index

    def test_two_slash_messages_do_not_pair(self, ctx: RenderingContext) -> None:
        """Two adjacent SlashCommand messages are unrelated; no pair."""
        a = _slash(ctx, "a1", name="init")
        b = _slash(ctx, "a2", name="exit")
        assert _try_pair_adjacent(a, b) is False
        assert a.pair_last is None and b.pair_first is None

    def test_two_user_slash_messages_do_not_pair(self, ctx: RenderingContext) -> None:
        a = _user_slash(ctx, "b1", text="one")
        b = _user_slash(ctx, "b2", text="two")
        assert _try_pair_adjacent(a, b) is False


# ----------------------------- regression: existing rules --------------------


class TestExistingPairingRulesPreserved:
    """The new rule must not regress slash-cmd → command-output pairing."""

    def test_slash_then_output_still_pairs(self, ctx: RenderingContext) -> None:
        slash = _slash(ctx, "c1", name="context")
        output = _cmd_output(ctx, "c2", stdout="rendered context")
        assert _try_pair_adjacent(slash, output) is True
        assert slash.pair_last == output.message_index

    def test_user_slash_then_output_still_pairs(self, ctx: RenderingContext) -> None:
        user_slash = _user_slash(ctx, "d1", text="some prompt")
        output = _cmd_output(ctx, "d2", stdout="rendered")
        assert _try_pair_adjacent(user_slash, output) is True
        assert user_slash.pair_last == output.message_index


# ----------------------------- full pass: triples ----------------------------


class TestThreeMessageSequence:
    """The dominant `/cmd` shape in real transcripts is three sibling user
    messages: a UserSlash caveat preamble, the typed SlashCommand, and the
    CommandOutput, all sharing one timestamp. They group as a triple
    (pair_first → pair_middle → pair_last) so the slash-command name stays
    in the rendered title and no message is orphaned."""

    def test_caveat_slash_output_binds_as_triple(self, ctx: RenderingContext) -> None:
        caveat = _user_slash(ctx, "e1", text="Caveat: ...")
        slash = _slash(ctx, "e2", name="exit")
        output = _cmd_output(ctx, "e3", stdout="See ya!")

        _identify_message_pairs([caveat, slash, output])

        # Triple wiring: pair_first owns pair_middle and pair_last;
        # the middle and last members back-reference pair_first.
        assert caveat.pair_middle == slash.message_index
        assert caveat.pair_last == output.message_index
        assert slash.pair_first == caveat.message_index
        assert slash.pair_last == output.message_index
        assert output.pair_first == caveat.message_index

        # Role properties classify each member exclusively.
        assert caveat.is_first_in_pair
        assert not caveat.is_middle_in_pair
        assert not caveat.is_last_in_pair
        assert slash.is_middle_in_pair
        assert not slash.is_first_in_pair
        assert not slash.is_last_in_pair
        assert output.is_last_in_pair
        assert not output.is_first_in_pair
        assert not output.is_middle_in_pair

        # CSS roles emit the right class names for the HTML template.
        assert caveat.pair_role == "pair_first"
        assert slash.pair_role == "pair_middle"
        assert output.pair_role == "pair_last"

    def test_slash_userslash_no_output_pairs_cleanly(
        self, ctx: RenderingContext
    ) -> None:
        """`/init` — no command output — pairs as a 2-msg pair (not triple)."""
        slash = _slash(ctx, "f1", name="init")
        user_slash = _user_slash(ctx, "f2", text="Please analyze...")

        _identify_message_pairs([slash, user_slash])

        assert slash.pair_last == user_slash.message_index
        assert user_slash.pair_first == slash.message_index
        # No middle in a 2-msg pair.
        assert slash.pair_middle is None
        assert user_slash.pair_middle is None
        assert slash.is_first_in_pair
        assert user_slash.is_last_in_pair


# ----------------------------- end-to-end Markdown render --------------------


class TestMarkdownRender:
    """End-to-end Markdown coverage for the dominant `/cmd` triple shape.

    Without the title-borrowing override and `pair_middle` body delegation,
    Markdown would render as `## User (slash command)` with no `/exit`
    visible and the trailing CommandOutput body dropped — both regressions
    surfaced in monk's review of PR #127.
    """

    def _load_triple(
        self, tmp_path, *, ts: str = "2026-04-17T01:13:55Z", cmd: str = "exit"
    ):
        """Write a 3-msg JSONL fixture and load it into TemplateMessages."""
        import json
        from claude_code_log.converter import load_transcript

        lines = [
            {
                "type": "user",
                "uuid": "u1",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "1",
                "parentUuid": None,
                "isSidechain": False,
                "userType": "user",
                "cwd": "/x",
                "isMeta": True,
                "message": {"role": "user", "content": "Caveat: caveat text."},
            },
            {
                "type": "user",
                "uuid": "u2",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "1",
                "parentUuid": "u1",
                "isSidechain": False,
                "userType": "user",
                "cwd": "/x",
                "message": {
                    "role": "user",
                    "content": (
                        f"<command-name>{cmd}</command-name>"
                        f"<command-message>{cmd}</command-message>"
                        f"<command-args></command-args>"
                    ),
                },
            },
            {
                "type": "user",
                "uuid": "u3",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "1",
                "parentUuid": "u2",
                "isSidechain": False,
                "userType": "user",
                "cwd": "/x",
                "message": {
                    "role": "user",
                    "content": "<local-command-stdout>See ya!</local-command-stdout>",
                },
            },
        ]
        fn = tmp_path / "t.jsonl"
        fn.write_text("\n".join(json.dumps(line) for line in lines))
        return load_transcript(fn)

    def test_triple_renders_command_name_and_output(self, tmp_path) -> None:
        from claude_code_log.markdown.renderer import MarkdownRenderer

        # Bare ``exit`` exercises the legacy-emission normalisation path —
        # the rendered command name carries the unified ``/exit`` shape.
        msgs = self._load_triple(tmp_path, cmd="exit")
        md = MarkdownRenderer().generate(msgs, "Test")

        # Slash command title borrowed (would otherwise be "User (slash command)").
        assert "🤷 Command `/exit`" in md
        assert "User (slash command)" not in md
        # Caveat body kept (delegated from pair_first to itself).
        assert "Caveat: caveat text." in md
        # Output body kept (delegated from pair_first to pair_last — would
        # otherwise be dropped by the orphan empty-title content-skip path).
        assert "See ya!" in md

    def test_triple_renders_with_pair_css_classes_in_html(self, tmp_path) -> None:
        """HTML triple emits pair_first / pair_middle / pair_last in document order."""
        import re
        from claude_code_log.html.renderer import HtmlRenderer

        msgs = self._load_triple(tmp_path, cmd="exit")
        html = HtmlRenderer().generate(msgs, "Test")

        # Extract the message-div opening classes in document order, scoped to
        # the slash-command/command-output blocks.
        classes = [
            m.group(1)
            for m in re.finditer(r"<div class='(message [^']+)'", html)
            if "slash-command" in m.group(1) or "command-output" in m.group(1)
        ]
        assert len(classes) == 3, classes
        assert "pair_first" in classes[0]
        assert "pair_middle" in classes[1]
        assert "pair_last" in classes[2]
        # Triple still surfaces the three distinct messages — slash-command card
        # carries the normalised command name in its body code tag (bare
        # ``exit`` → ``/exit``), command-output card carries its stdout.
        # Both must remain visible after pairing.
        assert "<code>/exit</code>" in html
        assert "See ya!" in html

    def test_args_with_backticks_render_with_widened_fence(self, tmp_path) -> None:
        """``command_args`` containing backticks must not break the inline span.

        Reachability note: the ``_COMMAND_ARGS_RE`` tightening to ``(.*?)``
        in this PR widened the class of args that survive parsing — args
        containing ``<`` (which strongly correlates with shell-ish
        payloads carrying backticks) used to be silently dropped, now
        round-trip. The Markdown formatter wraps ``command_args`` in an
        inline code span; with a single-tick fence, an inner backtick
        would terminate the span at the first match and the rest of the
        args would render as a mix of plain text and unmatched ticks.
        ``_inline_code`` widens the fence past the longest backtick run.
        """
        import json
        from claude_code_log.converter import load_transcript
        from claude_code_log.markdown.renderer import MarkdownRenderer

        args_payload = "echo `date` && diff a > b"
        line = {
            "type": "user",
            "uuid": "u1",
            "timestamp": "2026-04-17T01:13:55Z",
            "sessionId": "s1",
            "version": "1",
            "parentUuid": None,
            "isSidechain": False,
            "userType": "user",
            "cwd": "/x",
            "message": {
                "role": "user",
                "content": (
                    "<command-name>/run</command-name>"
                    "<command-message>run</command-message>"
                    f"<command-args>{args_payload}</command-args>"
                ),
            },
        }
        fn = tmp_path / "t.jsonl"
        fn.write_text(json.dumps(line))
        msgs = load_transcript(fn)
        md = MarkdownRenderer().generate(msgs, "Test")

        # The args payload must appear verbatim somewhere in the output.
        assert args_payload in md
        # The whole payload must sit inside one inline-code span — i.e.
        # delimited by the same fence on both sides. With the widened
        # fence (``` outside, single ` inside), the line should contain
        # ``**Args:** ``echo `date` && diff a > b``\n``-style output.
        # Locate the substring and verify the surrounding fence pair.
        assert "**Args:** ``" in md, f"Expected widened fence, got: {md!r}"
        # Sanity: the payload wasn't fragmented across multiple ticks
        # (which a single-tick span would have produced).
        assert f"``{args_payload}``" in md, (
            f"Expected widened fence to wrap args verbatim, got: {md!r}"
        )


class TestSystemMessageChainPairing:
    """Regression: chained system messages must not produce N-tuples (#137).

    A chain of N system messages (each one's ``parentUuid`` equal to
    the previous system message's ``uuid`` — common with ``/context`` /
    ``/cost`` multi-step output) used to render as
    ``pair_first → pair_middle × (N-2) → pair_last``: a fake N-tuple.
    Mechanism: the parent-child indexed pairing called ``_mark_pair``
    on every link in the chain, leaving every interior node with both
    ``pair_first`` and ``pair_last`` set, which ``is_middle_in_pair``
    reads as a triple-middle. The fix skips ``_mark_pair`` when the
    candidate parent is itself already paired as a child, breaking
    chains into pairs of two from the leading edge.
    """

    @staticmethod
    def _build_chain(tmp_path, n: int):
        """Write a JSONL with one user root + ``n`` chained system entries.

        Each system entry's ``parentUuid`` equals the previous one's
        ``uuid``; the first system entry parents the user root. This is
        the exact shape produced by real-world ``/context``-style output
        when the harness writes multi-step status into chained system
        entries.
        """
        import json
        from claude_code_log.converter import load_transcript

        ts = "2026-05-06T10:00:00Z"
        lines: list[dict[str, object]] = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "2.0.0",
                "type": "user",
                "message": {"role": "user", "content": "hello"},
                "uuid": "root",
                "timestamp": ts,
            },
        ]
        prev_uuid = "root"
        for i in range(n):
            uuid = f"sys{i}"
            lines.append(
                {
                    "parentUuid": prev_uuid,
                    "isSidechain": False,
                    "userType": "external",
                    "cwd": "/tmp",
                    "sessionId": "s1",
                    "version": "2.0.0",
                    "type": "system",
                    "level": "info",
                    "subtype": "local_command",
                    "content": f"system entry {i}",
                    "uuid": uuid,
                    "timestamp": ts,
                }
            )
            prev_uuid = uuid
        fn = tmp_path / "chain.jsonl"
        fn.write_text("\n".join(json.dumps(line) for line in lines))
        return load_transcript(fn)

    @staticmethod
    def _system_pair_classes(html: str) -> list[str]:
        """Extract pair-role keywords from each system message div.

        The Jinja template currently emits single-quoted class
        attributes, but accept either quote style so the helper does
        not silently miss future template changes (and so the test
        fails loudly if the template starts emitting nothing at all,
        rather than fails noisily several layers downstream).
        """
        import re

        # Match either ``class='message system ...'`` or
        # ``class="message system ..."`` — the quote at the start of
        # the class attribute is captured and required to match at the
        # close (back-reference).
        pattern = re.compile(r"""<div class=(['"])message system [^'"]*\1""")
        roles: list[str] = []
        for m in pattern.finditer(html):
            cls = m.group(0)
            for role in ("pair_first", "pair_middle", "pair_last"):
                if role in cls:
                    roles.append(role)
                    break
            else:
                roles.append("none")
        # Defensive: every test in this class drives a fixture with at
        # least one system message; an empty list almost certainly means
        # the template stopped emitting the expected shape, not that
        # the renderer's behaviour changed.
        assert roles, "expected at least one system message div in rendered HTML"
        return roles

    def test_chain_of_four_pairs_into_two_doubles(self, tmp_path) -> None:
        """4-system chain → ``[pair_first, pair_last, pair_first, pair_last]``.

        Pre-fix shape was ``[pair_first, pair_middle, pair_middle, pair_last]``.
        """
        from claude_code_log.html.renderer import HtmlRenderer

        msgs = self._build_chain(tmp_path, n=4)
        html = HtmlRenderer().generate(msgs, "Test")
        assert self._system_pair_classes(html) == [
            "pair_first",
            "pair_last",
            "pair_first",
            "pair_last",
        ]

    def test_chain_of_three_pairs_first_two_only(self, tmp_path) -> None:
        """3-system chain → ``[pair_first, pair_last, none]``: the third
        system stands alone because its parent is already paired."""
        from claude_code_log.html.renderer import HtmlRenderer

        msgs = self._build_chain(tmp_path, n=3)
        html = HtmlRenderer().generate(msgs, "Test")
        assert self._system_pair_classes(html) == [
            "pair_first",
            "pair_last",
            "none",
        ]

    def test_no_middle_role_in_chain(self, tmp_path) -> None:
        """Even longer chains never produce a ``pair_middle`` role —
        the only legitimate source of ``pair_middle`` is the
        ``UserSlash → Slash → CommandOutput`` triple, which is a
        different code path."""
        from claude_code_log.html.renderer import HtmlRenderer

        msgs = self._build_chain(tmp_path, n=8)
        html = HtmlRenderer().generate(msgs, "Test")
        roles = self._system_pair_classes(html)
        assert "pair_middle" not in roles, (
            f"Chains must not produce pair_middle, got: {roles}"
        )

    @staticmethod
    def _build_siblings(tmp_path):
        """Two system entries with the SAME ``parentUuid`` (siblings).

        Distinct from the chain shape above: chains are A→B→C→…; here
        both system entries point at the same parent. ``_mark_pair``
        sets only ``parent.pair_last`` (forward-link), never
        ``parent.pair_first``, so a guard that only checks the back-
        link silently fires twice and the second sibling overwrites
        the first's pairing — the bug CodeRabbit flagged on PR #140.
        """
        import json
        from claude_code_log.converter import load_transcript

        ts = "2026-05-07T10:00:00Z"
        lines: list[dict[str, object]] = [
            {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "2.0.0",
                "type": "user",
                "message": {"role": "user", "content": "hello"},
                "uuid": "root",
                "timestamp": ts,
            },
            {
                "parentUuid": "root",
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "2.0.0",
                "type": "system",
                "level": "info",
                "subtype": "local_command",
                "content": "parent system entry",
                "uuid": "parent",
                "timestamp": ts,
            },
        ]
        # Two siblings, both pointing at "parent".
        for i in range(2):
            lines.append(
                {
                    "parentUuid": "parent",
                    "isSidechain": False,
                    "userType": "external",
                    "cwd": "/tmp",
                    "sessionId": "s1",
                    "version": "2.0.0",
                    "type": "system",
                    "level": "info",
                    "subtype": "local_command",
                    "content": f"sibling {i}",
                    "uuid": f"sib{i}",
                    "timestamp": ts,
                }
            )
        fn = tmp_path / "siblings.jsonl"
        fn.write_text("\n".join(json.dumps(line) for line in lines))
        return load_transcript(fn)

    def test_siblings_share_parent_only_first_pairs(self, tmp_path) -> None:
        """Two system messages with the same parent: only the first
        pairs with the parent; the second renders standalone.

        Pre-fix shape (chain-bug guard alone, ``pair_first is None``):
        ``[pair_first (parent, pointing at sib1), pair_last (sib0,
        stale-pointing at parent), pair_last (sib1)]`` — sib0's
        ``pair_first`` no longer matches ``parent.pair_last`` after
        sib1 overwrites it.

        Post-fix shape (full guard, ``pair_first AND pair_last is
        None``): ``[pair_first (parent ↔ sib0), pair_last (sib0 ↔
        parent), none (sib1 standalone)]``.
        """
        from claude_code_log.html.renderer import HtmlRenderer

        msgs = self._build_siblings(tmp_path)
        html = HtmlRenderer().generate(msgs, "Test")
        roles = self._system_pair_classes(html)
        assert roles == ["pair_first", "pair_last", "none"], (
            f"Expected siblings-pair shape, got: {roles}"
        )
