# Issue #153 — collapsible body overlaps preceding content

## Symptom

In a WebFetch tool result, the meta badge (`200` / `559.5 KB` / `1.5s`) and
the collapsible's `▶ N lines` summary render **on top of each other** instead
of stacked. Screenshot in [issue #153](https://github.com/daaain/claude-code-log/issues/153).

## Root cause

Tool-result collapsibles are styled with a deliberate negative top margin:

```css
.tool_result .collapsible-code { margin-top: -2.5em; }
```

This is intentional — it tucks the *first* collapsible up under the tool's
header bar so the `▶ N lines` summary visually merges with the card title.

The bug appears whenever a collapsible is **not** the first thing in the card
body — i.e. when sibling content precedes it:

| Case | Preceding sibling | Status before fix |
|------|-------------------|-------------------|
| WebFetch | `.webfetch-meta` badge | **broken** (overlap) |
| Async Task answer (#90) | "Result (from async notification)" label | point-fixed (`.task-async-answer .collapsible-code { margin-top: 0 }`) |
| Plugin-emitted header line | a `<p>` header before `render_markdown_collapsible(...)` | broken (overlap) |

In all three the `-2.5em` pull-up drags the collapsible's first row up over the
preceding sibling.

The WebFetch case had an override that was meant to neutralise this:

```css
.webfetch-result.collapsible-code { margin-top: 0; }   /* dead code */
```

…but it never matched: those two classes sit on **different elements** —
`.webfetch-result` is the wrapper `<div>`, `.collapsible-code` is the nested
`<details>`. It needed a descendant combinator, not a compound selector. So the
`-2.5em` won and the overlap remained.

## Fix

Replace the per-case overrides with one general rule keyed on the structural
discriminator that distinguishes "merge under header" from "overlap": **is the
collapsible's wrapper the first child of the card body, or does it follow a
sibling?**

```css
/* message_styles.css */
.tool_result .content > * + * .collapsible-code {
    margin-top: 0.5em;
}
```

`* + *` matches any card-body child that has a preceding sibling; the
collapsible inside it then gets a small positive margin instead of the
`-2.5em` merge. Higher specificity (0,0,3) than `.tool_result .collapsible-code`
(0,0,2), so it wins for following-collapsibles while the first one keeps the
merge. A small **positive** value (not `0`) guarantees separation even when the
preceding element has a zero bottom margin (e.g. a `.markdown p` header), which
is why it also covers the plugin/header shape, not just WebFetch.

The dead `.webfetch-result.collapsible-code` override and the now-redundant
`.task-async-answer .collapsible-code` override are removed; both are subsumed
by the general rule (comments left in place pointing to it).

## Visual before/after

Generated samples (open in a browser):

- `before.html` — WebFetch meta badge and `▶ 33 lines` overlapping.
- `after.html` — meta badge on its own line, `▶ 33 lines` cleanly below.
- Read tool result is **identical** before/after — its collapsible is the first
  child, so the header-bar merge is preserved.

## Regression guard

`test/test_collapsible_overlap_browser.py` (Playwright, `@pytest.mark.browser`):

- `test_webfetch_meta_does_not_overlap_collapsible` — asserts the summary's top
  is at/below the meta badge's bottom. Fails on the pre-fix CSS (~32px overlap).
- `test_first_collapsible_still_tucks_under_header` — asserts a Read result's
  summary still starts above the header's bottom edge (merge preserved).

Snapshot HTML tests were updated (`--snapshot-update -n0`) for the embedded-CSS
text change; no structural HTML changed.
