# PR3 design — render the WorkflowRun tree (issue #174, final PR)

Branch: `dev/workflow-tree-render` off main `af7dc29` (PR0+PR1+PR2 landed).
Scope: splice the parsed `WorkflowRun` (phases → agents → each agent's
side-channel transcript) into the message tree at the Workflow tool_use/result
site, on PR0's nested DOM; + snapshot-first header refinement.

## Architecture decisions (locked)

**Strategy B — self-contained sub-tree, spliced post-`_build_message_tree`.**
Do NOT route workflow agents through `_integrate_agent_entries` /
`_build_message_hierarchy` / `_relocate_subagent_blocks` (the 0–5 level-stack
can't express phase→agent→sidechain and the blast radius on non-workflow
rendering is high). Instead build the workflow sub-tree separately and attach
it as `.children` of the Workflow tool_use node after the main tree is built.

### Step 1 — load + link (foundation)
- `converter.load_directory_transcripts`: after the tree is built, call PR1's
  `load_workflow_runs(directory_path)` and stash `{run_id: WorkflowRun}` on the
  `SessionTree` (new field `workflow_runs`, default `{}`).
- `renderer.generate_template_messages`: read `session_tree.workflow_runs`.
- Link each run to its Workflow tool_use: the `runId` is on the tool_RESULT's
  `toolUseResult` (`status: async_launched`), same anchor `_link_async_notifications`
  uses. Find the Workflow `ToolUseMessage` paired with that result; stash the
  `WorkflowRun` on it (e.g. `ToolUseMessage.workflow_run`).

### Step 2 — snapshot-first header (cboos refinement)
- `format_workflow_input` / `MarkdownRenderer.format_WorkflowToolInput`: when a
  linked `WorkflowRun` with a snapshot is present, use its `workflow_name` +
  `phases[].title` for the header (authoritative); else fall back to the
  JS-`meta` regex (`parse_workflow_meta`) for the running/no-snapshot case.
- **Warn** when the JS-meta parse misses expected fields (format-drift signal).
- **Back-fill**: prefer JSON when available; regex is the running-only fallback.

### Step 3 — tree splice (the core)
New `MessageContent` subclasses (in models.py) so they thread into the tree and
dispatch via `format_<ClassName>`:
- `WorkflowPhaseMessage` (title, detail, counts) → phase-header card.
- `WorkflowAgentMessage` (label, model, state, tokens, tool_calls, result) →
  agent card with its result (StructuredOutput dict pygmentized / string md).
Splice pass (after `_build_message_tree`, before render):
- For each Workflow tool_use node with a linked run, synthesize a
  `WorkflowPhaseMessage` TemplateMessage per phase; under each, a
  `WorkflowAgentMessage` per agent; under each agent, the agent's side-channel
  entries rendered into TemplateMessages (reuse the factory→TemplateMessage path
  on `agent.entries`) nested as children. Attach phase nodes as `.children` of
  the tool_use (or tool_result) node.
- Assign `message_index` to synthetic nodes from a high non-colliding counter;
  set `.children` directly (we're past `_build_message_tree`, so ancestry isn't
  needed — just populate `.children` + `message_id`/`should_render`).
- Timeline parity: add the new CSS classes to `components/timeline.html`
  detection.

### Verification
- New fixture already exists: `test/test_data/workflow_basic` (PR1).
- Tests: run discovered+linked; phases/agents/sidechains nested under the
  tool_use; header snapshot-first + warn + fallback; HTML + Markdown.
- Snapshot regen serially (`-n0`); review diff.
- `just ci` green (ty warnings-only/exit-0).

## Open risk
- `message_index` allocation for synthetic nodes must not collide with existing
  indices (anchors/timeline). Use `max(existing)+1...` counter.
- Side-channel entries → TemplateMessages: simplest is a recursive
  `generate_template_messages(agent.entries)` and graft its non-session-header
  nodes; verify it doesn't emit spurious session headers per agent.

---

## STATUS (2026-06-07) — steps 1-2 DONE, step 3 NOT STARTED

Branch `dev/workflow-tree-render` (off main `af7dc29`):
- `6155d0e` step 1 — load + attach `SessionTree.workflow_runs` + this doc.
- `ac56ccb` step 2 — taskId linkage (`_link_workflow_runs`) +
  `resolve_workflow_header` (snapshot-first, warn-on-drift) used by both
  renderers; fixture tool_result content fixed to real-data shape.
- (this commit) — step-3 implementation map below.

Steps 1-2 verified: 35 workflow tests, pyright 0, ruff clean. NOT pushed
(keep fresh-PR-auto-CR for when PR3 is whole).

