# Plan: support dynamic-workflow transcripts (issue #174)

**Status:** greenlit & in progress — PR0 (nested DOM, #191) and PR1
(parsing/models, #203) have landed; PR2 (tool-input + async-body rendering)
is underway. *The §1–§9 narrative below was written 2026-05-31 and describes
the pre-implementation baseline as of that date; it is kept as the design
record rather than rewritten per landed PR.*
**Branch (original plan):** `dev/dynamic-workflow-support` (off `main`);
implementation lands as the per-PR branches in the §9 sequence.
**Scope:** parse & render Claude Code *dynamic Workflow* runs so a
workflow tool_use no longer collapses to a single async-launch card +
final-answer blob, but shows its phases and the dozens of sub-agents
fired underneath.

This doc is deliberately **workflow-shaped** (§7): the phases below map
onto `Map → Design → Implement → Verify` stages so the implementation
can itself be run as a dynamic workflow once greenlit.

---

## 1. What a workflow run leaves on disk (verified)

All paths are relative to a session's transcript dir, `<session>/`
(the dir named after the trunk `sessionId`). Verified against a real
40-agent run (`simplify-converter-renderer`, runId `wf_<id>`).

```
<session>/
  <session>.jsonl                      ← trunk transcript (the Workflow tool_use lives here)
  subagents/
    workflows/
      <runId>/
        journal.jsonl                  ← LIVE append-only spine (started/result events)
        agent-<agentId>.jsonl          ← per-agent side-channel transcript  (×N)
        agent-<agentId>.meta.json      ← {"agentType":"workflow-subagent"}   (×N)
  workflows/
    scripts/<workflowName>-<runId>.js  ← the JS orchestrator
    <runId>.json                       ← TERMINAL snapshot (phases + per-agent metadata)
  tool-results/<taskId>.txt            ← spilled oversized tool results (already a thing)
```

Observed counts for the example: 42 `agent-*.jsonl` + 42 `agent-*.meta.json`,
`journal.jsonl` = 82 lines, `<runId>.json` reports `agentCount: 40`
(42 started, 40 produced results — 2 were retried/abandoned; see the
`attempt` field).

### 1.1 `journal.jsonl` — the live spine

Newline-delimited, exactly two event types, no phase information:

```jsonc
{"type":"started","key":"v2:<hash>","agentId":"<id>"}
{"type":"result", "key":"v2:<hash>","agentId":"<id>","result": <dict|str>}
```

- `agentId` is the join key to everything else.
- `result` is the **full** agent output — a structured dict (e.g.
  `{area, summary, key_functions[], invariants[], complexity_smells[],
  opportunities[…]}`) for `StructuredOutput` agents, or a plain string
  (the synthesize agent's markdown plan). This is the data missing from
  `<runId>.json` (which truncates it to `resultPreview`).
- Append-only and present from the start of the run → **this is the
  only source that lets us snoop a running workflow.**

### 1.2 `<runId>.json` — terminal snapshot (phases + metadata)

Top-level keys: `runId, taskId, status, workflowName, timestamp,
startTime, durationMs, agentCount, totalTokens, totalToolCalls,
defaultModel, script, scriptPath, phases[], workflowProgress[], logs[],
result, summary`.

- `phases`: `[{title, detail}]` — e.g. `Map / Verify / Synthesize`.
- `workflowProgress`: flat list mixing two node types:
  - `{type:"workflow_phase", index, title}`
  - `{type:"workflow_agent", index, label, phaseIndex, phaseTitle,
     agentId, model, state, startedAt, queuedAt, attempt, lastToolName,
     lastToolSummary, promptPreview, lastProgressAt, tokens, toolCalls,
     durationMs, resultPreview}`
- **Phase membership lives only here**, via `phaseIndex`/`phaseTitle`
  on each `workflow_agent`. The join back to journal/agent files is
  `agentId`.
- `result`: `{plan, areaCount, opportunityCount}` (the final answer,
  same as the async notification body).

### 1.3 Incremental vs. final — **resolved with mtime evidence**

cboos's open question ("presumably `<runId>.json` only appears once the
workflow is finished… I'll see at the next one"). Decisive evidence from
the example run:

| File | mtime |
|---|---|
| run dir created / script written | 09:24:46 |
| last `journal.jsonl` write | **12:20:10** |
| `<runId>.json` written | **12:35:10** |
| async-completion notification fired | 12:35:10 |

`<runId>.json` appears **15 minutes after the final journal write**, at
the exact completion instant, carrying `status:"completed"` and every
agent in `state:"done"`. **Conclusion: `<runId>.json` is a one-shot
terminal serialization, not an incrementally-updated file.** The plan
therefore treats `journal.jsonl` as the authoritative live spine and
`<runId>.json` as an *optional enrichment* present only post-completion.
This confirms cboos's "use both, journal-led" lean.

> Caveat worth one line in the PR: this is one run. The contract isn't
> documented upstream; we should re-confirm on the next captured run
> that `<runId>.json` doesn't get rewritten mid-flight. The design
> degrades gracefully either way (it never *requires* `<runId>.json`).

### 1.4 `agent-<id>.jsonl` — standard side-channel transcripts

Same shape as existing sub-agent transcripts: `user`/`assistant`/
`attachment` entries with `message.content` carrying `tool_use` /
`tool_result`, plus `isSidechain:true`, `parentUuid`, `uuid`,
`sessionId`, `agentId`. In the example they use Read/Bash/StructuredOutput
and **do not spawn further sub-agents** — so nesting bottoms out at
exactly the depth cboos drew (no unbounded recursion to design for in
v1, though the recursive loader handles it for free if it ever appears).
`agent-<id>.meta.json` is currently just `{"agentType":"workflow-subagent"}`
— a useful type discriminator, nothing more.

---

## 2. What the code does today (and where it breaks)

*As of 2026-05-31 (pre-implementation baseline):* `grep -rni workflow
claude_code_log/` → **0 hits**; no workflow handling existed. (PR1 / #203
has since added `claude_code_log/workflow.py`, which parses runs into
`WorkflowRun` models but does not yet render them.) The gaps this plan
addresses were, concretely:

1. **The Workflow tool_use** (`name:"Workflow"`, `input.script` = JS)
   falls through `create_tool_input` to the raw `ToolUseContent`
   fallback — the JS renders as an unhighlighted blob.
2. **The Workflow tool_result** (`status:"async_launched"`, with
   `runId`/`taskId`/`transcriptDir`/`scriptPath`) hits the **generic
   async-launch path already specialized for #90** — that's the
   "specialized since 1.3.0 but not enough" cboos refers to. The later
   `🔄 Async result …` notification is folded onto the launch card by
   `_link_async_notifications` (`renderer.py:2588`). Net effect: one
   launch card + one final answer; **the 40 agents in between are
   invisible.**
3. **The loader never finds the agent files.** The sidecar glob is
   `directory.glob("*/subagents/*.jsonl")` (`converter.py:127`) and the
   per-session resolver looks in `<session>/subagents/agent-*.jsonl`.
   Neither descends into `subagents/workflows/<runId>/`, so the 42
   `agent-*.jsonl` are never loaded.
4. **The hierarchy model can't express the needed nesting.** Rendering
   is a *hybrid*: `_build_message_tree` (`renderer.py:2173`) builds a
   real nested tree, but `HtmlRenderer._flatten_preorder`
   (`html/renderer.py:1148`) flattens it to a flat list and the template
   (`transcript.html`) emits siblings with `d-{ancestor}` ancestry CSS
   classes + JS fold — **not nested DOM**. Depth is governed by the
   hardcoded 0–5 table in `_get_message_hierarchy_level`
   (`renderer.py:1974`): session=0, user/teammate=1, assistant=2,
   tool=3, sidechain conv=4, sidechain tool=5, default=2. The required
   workflow nesting is **6 tiers** —
   `Workflow-tool(≈3) > workflow_phase > workflow_agent >
   side-channel-agent(≈4) > side-channel-tool(≈5)` — and crucially the
   two middle tiers (`workflow_phase`, `workflow_agent`) **have no
   message type at all today.** A monotonic level-stack
   (`_build_message_hierarchy:2069`) also means 40 agents at the same
   level would all collapse under the last anchor without the kind of
   relocation pass `_relocate_subagent_blocks` (`renderer.py:1814`)
   already does for teammates.

---

## 3. Design decisions

### D1 — Data strategy: journal-led, `<runId>.json`-enriched ✅
Parse `journal.jsonl` as the spine (live, has full results, keyed by
`agentId`). When `<runId>.json` is present, enrich each agent with its
phase (`phaseIndex`/`phaseTitle`) + metadata (tokens/toolCalls/
durationMs/model/state/label) and synthesize the `workflow_phase`
grouping nodes. When it's absent (running workflow), render a flat
"WIP" view: agents grouped only as "in this run", ordered by journal
appearance, no phase headers. **Never require `<runId>.json`.**

### D2 — Rendering hierarchy: **TRUE NESTED rendering** ✅ DECIDED (firm)
**cboos's decision** (2026-05-31): true nested rendering,
**firm "whatever the blast radius is."** Rationale:
- Nested rendering has been a long-standing wish (`work/rendering-next.md`
  §1 "Recursive Template Rendering"); what was missing was a use case
  that *required* it — dynamic-workflow rendering is exactly that case.
- D2-alt (flat-extended) would only *increase* complexity, at odds with
  the converter/renderer simplification effort. Nested is technically
  sound and opens the way to a longer-term incremental-update approach.

**Consequence for this plan:** the snapshot blast-radius estimate is
**no longer a gate on the D2 decision.** Phase A's mapping is used only
to **sequence** the work — land the nested-DOM conversion as an
**isolated, behaviour-preserving pure-refactor PR FIRST**, then build
workflow rendering on top.

How it's scoped:
- The flat `d-N` model is already at its ceiling (level 5) *before*
  workflows add two new tiers — so extending it is a dead end anyway.
- `_build_message_tree` *already produces a genuine tree*; the
  flattening is purely a rendering choice in `_flatten_preorder` +
  template. Teach the HTML template to recurse over `children` (nested
  `<div>`s; see the macro sketch in `work/rendering-next.md` §1) instead
  of consuming the pre-flattened list — removes the depth ceiling **for
  all message types at once**, and fold/unfold JS keys off DOM nesting
  instead of `d-N` ancestry.

*D2-alt (flat-extended) is rejected and kept here only as a record of
the path not taken: add `workflow_phase`/`workflow_agent` levels, make
workflow side-channel levels relative, add a `_relocate_workflow_blocks`
pass. Rejected because it entrenches the model the issue calls "the
limit" and adds complexity.*

### D3 — Workflow tool_use: pygmentize the JS `script` ✅
Add a `Workflow` entry to the tool-input registry; render `input.script`
as a Pygments-highlighted JavaScript block (+ surface `meta.name`/
`description`/`phases` as a header). Mechanically identical to existing
specialized tool-input renderers.

### D4 — Async result rendering: JSON-shaped → pygmentize ✅
For the async result body, apply cboos's heuristic: a payload that
starts with `{"` (possibly truncated) → treat as JSON and Pygmentize.
Keep the existing async-fold; just improve the body renderer. Reuse the
spilled-`tool-results/<taskId>.txt` mechanism for the full untruncated
result if present.

### D5 — Tie agents to phases ✅
Join on `agentId`: journal gives result + order; `<runId>.json`
`workflowProgress` gives `phaseIndex`/`phaseTitle` + metadata. Group
agents under `workflow_phase` nodes built from `phases[]`. Side-channel
transcript for each agent loaded from
`subagents/workflows/<runId>/agent-<agentId>.jsonl` via the existing
recursive `load_transcript`.

### D6 — AskUserQuestion regression ✅ DECIDED: out of scope (issue #180)
The same issue comment mentions an AskUserQuestion rendering regression
and a "collapse answers into the input side" idea. It does **not**
intersect the workflow work (no `AskUserQuestion` in workflow data), and
it's already specialized in code (`markdown/renderer.py`
`format_AskUserQuestionInput` / `format_AskUserQuestionOutput`).
**cboos's call: out of this scope — tracked as issue #180. Do not touch
it here.**

---

## 4. Loader & parser changes (sketch, for sizing only)

- **Discovery:** extend sidecar discovery to also match
  `*/subagents/workflows/*/agent-*.jsonl` (and the per-session
  resolver to look under `subagents/workflows/<runId>/`). Collect
  `runId`s seen.
- **Workflow run model:** new parse step keyed off the **`runId` in the
  Workflow `tool_result`** (`toolUseResult.runId`/`taskId`/`status:
  "async_launched"`) — **not** the tool_use input, which carries only
  `script` (verified Phase A). Read `journal.jsonl` → `{agentId:
  result}`; read `<runId>.json` if present → phases + per-agent
  metadata; load each `agent-<id>.jsonl` as a recursive sidechain;
  assemble a `WorkflowRun` (phases → agents → side-channel entries).
- **Models:** `WorkflowToolInput` (script + meta), `WorkflowRun`,
  `WorkflowPhase`, `WorkflowAgent` (+ `message_type`s
  `workflow_phase`/`workflow_agent` if D2-alt, or tree nodes if D2).
- **Splice:** insert the run under the trunk Workflow tool_result
  anchor (same anchor `_link_async_notifications` already finds), so the
  async fold and the new expansion coexist.
- **Public hygiene:** test fixtures must be **synthesized/sanitized** —
  no real absolute paths, no private session ids. Build a small
  `test/test_data/workflow_*` fixture (1 phase-set, ~3 agents, 1
  structured + 1 string result) mirroring the verified schemas in §1.

---

## 5. Risks / open items

1. **Snapshot blast radius (D2).** Nested DOM will move a lot of HTML.
   No longer a *decision* gate (D2 is firm), but still a real cost to
   manage: land the pure-refactor first and update snapshots
   `--snapshot-update` **serially** (`-n0`, per CONTRIBUTING — parallel
   `--snapshot-update` corrupts `.ambr`). Verify the refactor is
   behaviour-preserving (DOM shape may change; rendered *content* must
   not).
2. **`<runId>.json` lifecycle** unconfirmed beyond one run (§1.3).
   Design degrades gracefully, but re-verify on the next capture.
3. **Truncated results.** `<runId>.json.resultPreview` and the async
   body are truncated; always prefer journal `result` / spilled
   `tool-results/<taskId>.txt` for full content.
4. **Timeline component** (`templates/components/timeline.html`) parses
   message types from CSS classes — new workflow types need timeline
   detection updated (per CLAUDE.md parity rule).
5. **`dev-docs/` sync** — add a workflow section to the rendering/agents
   docs in the same PR (per repo convention).

---

## 6. Acceptance criteria

- A trunk transcript containing a `Workflow` tool_use renders: the JS
  script highlighted; phases as group headers; each agent under its
  phase with tokens/tool-calls/state; each agent's side-channel
  transcript expandable to its own tool_use/tool_result.
- A *running* workflow (no `<runId>.json`) renders a journal-only WIP
  view without error.
- Async final answer still folds onto the launch card (no regression to
  #90 behaviour); JSON body pygmentized.
- New synthesized fixture + snapshot; `just ci` clean.

---

## 7. Workflow-oriented execution plan (phases)

Structured so this can run as a dynamic workflow.

**Phase A — Map (parallel readers).** One agent each over:
(1) loader/discovery (`converter.py` glob + sidechain resolution),
(2) hierarchy/tree (`renderer.py` levels + `_build_message_tree` +
`_relocate_subagent_blocks`), (3) flatten/template
(`html/renderer.py:_flatten_preorder` + `transcript.html` + fold JS),
(4) tool-input/output registries + async fold, (5) models &
factories. Each returns: touch-points, the minimal change for its area,
and a snapshot-impact estimate. **Purpose of the estimate:** *sequencing*
only (D2 is firm) — confirm the nested-DOM pure-refactor is a clean
standalone first PR and size its snapshot churn.

**Phase B — Design (synthesize).** One agent merges Map outputs into a
concrete change-list + the synthesized test fixture spec, ordered so
the nested-DOM refactor lands first, then parsing, then workflow render.

**Phase C — Implement (pipelined).** PR0 nested-DOM pure-refactor
(behaviour-preserving) lands and merges first; then stage 1 parse/loader
+ models + fixture; stage 2 tool-input (D3) + async body (D4); stage 3
workflow rendering on the nested DOM + timeline parity; stage 4
dev-docs. Each stage gated on its own unit tests before snapshot work.

**Phase D — Verify (adversarial).** One agent re-derives the expected
HTML from the schemas in §1 independently and diffs against the
rendered output; one runs `just ci` + serial snapshot update; one
checks the running-workflow (journal-only) path and the no-regression
async fold.

---

## 8. Greenlight status (GREENLIT 2026-05-31)

cboos read the plan, approved it, and made the calls:

1. ✅ **D1** journal-led + `<runId>.json` enrichment — approved
   ("what we need").
2. ✅ **D2** TRUE NESTED rendering — **firm, blast-radius is not a
   decision gate.** Sequence: isolated pure-refactor PR first, then
   workflow rendering on top.
3. ✅ **D6** out of scope — issue #180.
4. ✅ Implementation may proceed.

Each landable chunk lands as its own branch/PR off `main` and is
reviewed before merge by the maintainer.

---

## 9. Phase A findings (mapping run, 2026-05-31)

Five parallel read-only readers mapped the subsystems. Outcome confirms
the plan; refinements below. The blast-radius estimate is **sequencing
input only** (D2 firm).

**Nested-DOM refactor (PR0) snapshot impact: HIGH but purely
structural.** All 8 HTML snapshot cases change (every message div drops
its `d-N` ancestry classes; nesting moves into the DOM tree). Fold-bar
HTML and rendered *content* (text/code/tables) are unaffected — no
logic/rendering regression, just structure. → land PR0 alone, regen
snapshots serially (`-n0`), review the diff is structure-only.

**Per-area refinements:**
1. **Loader/discovery** (low snapshot): extend `_scan_sidechain_uuids`
   glob (`converter.py:131`) to also match
   `*/subagents/workflows/*/*.jsonl` (covers both `agent-*.jsonl` and
   `journal.jsonl` — `Path.glob()` has no `{a,b}` brace expansion, so a
   single suffix glob is used rather than a brace set); add a
   `_load_workflow_runs` pass in `load_directory_transcripts` *after*
   main load, *before* `_integrate_agent_entries` — extract Workflow
   tool_uses, get `runId` **from the tool_result**, load journal +
   optional `<runId>.json`, recursively load each `agent-<id>.jsonl`,
   join on `agentId`.
2. **Hierarchy/tree** (medium): `_build_message_tree`
   (`renderer.py:2173`) already builds a real parent/child tree, so the
   nested-DOM path can derive depth from tree position and the 0–5
   integer table (`_get_message_hierarchy_level:1974`) can be retired
   for the workflow path; synthesize `workflow_phase`/`workflow_agent`
   nodes and generalize `_relocate_subagent_blocks` (`renderer.py:1814`)
   to a workflow-aware relocation.
   - **Open Q (for PR0 scope):** does the non-workflow path *also*
     switch to tree-depth (cleaner, bigger snapshot churn) or keep the
     table for back-compat? Resolve in Phase B.
   - **Open Q:** can `workflow_phase` nodes be synthetic (no backing
     JSONL entry) without breaking DAG invariants? Resolve in Phase B.
3. **Tool registries + async fold**: register a `Workflow` input model
   (pygmentize `input.script` as JS; surface `meta.name/description/
   phases`); improve the async body renderer to detect JSON-shaped `{"`
   and pygmentize; splice the `WorkflowRun` under the **same**
   tool_result anchor `_link_async_notifications` (`renderer.py:2588`)
   already finds, so async-fold and workflow-expansion coexist.
4. **Models**: `WorkflowToolInput` (script + parsed meta), `WorkflowRun`
   (runId, phases, agents, result), `WorkflowPhase` (index, title,
   agents), `WorkflowAgent` (agentId, label, phaseIndex, model, state,
   tokens, toolCalls, result, side-channel entries) — the two node
   types as `MessageContent` subclasses so they thread into the tree.

**Sequenced PR plan (refined):**
- **PR0** — nested-DOM pure refactor (behaviour-preserving), no workflow
  code. Resolves the two open Qs in §9.2 as part of its design.
- **PR1** — parsing/loader + models + synthesized fixture (no render).
- **PR2** — Workflow tool-input (JS highlight) + async body JSON.
- **PR3** — workflow rendering on the nested DOM + timeline parity.
- **PR4** — dev-docs sync.
