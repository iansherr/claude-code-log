# Spike: Parse / render auto-memory (issue #192)

**Status:** v1 implemented (title + filter only). Scope confirmed by cboos:
title + filter ONLY, dir-browser deferred; dedicated `memory` filter toggle
with timeline lane in lockstep; minor-Q defaults accepted (relative-to-memory/
short path; "N days old" recall badge deferred; Bash refs skipped).

## What v1 ships

- `is_memory_path()` / `is_memory_tool()` / `memory_short_path()` helpers
  (`html/utils.py`), anchored on `/.claude/projects/<slug>/memory/`.
- 🧠 titles for memory Read/Write/Edit in **both** the HTML (`html/renderer.py`)
  and Markdown (`markdown/renderer.py`) renderers.
- `memory` CSS modifier on the tool_use call **and** its tool_result
  (`html/utils.py:_get_css_classes_from_content`).
- Memory file **bodies** (Read result + Write input) render as rendered
  Markdown via the usual collapsible-markdown helper
  (`render_markdown_collapsible`), instead of syntax-highlighted source —
  gated on `is_memory_path` in `html/tool_formatters.py`. (Edit stays as a
  highlighted snippet/diff — it's a partial region, not a full doc.)
  Relative links in those bodies (e.g. a `MEMORY.md` link to a sibling
  `feedback_x.md`) are anchored to the memory file's own directory via
  `resolve_memory_body_links`, so they resolve under `…/<slug>/memory/`
  instead of the transcript page's directory `…/<slug>/`.
- `memory` filter toggle (`transcript.html`) behaving as a cross-cutting
  modifier (like `sidechain`) that both hides memory noise and isolates
  memory-only; `memory` timeline lane (`components/timeline.html`) in lockstep.
- Tests: `test/test_memory_rendering.py` (titles + CSS), `test/test_memory_browser.py`
  (toggle hide/isolate + timeline lane), `test/test_data/memory_interactions.jsonl`.

## Follow-ups (deferred, not in v1)

- **Custom `autoMemoryDirectory`** relocates memory outside the default path,
  so the path-anchored regex won't detect it. Needs config plumbing — deferred.
  (Noted on `_MEMORY_PATH_RE`.) Windows backslash paths *are* handled
  (separators normalized before matching; covered by a test).
- **Render the memory directory itself** (issue Q2) — standalone browser of
  `MEMORY.md` + topic files. Bigger feature; deferred.
- **"N days old" recall badge** from the `<system-reminder>` age marker.
- **Bash** references to memory paths (path lives in the command string).

---

## Original investigation (kept for reference)

## The question (issue #192)

1. Do transcripts capture Claude interacting with [auto-memory](https://code.claude.com/docs/en/memory#auto-memory)?
2. If so, should we also parse/render the files in the memory directory?

## Findings

### 1. Yes — but as plain Read/Write/Edit, not a dedicated tool

There is **no "memory" tool**. Auto-memory is "basically a few prompts"
(daaain's words, confirmed). In transcripts it shows up as ordinary
`Read` / `Write` / `Edit` tool calls whose `file_path` points into the
memory directory. (One `Bash` call also referenced a memory path — path
lives in the command string, not a `file_path` field.)

Verified against 14 real projects under `~/.claude/projects/*/memory/`.
Example shapes (this project's transcripts):
- `Write` → `{file_path, content}`, path `…/memory/project_x.md`
- `Edit`  → `{file_path, old_string, new_string, replace_all}`
- `Read`  → `{file_path}` (+ optional `offset`/`limit`)

### 2. Two distinct signals

- **"Writing memory"** = `Write`/`Edit` to a memory file.
- **"Recalled memory"** = `Read` of a memory file. The matching
  `tool_result` is **prefixed with a `<system-reminder>`**:
  > `<system-reminder>This memory is 3 days old. Memories are
  > point-in-time observations…</system-reminder>`
  — an extra, reliable recall marker (carries an age we could surface).

### 3. Storage location (key architectural fact)

`~/.claude/projects/<project-slug>/memory/` — a **sibling of the JSONL
transcripts claude-code-log already processes**. `<slug>` is the same
slugified repo path; shared across worktrees. Layout:
`MEMORY.md` (index, loaded every session, first 200 lines / 25 KB) +
topic `*.md` files (loaded on demand). All plain markdown.

Customizable via `autoMemoryDirectory` setting (absolute or `~/`-path) —
edge case that breaks a fixed-path heuristic; see limitations.

The pipeline globs only `*.jsonl` (+ `*/subagents/*.jsonl`), so the
`memory/*.md` files are **invisible to the tool today**; their content
reaches the output only inline, as the tool-call content already rendered.

### 4. False positives (daaain's concern)

Detecting on the full path `…/.claude/projects/<slug>/memory/…` (not a
bare `memory/` substring) prevents a random repo `memory/` dir from
matching. This is self-contained per tool call — no need to thread the
project path through the renderer.

## Proposed approach (matches the cboos + daaain convergence in the issue)

Two thin, low-risk changes on **existing extension points**. Both keyed
off one helper:

```python
# matches the default auto-memory location
_MEMORY_PATH_RE = re.compile(r"/\.claude/projects/[^/]+/memory/")
def is_memory_path(file_path: str | None) -> bool: ...
```

**A. Title (cboos's proposal)** — in `title_ReadInput` /
`title_WriteInput` / `title_EditInput` (`html/renderer.py:984-1008`),
when the path is a memory path, swap the emoji to 🧠 and shorten:
`🧠 Read memory MEMORY.md`, `🧠 Write memory project_x.md`
(short-path = path relative to the `memory/` dir).

**B. Filter / analyse (daaain's main interest)** — add a dynamic
`memory` CSS modifier in `_get_css_classes_from_content`
(`html/utils.py:101`), parallel to the existing `error` modifier on
`ToolResultMessage`. That makes memory tool calls filterable. Per
CLAUDE.md, the **filter toolbar** entry and the **timeline** message-type
detection must be updated in lockstep with any new CSS class.

## Scope questions (confirm before I implement)

1. **Render the memory dir itself?** (issue Q2) Recommend **no for v1** —
   content already shows inline via the tool calls; a standalone
   memory-browser page is a separate, larger feature. Defer.
2. **Filter UX:** dedicated "memory" toolbar toggle, or just emit the CSS
   class for ad-hoc analysis? daaain wants to "filter these to analyse
   usage" → leaning toolbar toggle.
3. **Title short-path:** relative-to-`memory/` (e.g. `sub/dir/x.md`) or
   bare basename? I propose relative-to-`memory/`.
4. **Recalled-memory age:** surface the "N days old" marker (badge)? Nice
   -to-have, optional.
5. **Bash refs:** skip for v1 (rare; path is in the command string, not a
   typed field).

## Limitations

- Custom `autoMemoryDirectory` won't match the default-path regex. Could
  be made configurable later; flag as known gap for v1.