## Step 3 implementation map (wiring points located — build this next)

**A. Node types** (`models.py`, after `ToolUseMessage` ~L1141, before the
Tool Input Models section): two `@dataclass(MessageContent)` subclasses
(base needs `meta: MessageMeta` first; use `MessageMeta.empty()` for synthetic
nodes; override `message_type`):
- `WorkflowPhaseMessage(title, detail, agent_count)` → `message_type =
  "workflow_phase"`.
- `WorkflowAgentMessage(label, model, state, tokens, tool_calls, result,
  result_preview)` → `message_type = "workflow_agent"`.

**B. CSS classes** (`html/utils.py`): add both types to `CSS_CLASS_REGISTRY`
(L62) → `["workflow_phase"]` / `["workflow_agent"]`. `css_class_from_message`
(L125) → `_get_css_classes_from_content` reads the registry, so the cards get
those classes automatically. Add `.workflow_phase` / `.workflow_agent` styling
to `components/message_styles.css`.

**C. Formatters + titles** — dispatch is `format_<ClassName>` /
`title_<ClassName>` via `_dispatch_format` (`renderer.py` L4457) /
`_dispatch_title` (L4471). Add to BOTH `HtmlRenderer` and `MarkdownRenderer`:
- `format_WorkflowPhaseMessage` / `title_WorkflowPhaseMessage` (header card:
  title + detail + "N agents").
- `format_WorkflowAgentMessage` / `title_WorkflowAgentMessage` (label/model/
  state/tokens; body = result — dict → JSON-pygmentize (reuse
  `render_async_result_body`), str → markdown).

**D. Splice pass** (`renderer.py`, new `_splice_workflow_runs(root_messages,
ctx)` called AFTER `_build_message_tree` (~L821), BEFORE
`_link_async_notifications`; guard: only when a Workflow tool_use has
`input.workflow_run`). For each such tool_use TemplateMessage:
  1. counter = `max(message_index over all messages) + 1`.
  2. For each `run.phases` (or flat `run.agents` when no snapshot): build a
     `WorkflowPhaseMessage` TemplateMessage; under it a `WorkflowAgentMessage`
     TemplateMessage per agent; under each agent, render `agent.entries` and
     graft (see E). Allocate `message_index`/`message_id = d-{idx}` from the
     counter for EVERY synthetic + grafted node (re-index to avoid collision).
  3. Set fold-state fields on each synthetic parent: `has_children`,
     `immediate_children_count`, `total_descendants_count`, `should_render=True`,
     `is_paired=False`, `ancestry`/`children` (children populated directly).
  4. Attach phase nodes as `.children` of the tool_use node (or its paired
     tool_result — pick the one that reads best; tool_use keeps it next to the
     script). Recompute the tool_use's `has_children`/counts.

**E. Agent side-channel → TemplateMessages**: call
`generate_template_messages(agent.entries)` → take each session-header root's
`.children` (skip the synthetic session header) and graft under the agent node,
RE-INDEXING every grafted node's `message_index`/`message_id` from the counter
(walk `.children` recursively). Verify no spurious per-agent session header
leaks into the output.

**F. Timeline parity** (`components/timeline.html`): add `workflow_phase` /
`workflow_agent` to `messageTypeGroups` (L24) AND a detection branch in the
class→type chain (L56-95) — they carry only their own class, so add explicit
`classList.includes('workflow_phase'|'workflow_agent')` branches before the
generic `.find`.

**G. Watch-points (main):**
1. Timeline — done via B+F (registry class + timeline detection).
2. Fold/unfold — the spliced sub-tree uses the SAME nested-DOM `.children`
   structure (#191), so the existing fold machine works IFF fold-state fields
   (C-step3) are set. Verify fold/unfold of a phase via Playwright.
3. Non-workflow byte-identical — the splice is gated on `input.workflow_run`
   (only set for directory loads with a run), so non-workflow snapshots must be
   unchanged. Re-run snapshot suite; diff should be empty for non-workflow
   fixtures (workflow_basic isn't a snapshot fixture).

**Tests**: extend `test_workflow_rendering.py` — directory render of
workflow_basic shows phase/agent cards nested under the Workflow tool_use; each
agent's side-channel entries present beneath it; HTML + Markdown; a fold
Playwright test. Snapshot regen serial (`-n0`); `just ci` green.

**EXACT NEXT ACTION**: implement A→B→C (node types + registry + formatters +
CSS) first and unit-test rendering a synthesized node; THEN D→E (the splice +
re-index) — the highest-risk part; THEN F (timeline) + tests.
