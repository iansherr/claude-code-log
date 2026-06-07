# Ghosting epic — implementation plan

> Concrete design for the unified-ghosting refactor that gates D12
> (`detail-delete-reindex`) and the single-axis end-state described in
> [refactor-reindex-with-ghosting.md](refactor-reindex-with-ghosting.md)
> and [simplify-converter-renderer.md §7](simplify-converter-renderer.md#7-detail-filtering-the-single-axis-end-state-added-2026-05-29).

**Status:** AWAITING CBOOS GREENLIGHT. Branched at
`dev/ghosting-epic` from `main` @ `129c998`. No code written yet.

---

## 0. Why this exists

`ctx.messages` is the rendering layer's source-of-truth registry —
every TemplateMessage's `message_index` is its position in this list,
and downstream passes (pair identification, session nav, fork-point
backlinks, junction forward links, hierarchy/tree, fold/unfold) all
read those indices to look things up. Today, two passes
(`_pair_skill_tool_uses` and `_filter_template_by_detail`) **delete**
messages from `ctx.messages`, which invalidates every cached index;
`_reindex_filtered_context` exists solely to rewrite those caches
after the fact.

The remap surface keeps growing — PR #131 added
`SessionHeaderMessage.parent_message_index`, PR #132 added the
async-agents targeted ghost (which proved the alternative works).
Each new index-bearing field is a "remember to remap X" obligation
on every contributor.

The ghosting approach inverts the model: dropped messages become
`None` slots in `ctx.messages` (preserving every index and freeing
the dropped object for GC). Downstream passes that *iterate*
messages learn to skip the `None` slots; passes that read stored
indices keep working unchanged because the stable indices around
the dropped slots don't shift. The two reindex-callers +
`_reindex_filtered_context` itself all disappear once both callers
migrate.

This document covers the **entire** migration: the unified flag, the
pass-by-pass changes, the single-axis collapse of pre-render
filtering, and a phased rollout that's reviewable in steps.

---

## 1. Why standalone ghost attempts failed (and the unified version
doesn't)

The verifier rejected two earlier scoped attempts. Both rejections
land on real failure modes that this plan addresses head-on:

### 1.1 `detail-ghost-skill-fold` — "bare card" rendering

**Reject reason:** "the ghosted level-1 slash body adopts the
following assistant turn as a child after `_build_message_hierarchy`,
so the elision rule keeps it (renders a bare card)."

**Root cause:** the existing render-loop elision is `if title or
html or msg.children:` (HTML) — a "no title AND no content AND no
children" rule. A ghost that has visible children is *not* eligible
for elision; it renders as an empty header above its children.

**Why the unified plan fixes it:** `_build_message_hierarchy` learns
to skip ghosts and **graft** their (non-ghost) children to the next
non-ghost ancestor. After hierarchy, a ghost's `children` is empty
(its real children moved up the stack). The existing elision rule
then catches it cleanly — no special case in the render loop.

### 1.2 `detail-move-template-filter-to-tree` — late-pass references

**Reject reason:** "nav/descendant-counts/backlinks run *after* the
proposed prune point, so descendant counts and backlink anchors
would dangle."

**Root cause:** the prior attempt moved the prune (= delete) deeper
into the pipeline without addressing the downstream readers.
Anything that reads stored indices after the prune sees stale
pointers.

**Why the unified plan fixes it:** ghosting **never prunes**. The
indices stay stable across the entire pipeline. Nav, descendant
counts, and backlinks read the same indices they always did; the
only change is that ghost messages' contributions are filtered out
of the *output*, not out of the *registry*.

---

## 2. Ghosts as `None` slots in `ctx.messages`

### 2.1 Model

No new field. Widen the slot type instead:

```python
class RenderingContext:
    messages: list[Optional[TemplateMessage]]  # was list[TemplateMessage]
```

Ghosting a message becomes `ctx.messages[idx] = None`. The slot
stays, so every index around it (and every stored reference to
that index from another message) keeps pointing at the same
position. The dropped `TemplateMessage` itself is freed by GC —
no memory bloat from keeping a "tombstone" object alive.

`RenderingContext.get(idx)` already returns `None` when out of
range; with the wider slot type it just naturally returns `None`
for ghosted slots too. No signature change.

### 2.2 Semantics

A ghosted slot:

- **Keeps the index intact** — pair-refs, parent_message_index,
  junction_forward_links, session_first_message all stay valid
  because the LIST LENGTH and the indices of OTHER messages don't
  shift. Stored references to the ghost's own index now resolve
  to `None`, which in practice never happens (see below).
- **Is freed** — the original `TemplateMessage` object is
  dereferenced and GC'd. No content/meta/render_session_id kept
  around.
- **Doesn't participate** in any pass that iterates
  `ctx.messages`: hierarchy stack (no contribution → children
  graft to the next non-None ancestor), pair-id (no entry in the
  index dicts), tree-build (not emitted as a root, can't be
  someone's parent), format renderers (skipped by iteration).

**Stored references can target a None slot, and the ghosting
passes repair them.** The things the reindex pass remapped today
reference *session/branch headers* (`parent_message_index`,
`session_first_message`, `junction_forward_links` targets) or
*paired content messages* (`pair_first/last/middle`). Session
headers are never ghosted, but a *fork point* can coincide with a
slot a detect-and-ghost pass removes — e.g. a within-session fork
attached to a slash-body or launching-skill tool_result that
`_pair_skill_tool_uses` ghosts, or (Phase 2) a fork-point assistant
that `_ghost_template_by_detail` ghosts at low detail. When that
happens, a cached `parent_message_index` / `session_first_message`
entry resolves through `ctx.get(...)` to `None`, and the rendered
`#msg-d-{N}` backlink (emitted from the raw index) would dangle.
Each ghosting pass therefore repairs its own cached refs:
`_pair_skill_tool_uses` calls `_drop_anchor_refs_into_ghosts`
(and `prepare_session_navigation` leaves the fork anchor unset for
a ghosted target rather than retargeting it); Phase 2 adds
`_repair_stale_anchor_refs`. `junction_forward_links` are populated
*after* the skill-fold pass, so they need no repair there.

### 2.3 Helper

A single helper in `renderer.py`, used by every iterator:

```python
def _visible(
    messages: Iterable[Optional[TemplateMessage]],
) -> Iterator[TemplateMessage]:
    """Yield only non-None messages (filter ghosts)."""
    return (m for m in messages if m is not None)
```

Most passes use `_visible(messages)` instead of `messages` directly.
Where positional lookups happen (`message_by_index`), the build
loop already does `if message.message_index is not None` per the
existing code — folding a None check in costs nothing.

---

## 3. Pass-by-pass refactor plan

The pipeline order (from `generate_template_messages`):

| Order | Pass | Today's behavior | Post-ghost behavior |
|---|---|---|---|
| 1 | `_render_messages` | builds `ctx.messages` | unchanged |
| 2 | `_pair_skill_tool_uses` | deletes 1–2 msgs + reindexes | sets `ctx.messages[idx] = None`; no reindex |
| 3 | `_link_junction_forwards` | reads ctx.messages + writes indices | unchanged — indices stable; skips None slots |
| 4 | `_filter_template_by_detail` + reindex | deletes many + reindexes | rewritten as `_ghost_template_by_detail(ctx, detail)`; sets None slots; no reindex |
| 5 | `prepare_session_navigation` | reads `ctx.session_first_message` | unchanged |
| 6 | `_reorder_session_template_messages` | reorders by render_session_id | skips None slots; otherwise unchanged |
| 7 | `_identify_message_pairs` | sequential pair scan | iterates `_visible(messages)` in BOTH the index dict build AND the adjacency walk |
| 8 | `_reorder_paired_messages` | moves pair_last adjacent | unchanged (None slots not paired so they don't move) |
| 9 | `_relocate_subagent_blocks` | moves blocks under anchors | skips None slots; otherwise unchanged |
| 10 | `_build_message_hierarchy` | ancestry from level stack | **skip None + graft children** (None never pushed onto stack) |
| 11 | `_mark_messages_with_children` | counts via ancestry | naturally correct (ancestry already grafted past Nones) |
| 12 | `_build_message_tree` | tree by ancestry | skip None from root_messages; children list already excludes them |
| 13 | `_cleanup_sidechain_duplicates` | mutates parent.children | unchanged (already operates on the post-ghost tree) |
| 14 | Format renderers | iterate root_messages → children | naturally exclude ghosts (they're absent from the tree) |

The detailed changes per pass:

### 3.1 `_pair_skill_tool_uses` — None-out the consumed slots

Today's tail (line ~3360):

```python
consumed_indices.add(other.message_index)
# ...
kept = [msg for msg in ctx.messages if msg.message_index not in consumed_indices]
_reindex_filtered_context(ctx, kept)
```

After:

```python
consumed_indices.add(other.message_index)
# ...
for idx in consumed_indices:
    ctx.messages[idx] = None
# No reindex.
_drop_anchor_refs_into_ghosts(ctx)
```

Same selection logic, smaller action. The dropped TemplateMessages
are freed by GC immediately.

`_drop_anchor_refs_into_ghosts(ctx)` is the anchor-repair tail: a
branch header's `parent_message_index` and the `session_first_message`
map are cached in `_render_messages` — *before* this pass — so a fork
point landing on a consumed slot leaves a cached index pointing at a
ghost. The rendered `#msg-d-{N}` backlink is emitted from that raw
index (`ctx.get()` returning None does not suppress it), so the ref
itself is nulled: the anchor is omitted rather than dangling.
`junction_forward_links` are populated *after* this pass
(`_link_junction_forwards`), so they are out of scope here.
`prepare_session_navigation` independently resolves the fork anchor by
scanning `_visible(ctx.messages)` for `attachment_uuid`; it must leave
`fork_msg_idx = None` when the fork point was ghosted rather than
falling back to the parent session header.

### 3.2 `_ghost_template_by_detail(ctx, detail)` — replaces filter + reindex

Today (line 730):

```python
if detail != DetailLevel.FULL:
    filtered = _filter_template_by_detail(ctx.messages, detail)
    _reindex_filtered_context(ctx, filtered)
```

After:

```python
if detail != DetailLevel.FULL:
    _ghost_template_by_detail(ctx, detail)  # mutates in place
```

Where `_ghost_template_by_detail` walks `ctx.messages`, runs the
existing visibility predicate (same `_content_visible_at`, same
`_LOW_KEEP_TOOLS` opt-out, same sidechain rule) and assigns
`ctx.messages[i] = None` for each non-visible slot. The selection
logic is byte-identical to today's `_filter_template_by_detail`;
the only change is the side-effect (None-out the slot rather than
omit the entry from a kept list).

### 3.3 `_identify_message_pairs` — skip Nones in BOTH passes

Two places:

1. **`_build_pairing_indices`** (the dict-building first pass): None
   slots must NOT be added to the indices. Otherwise an index-based
   pairing could attach a real tool_result to a ghosted tool_use.
   Easy: iterate `_visible(messages)`.

2. **Sequential scan**: when looking at `messages[i]`,
   `messages[i+1]`, `messages[i+2]` for adjacency, None slots must
   be skipped. Easiest implementation: collapse the input via
   `list(_visible(messages))` and walk the visible subsequence (the
   sequential scan can't be replaced by index lookups — see the
   adjacency-only pair types: thinking+assistant, bash-input+output,
   system+output, and the UserSlash→Slash→CommandOutput triple).

Edge case: a ghosted ToolUseMessage with a corresponding non-ghost
ToolResultMessage — the result should NOT be paired (no visible
"use" to pair against), so it just renders as an orphan tool_result.
This is acceptable and matches today's behavior (when the filter
drops the tool_use, the result also drops in most detail levels;
where it doesn't, an orphan tool_result is the correct visible
shape).

### 3.4 `_build_message_hierarchy` — skip Nones + graft children

The critical pass. Today (line 2148):

```python
for message in messages:
    current_level = ... (from message)
    # pop stack until parent level
    while hierarchy_stack and hierarchy_stack[-1][0] >= current_level:
        hierarchy_stack.pop()
    ancestry = [idx for _, idx in hierarchy_stack]
    if message.message_index is not None:
        hierarchy_stack.append((current_level, message.message_index))
    message.ancestry = ancestry
```

After:

```python
for message in messages:
    if message is None:
        continue  # ghosted slot — don't push onto stack; real
                  # children will compute ancestry against the
                  # SURVIVING stack (the ghost's would-be ancestor),
                  # naturally grafting up.
    current_level = ...
    while hierarchy_stack and hierarchy_stack[-1][0] >= current_level:
        hierarchy_stack.pop()
    ancestry = [idx for _, idx in hierarchy_stack]
    if message.message_index is not None:
        hierarchy_stack.append((current_level, message.message_index))
    message.ancestry = ancestry
```

That's the entire children-grafting mechanism: a None slot never
contributes to the stack, so its real children "see through" it to
the next surviving ancestor. No explicit graft step needed.

### 3.5 `_mark_messages_with_children` — skip Nones

Reads ancestry indices, increments counts on ancestors. Per 3.4 a
ghosted slot is None, so its `message_index → message` mapping
isn't built in the first place; ancestor lookups (`if immediate_
parent_index in message_by_index`) naturally don't resolve through
ghosts. Adding `if message is None: continue` at the top of each
loop is purely a defensive readability improvement.

### 3.6 `_build_message_tree` — exclude Nones from roots

Today (line 2255):

```python
for message in messages:
    message.children = []
for message in messages:
    if not message.ancestry:
        root_messages.append(message)
    else:
        immediate_parent_index = message.ancestry[-1]
        if immediate_parent_index in message_by_index:
            parent = message_by_index[immediate_parent_index]
            parent.children.append(message)
return root_messages
```

After:

```python
for message in messages:
    if message is None:
        continue  # skip ghost slots — no children to clear
    message.children = []
for message in messages:
    if message is None:
        continue  # not a root, not a child of anyone
    if not message.ancestry:
        root_messages.append(message)
    else:
        immediate_parent_index = message.ancestry[-1]
        if immediate_parent_index in message_by_index:
            parent = message_by_index[immediate_parent_index]
            parent.children.append(message)
```

Per 3.4's invariant (a non-None message's ancestry never names a
ghost), the parent lookup always resolves to a non-None message —
no defensive `if parent is not None` guard needed beyond the
existing `if immediate_parent_index in message_by_index` check
(which already short-circuits because Nones aren't in the index
map).

### 3.7 Format renderers (HTML + Markdown) — no change

The HTML template iterates root_messages and recurses through
`msg.children`. Ghosts are absent from both. The existing "skip
empty messages" elision is no longer needed for ghost-handling
specifically — but stays because it serves other shapes (e.g.
`AwaySummaryMessage` returning `""` at LOW). Net behavior change:
ghosts simply don't exist in the renderer's input. No template
edits.

Markdown is the same — it iterates the same tree.

JSON exporter (`json/renderer.py`) similarly iterates the tree.
Should "just work" — but I'll explicitly verify (see test plan).

### 3.8 `_link_junction_forwards` — unchanged

Reads `ctx.junction_targets` (from session_hierarchy) and
`ctx.messages`. Indices it writes (`junction_forward_links`,
`fork_point_preview`) are stored on real fork-point messages and
reference branch headers by message_index. All indices are stable
under ghosting. No change.

### 3.9 `prepare_session_navigation` — ghost-aware fork anchor

Reads `ctx.session_first_message` and emits the nav. Indices stay
stable, but the fork-point nav item resolves its anchor by scanning
`_visible(ctx.messages)` for the junction `attachment_uuid`. When the
fork point was ghosted (e.g. a folded Skill slot), that scan can't
find it, so `fork_msg_idx` stays `None` and the anchor is *omitted*
rather than falling back to `session_first_message[parent_sid]` (which
would point the fork link at the parent session header and undo
`_drop_anchor_refs_into_ghosts`). The template guards the fork link
against a `None` index. Mirrors the ghost-aware repair contract.

### 3.10 `_reorder_session_template_messages` — ghost-filter boundary

This pass is where ghosts are *materialized out*. It accepts the
ghost-aware `list[Optional[TemplateMessage]]`, skips `None` slots
while grouping by `render_session_id`, and returns a None-free
`list[TemplateMessage]` (the no-header early-return uses
`list(_visible(messages))`). Because the boundary is here, every
downstream pass (`_identify_message_pairs`, `_reorder_paired_messages`,
tree-build, format renderers) consumes a None-free list and needs no
ghost-skipping — Pyright enforces that via the narrowed return type.

### 3.11 `_reorder_paired_messages` — unchanged

Pair fields are not set on ghosts (per 3.3), so they don't move.
The reorder of *real* pairs is unaffected. No change.

### 3.12 `_relocate_subagent_blocks` — unchanged

Operates on `meta.session_id` (the agent sidechain id). Agent
messages aren't typically ghosted by the detail filter (agent
content survives at LOW; sidechain is filtered at MINIMAL but that's
a separate pre-existing path). Even if a ghost is inside a relocated
block, it rides along correctly. No change.

### 3.13 `_cleanup_sidechain_duplicates` — unchanged

Operates on `parent.children` after the tree is built. By 3.6,
`parent.children` doesn't contain ghosts. No change.

---

## 4. The single-axis end-state — delete `_filter_by_detail`
(pre-render)

The pre-render filter (`_filter_by_detail` on `TranscriptEntry`)
exists today only because deleting at the post-render layer needs
the reindex dance. With ghosting, *everything* it does becomes
expressible at the post-render layer via the per-class
`MessageContent.visible_at` predicate.

**Why this is in scope:** ghosting eliminates the cost the pre-render
filter was paying for (avoiding the reindex). Keeping the pre-render
filter after ghosting lands means maintaining two filter axes for
no benefit, exactly the complexity the §7 analysis identifies.

**What goes away:**

- `_filter_by_detail` (renderer.py, called at line 695).
- `application_model.md §2.6`'s two-axis rationale.

**What stays:**

- `_filter_messages` (structural — unrelated to detail level).
- `_LOW_KEEP_TOOLS` (orthogonal tool-name allowlist; lives in the
  same post-render pass that becomes `_ghost_template_by_detail`).

**Net effect on the post-render ghoster:** it gains the
content-item-stripping responsibilities that today live in
`_filter_by_detail`:

- At MINIMAL / USER_ONLY: strip `ThinkingContent`, `ToolUseContent`,
  `ToolResultContent` from each surviving message's content.
- At LOW: strip `ThinkingContent`.
- At HIGH: drop system entries except `away_summary`.

Each strip rule is naturally expressible on the post-render
TemplateMessage: ToolUseMessage / ToolResultMessage / ThinkingMessage
already have their own classes; the strip becomes
`ctx.messages[i] = None` for the slot. (Effectively: the per-class
`detail_visibility` declarations already encode these rules — see
[plugins.md §6](../dev-docs/plugins.md) — so the pre-render strip
collapses into the post-render visibility predicate.)

The one wrinkle: a single transcript entry can produce multiple
TemplateMessages (e.g., an assistant turn with text + tool_use →
AssistantTextMessage + ToolUseMessage). Pre-render filtering at
MINIMAL would strip the tool_use content item before factory
dispatch, leaving only the text → factory emits only
AssistantTextMessage. Post-render ghosting keeps both messages but
ghosts the ToolUseMessage. The visible output is identical (a single
AssistantTextMessage). The CHUNK_BOUNDARIES are slightly different
in intermediate `ctx.messages`, but since the renderer iterates
the tree (post-ghost), the rendered output matches.

**Risk:** the chunk-boundary difference could change `message_index`
values for surviving messages. Snapshots that ASSERT specific indices
(unusual; mostly snapshots assert rendered HTML/MD) would change.
This needs verification when the single-axis collapse lands —
likely Phase 3 of the rollout.

---

## 5. Phased rollout

The whole epic in one PR would be too big and too risky. Three
phases, each a separate PR, each independently merge-able:

### Phase 1 — `wf/ghosting/skill-fold` (small, safe)

- Widen `RenderingContext.messages` slot type to
  `list[Optional[TemplateMessage]]`. No new field on
  `TemplateMessage`.
- Add the `_visible()` helper.
- Migrate `_pair_skill_tool_uses` to None-out consumed slots
  instead of deleting + reindexing.
- Add `_drop_anchor_refs_into_ghosts` (called from the tail of
  `_pair_skill_tool_uses`) so a fork point landing on a consumed slot
  doesn't leave a dangling `#msg-d-{N}` backlink: null the cached
  `parent_message_index` / `session_first_message` refs that resolve
  to a ghost, and leave the `prepare_session_navigation` fork anchor
  unset rather than retargeting it at the parent session header.
- Implement steps 3.4 (hierarchy graft) and 3.6 (tree skip Nones)
  — both are needed for the Skill ghost to render correctly.
- Implement step 3.3 (pair-id skip Nones) — defensive; the Skill
  ghosts aren't paired but the next phase needs this.
- Walk every `ctx.messages` reader in `renderer.py` (and html/,
  markdown/, json/) and ensure each handles a `None` slot. Most
  iterators just need `_visible(...)` or an `if m is None: continue`.
- Leave `_filter_template_by_detail` and its reindex call
  unchanged.
- Leave `_reindex_filtered_context` in place (still called by the
  detail filter path).
- Verify: full suite green (1924 prior), snapshot byte-identity
  expected. Pin a Skill-fold-on-a-fork test (the PR #131 regression
  origin), at FULL detail. This is the test the verifier called out
  as missing for D12; landing it in Phase 1 closes the gate
  prerequisite even before the detail-filter migration.

Estimate: ~150 lines net change. Lowest risk; lays the
infrastructure. (The widened slot type changes the type annotation
in one place and surfaces `None`-handling additions across every
iterator — Pyright will surface any reader I missed.)

### Phase 2 — `wf/ghosting/detail-filter` (medium)

- Migrate `_filter_template_by_detail` + reindex to
  `_ghost_template_by_detail` (None-out non-visible slots instead
  of deleting).
- Delete `_reindex_filtered_context` (no more callers).
- Re-run per-detail-level snapshot suite. EXPECTED:
  byte-identical (ghosting produces the same visible output as
  deleting + reindexing, by construction).
- If any snapshot moves: it's a bug, not an intentional change —
  investigate before regenerating.

Estimate: ~200 lines net change (mostly deletion of
`_reindex_filtered_context` and its callers' tail). This is the D12
deletion itself.

### Phase 3 — `wf/ghosting/single-axis-collapse` (the §7 end-state)

- Move the pre-render strip rules from `_filter_by_detail` into
  the post-render ghoster's per-class predicate (`detail_visibility`
  ClassVars already cover most of it; one or two cases may need a
  small tweak).
- Delete `_filter_by_detail` and its call.
- Verify: snapshots may move by chunk-boundary effects (see §4
  wrinkle); review and regenerate IF the rendered output is
  byte-identical and ONLY the intermediate indices shifted.

Estimate: ~150 lines deletion (the pre-render filter is ~80 lines)
plus a handful of `detail_visibility` reconciliations. Highest
risk because of the chunk-boundary edge.

Phases 1 + 2 together delete `_reindex_filtered_context`. Phase 3
is the bonus single-axis cleanup; if it surfaces a deeper issue, it
can be deferred indefinitely without holding up D12 (which is
Phase 1 + 2's combined effect).

---

## 6. Test strategy

### 6.1 What MUST stay green

- Full unit suite (currently 1924 passed, 7 skipped).
- HTML + Markdown snapshot suites — byte-identical across Phase 1
  and Phase 2.
- All five `--detail` level snapshots — byte-identical across Phase
  1 and Phase 2.
- `test_skill_pairing.py::TestReindexBranchBackrefs` — the PR #131
  regression test. Currently runs at non-FULL detail (the only path
  that triggers reindex pre-ghosting). After Phase 1, the equivalent
  invariant must be exercised at FULL detail (Phase 1 ghosts inside
  the Skill-fold path which runs unconditionally).
- `test_dag_integration.py::TestRenderSessionResetAcrossSessions`
  (the latent-bug regression from D11) — independent of ghosting,
  should be entirely unaffected.

### 6.2 New tests added in Phase 1

1. **Skill-fold-on-a-fork at FULL detail** (the test the verifier
   called out as missing): construct a fixture with a Skill spawn
   inside a within-session branch; render at FULL; assert the
   branch's `parent_message_index` points to the correct fork
   anchor, the ghosted slash-command body is absent from the
   rendered output, AND the branch backlink "from #msg-d-{N}"
   resolves to the right anchor. This is the test the verifier
   identified as missing for D12 — landing it in Phase 1 means
   D12 (Phase 2 in this plan) inherits coverage.

2. **Skill-fold ghost doesn't break pairing**: a Skill `tool_use`
   inside a session where the slash-body has been ghosted should
   still pair correctly with its matching `tool_result` (if not
   also ghosted by the launching-skill payload rule).

3. **Ghost-with-children graft**: synthetic fixture where a
   ToolUseMessage (ghosted at MINIMAL) has children (a sidechain
   subagent thread that survives); assert the children's ancestry
   resolves to the ghost's *parent*, not the ghost itself.

### 6.3 New tests added in Phase 2

1. **Detail-filter byte-identity**: parametrize over every
   `DetailLevel` value, render a complex fixture (the existing
   per-detail snapshot fixture), assert the rendered output is
   byte-identical to the pre-Phase-2 baseline. This is the
   structural-correctness pin for the deletion.

2. **`_reindex_filtered_context` deleted**: grep -based static
   check that the symbol no longer exists (catches accidental
   re-introduction).

### 6.3.1 Deferred follow-up — junction-link elision coverage

`_repair_stale_anchor_refs` drops `junction_forward_links` entries
whose `branch_idx` resolves to a ghost slot, and elides the entire
fork-point indicator when fewer than 2 navigable branches remain.
The Phase-2 test fixture in `test/test_ghost_repair.py` keeps both
branches alive at USER_ONLY (the leaf user-replies survive), so the
junction-population path never fires and the elision branch stays
exercised only through the existing snapshot suite — same level of
coverage as pre-Phase-2.

A targeted regression test would build a fixture that ghosts an
*entire branch's content* (every message in one of two branches),
then assert: (a) the dropped branch's `junction_forward_links` tuple
disappears, and (b) the fork-point indicator is fully elided because
< 2 navigable branches remain. Not blocking the epic — landing it
as a follow-up tightening pass after Phase 3.

### 6.4 Failing-on-pre-ghosting verification

Per the D11 pattern: each phase's pinning test should FAIL on the
pre-phase tip when applied as a patch (in a `/tmp` throwaway
worktree). This proves the test genuinely exercises the new
behavior, not just passes vacuously.

---

## 7. Risks

### 7.1 Hidden index reader

Some pass we haven't catalogued might read `len(ctx.messages)` or
iterate `ctx.messages` and assume no `None` slots. Mitigations:
the `_visible(...)` helper in the renderer covers the common case,
and widening the slot type to `list[Optional[TemplateMessage]]`
makes Pyright surface every reader that dereferences a slot
without a `None` check (it'll flag `msg.content` / `msg.meta` etc.
on the un-narrowed type). The Phase-1 walk-through of every
`ctx.messages` reader in `renderer.py` + `html/` + `markdown/` +
`json/` closes the rest.

### 7.2 Markdown chunk-boundary subtlety (Phase 3 only)

When a single transcript entry's content items are split into
multiple TemplateMessages, pre-render filtering keeps factory
boundaries; post-render ghosting keeps the *ghosted* messages and
may shift downstream indices. The rendered output should be
identical (the tree iteration skips ghosts), but `d-{index}`
anchors may move. If a snapshot's `d-{N}` references shift, the
HTML is "different bytes, same logical content" — needs careful
review per snapshot.

### 7.3 Plugin contract

Third-party `MessageContent` subclasses interact with the slot
state only indirectly — their `visible_at` predicate continues to
drive whether they get ghosted (via the post-render
`_ghost_template_by_detail` pass), identical to today. The slot
itself is `Optional[TemplateMessage]` from the renderer's
perspective; plugin classes never see a `None` because the iterator
they reach (the format renderer's recursion over the tree) is
already post-filter. No plugin-contract change.

---

## 8. Rollout decisions for cboos to make

1. **Approve the phased plan** (Phases 1 + 2 + 3 as separate PRs)
   vs. monolithic.
2. **Approve dropping `_filter_by_detail`** (Phase 3 / single-axis
   collapse) vs. leaving the two-axis structure in place.
3. **Approve the Phase 1 "Skill-fold on a fork at FULL detail" test**
   as the D12 prerequisite, even though it lands in Phase 1.
4. **Allocate review:** monk reviews each phase; main coordinates;
   cboos merges.

Once approved, I'll start with Phase 1 on a new branch
`wf/ghosting/skill-fold` from `main` (this `dev/ghosting-epic`
branch is just the planning scratchpad — its only commit will be
this doc).

---

## 9. Pointers

- [refactor-reindex-with-ghosting.md](refactor-reindex-with-ghosting.md)
  — the original problem statement.
- [simplify-converter-renderer.md §3 opp 12 + §6 + §7](simplify-converter-renderer.md)
  — D12 gate context, verifier rejections, single-axis end-state.
- [dev-docs/rendering-architecture.md](../dev-docs/rendering-architecture.md)
  — the pipeline overview.
- [dev-docs/plugins.md §6](../dev-docs/plugins.md) — per-class
  `detail_visibility` mechanism.
- [PR #131](https://github.com/daaain/claude-code-log/pull/131) +
  [PR #132](https://github.com/daaain/claude-code-log/pull/132)
  — the regressions that motivated the epic.
