"""Runtime CSS contract for nested sub-agent groups (#213).

Every spawn boundary — at ANY depth — indents its transcript group by
2em and frames it with the tool-green line; depth accumulates through
DOM nesting (a depth-2 group lives inside a depth-1 group). Computed
styles are read directly so default fold state doesn't matter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from claude_code_log.converter import _integrate_agent_entries, load_transcript
from claude_code_log.html.renderer import generate_html

TRUNK_SID = "33330000-0000-4000-8000-000000000001"
TRUNK = Path(__file__).parent / "test_data" / "nested_agents" / f"{TRUNK_SID}.jsonl"

GROUPS_JS = """() => {
    const isGroup = el => el.classList && el.classList.contains('children')
        && el.querySelector(':scope > .message-node > .message.sidechain');
    const groups = Array.from(document.querySelectorAll('.children')).filter(isGroup);
    return groups.map(g => {
        const cs = getComputedStyle(g);
        let depth = 0, el = g.parentElement;
        while (el) {
            if (isGroup(el)) depth += 1;
            el = el.parentElement;
        }
        return {
            marginLeft: cs.marginLeft,
            borderWidth: cs.borderLeftWidth,
            borderColor: cs.borderLeftColor,
            enclosingGroups: depth,
        };
    });
}"""


class TestNestedAgentGroupCss:
    @pytest.mark.browser
    def test_every_spawn_boundary_indents_and_draws_the_line(
        self, page: Page, tmp_path: Path
    ) -> None:
        entries = load_transcript(TRUNK, silent=True)
        _integrate_agent_entries(entries)
        html_path = tmp_path / "nested.html"
        html_path.write_text(generate_html(entries, "Nested CSS"), encoding="utf-8")
        page.set_viewport_size({"width": 1600, "height": 1200})
        page.goto(f"file://{html_path}")

        groups = page.evaluate(GROUPS_JS)
        # 2×2: mid1 + mid2 (d1) with leaf22's surviving group inside mid2;
        # chain: chain1 (d1) with chain2's group inside; interrupted (d1).
        assert len(groups) == 6
        for g in groups:
            assert g["marginLeft"] == "32px", g  # 2em at 16px root
            assert g["borderWidth"] == "2px", g
            assert g["borderColor"] == "rgb(76, 175, 80)", g  # tool-green

        # Depth accumulates structurally: exactly two groups are nested
        # inside another group's subtree (depth-2 boundaries).
        nested = [g for g in groups if g["enclosingGroups"] > 0]
        assert len(nested) == 2, groups
