# Obsidian-friendly output (issue #151)

## Status: Shipped (impl + tests in this PR; follow-ups recorded below)

## Context

Issue #151 wants three CLI flags that project Claude Code transcripts into
the same Markdown-vault topology Obsidian (and similar Markdown-based KM
tools) expect:

```
claude-code-log --output ~/Documents/Obsidian/ClaudeProjects \
                --expand-paths --filter-path /home/joe \
                --format md --detail low --compact
```

Should land sessions at:

```
~/Documents/Obsidian/ClaudeProjects/project/A/<session>.low.md
```

The use case is the user's wider knowledge-management workflow — SAM
(the federation coordinator at `~/SAM`) and the Obsidian vault at
`~/Documents/Obsidian/Work` keep cross-project knowledge in a Markdown
tree; this feature gives Claude Code transcripts a clean projection
into that topology.

## What's already there vs. what's missing

The issue's framing implies `--output` already works for `--all-projects`
mode and produces a flat structure. **Empirically that's not what the
code does.** `claude_code_log/converter.py:process_projects_hierarchy`
writes `combined_transcripts.html` directly into each *source*
`project_dir` (e.g. `~/.claude/projects/-home-joe-project-A/`), and the
index lands at `~/.claude/projects/index.html`. `--output` is honoured
by the single-file/single-project paths (`convert_jsonl_to`,
`convert_single_session`) but is **not threaded through to
`process_projects_hierarchy`** (cli.py around line 816).

So #151's three "flag flavours" actually decompose into:

1. `--output <dir>` honoured in `--all-projects` mode (currently a gap).
2. `--expand-paths` — undo Claude Code's flat encoding of project dirs.
3. `--filter-path <prefix>` — select subset + truncate prefix.

The plan addresses all three; (1) is partially a prerequisite of (2)/(3).

## Implementation surface

### `claude_code_log/cli.py`

- Add two `@click.option` declarations:
  - `--expand-paths` — `is_flag=True`, default False.
  - `--filter-path` — `type=str` (path-like), default None.
    Optional but `--expand-paths` is a soft-prerequisite (filter
    truncation only meaningful with expansion). Decision: allow either
    flag standalone; document the behaviour matrix (see §Scope).
- Pass both into `main()` and forward to `process_projects_hierarchy`
  (and to `convert_jsonl_to` if we decide to support flat-output for
  the single-directory path too).
- Validation: warn if `--expand-paths` / `--filter-path` are given
  without `--output` *and* without `--all-projects` (no-op flags).

### `claude_code_log/converter.py`

- `process_projects_hierarchy` gains four new parameters:
  - `output_dir: Optional[Path]` — destination root (was missing entirely).
  - `expand_paths: bool` — flag.
  - `filter_path: Optional[str]` — prefix.
  - (Optional) `path_resolver: Optional[Callable]` — injection point
    for tests, defaulting to a real implementation that consults the
    cache for the authoritative `cwd`.
- Inside the per-project loop, just before computing `output_path`,
  decide the **destination directory for this project's outputs**
  using a small helper (see §Path-projection logic). Replace the
  current hard-coded `project_dir / "combined_transcripts.html"` with
  `dest_dir / "combined_transcripts.html"` where `dest_dir = project_dir`
  in the legacy flat case and the projected path in the new case.
- The index file (`projects_path / get_index_filename(...)`) likewise
  needs a destination decision (see §Index page question).
- Filtering: when `filter_path` is set and a project's resolved path
  does not start with the prefix, **skip it** (don't emit anything).

### Renderers

Format-agnostic: HTML, Markdown, and JSON renderers all consume the
final destination path from `converter.py`. None of them need changes
for #151. The flag is triggered via the CLI; the renderer doesn't
care whether its output lives in `~/.claude/projects/<flat>/` or
`~/Obsidian/<expanded>/`.

### Tests

- New `test/test_path_projection.py` (unit) — exercises the helper
  with a mix of real-corpus names from `~/.claude/projects/` plus
  synthesised edge cases.
- New `test/test_obsidian_output.py` (integration) — drives the CLI
  end-to-end with a tmp `--output` and asserts the directory tree
  matches the expected projected shape for each flag combination.

---

## Path-projection logic

This is the load-bearing piece. Three subtleties make it more than a
mechanical inverse:

### Subtlety 1: Claude Code's encoding is lossy

The forward direction (real path → flat name) is documented in
`cli.py:convert_project_path_to_claude_dir`:

- `/` → `-`
- `.` → `-` (effectively — see real-corpus samples below)
- Leading `-` is the path-root marker.

Confirmed against `~/.claude/projects/`:

| Real path | Encoded form |
|---|---|
| `/home/cboos/bin` | `-home-cboos-bin` |
| `/home/cboos/.claude` | `-home-cboos--claude` |
| `/home/cboos/Documents/Obsidian/Work/.git` | `-home-cboos-Documents-Obsidian-Work--git` |

Inverting is **fundamentally ambiguous**: `-home-joe-x-y` could mean
either `/home/joe/x/y` (a four-segment path with x and y as dirs) or
`/home/joe/x-y` (a three-segment path with `x-y` as a single dir).
A naïve "dash-as-separator" inverse cannot tell them apart.

### Subtlety 2: The cache *has* the real path; if it doesn't, peek a session

Claude Code records the actual `cwd` in every JSONL entry. Our
SQLite cache aggregates these into `cache.ProjectCache.working_directories`
and `SessionCacheData.cwd`. `convert_project_path_to_claude_dir`'s
forward direction is irrelevant here — for the inverse, we should read
the cache as the source of truth, not parse the encoded name.

When the cache hasn't been populated yet, **peek the first JSONL** in
the project directory: open the file, read just enough lines to find
one entry with a `cwd` field, extract it. No need for the full
`parser.py` model-validation pipeline — we want a single string field,
the entry shape is stable and well-known, a tiny `json.loads(line)
.get("cwd")` loop suffices.

Helper signature:

```python
def project_dir_to_real_path(
    project_dir: Path,
    cache_manager: Optional[CacheManager] = None,
) -> Path:
    """Recover the real on-disk path for a Claude project directory.

    Strategy (in order):
    1. If a cache_manager is available and the project has cached
       `working_directories`, return the first entry. Authoritative
       — that's the actual `cwd` Claude Code recorded at session time.
    2. Otherwise, peek the first JSONL file: read up to N lines,
       json.loads each, return the first non-empty `cwd`. Cheap
       (O(few KB) read, no validation overhead).
    3. Fall back to naïve `/`-for-`-` inversion only as a last
       resort (e.g. project dir has no JSONLs left — archived but
       cache evicted).

    Returns:
        Path representing the recovered real path.
    """
```

Worked examples:

| project_dir.name | cache hit | JSONL peek | Result |
|---|---|---|---|
| `-home-joe-project-A` | `["/home/joe/project/A"]` | — | `/home/joe/project/A` |
| `-home-cboos--claude` | `["/home/cboos/.claude"]` | — | `/home/cboos/.claude` |
| `-home-joe-x-y` (cache empty) | — | `cwd: "/home/joe/x-y"` | `/home/joe/x-y` |
| `-home-joe-x-y` (cache empty) | — | `cwd: "/home/joe/x/y"` | `/home/joe/x/y` |
| `-home-joe-orphan` (no cache, no JSONLs) | — | — | `/home/joe/orphan` (naïve last-resort) |

Filesystem-existence-testing as a fallback was considered and rejected:
the *target* path may have moved/been deleted since the session was
recorded, and we shouldn't make resolution depend on the local FS state
in a way that produces different output for the same project on
different machines.

### Subtlety 3: Filter-path semantics

When `filter_path` is set:

- **Selection**: skip projects whose resolved real path does not
  satisfy `Path.is_relative_to(filter_path)` (Python 3.9+).
- **Truncation**: the surviving project's destination becomes
  `output_dir / resolved.relative_to(filter_path)`.

Worked examples for `--filter-path /home/joe --output /tmp/obsidian
--expand-paths`:

| Resolved real path | Selected? | Destination |
|---|---|---|
| `/home/joe/project/A` | yes | `/tmp/obsidian/project/A/` |
| `/home/joe/.claude` | yes | `/tmp/obsidian/.claude/` |
| `/home/joe` | yes (matches itself) | `/tmp/obsidian/` (root) |
| `/home/jane/project/B` | no | (skipped) |

### Subtlety 4: Flag interaction matrix

`--filter-path` operates on **whatever path representation we're using**:
expanded real paths when `--expand-paths` is set, the flat encoded
project-dir name otherwise. This keeps the filter consistent with the
"current view" of project paths and avoids the surprise of a filter
silently consulting the cache when the user thought they were just
matching dir-name prefixes.

| --output | --expand-paths | --filter-path | Behaviour |
|---|---|---|---|
| ✗ | ✗ | ✗ | Legacy: write into `~/.claude/projects/<flat>/`. |
| ✓ | ✗ | ✗ | Flat copy under `<output>/<flat>/`. (Closes the implicit gap.) |
| ✓ | ✓ | ✗ | Expanded under `<output>/<real-path>/`. |
| ✓ | ✓ | ✓ | Expanded + filtered: filter against real path, truncate prefix, land under `<output>/<rel-to-prefix>/`. |
| ✓ | ✗ | ✓ | Filter against the flat encoded name (e.g. `--filter-path -home-joe` selects projects starting with `-home-joe-`). No prefix truncation (truncation only meaningful with `--expand-paths`). Result lands under `<output>/<flat>/`. |
| ✗ | (any) | (any) | Warn that the new flags are no-ops; proceed with legacy behaviour. |

### Helper API

```python
def project_dir_to_real_path(
    project_dir: Path,
    cache_manager: Optional[CacheManager] = None,
) -> Path: ...

def project_destination(
    project_dir: Path,
    *,
    output_dir: Optional[Path],
    expand_paths: bool,
    filter_path: Optional[str],
    cache_manager: Optional[CacheManager] = None,
) -> Optional[Path]:
    """Compute the per-project destination directory.

    Returns:
        The destination Path, or None if the project should be skipped
        (filter_path is set and the project doesn't match).
    """
```

Both pure (no I/O beyond reading the cache, which is read-only here);
both trivially testable with mocked CacheManager.

---

## Index page question

The current code writes the index to
`projects_path / get_index_filename(output_format)`. With `--output`,
the natural choice is `output_dir / get_index_filename(output_format)`.

Two open questions:

1. **Should the index even exist in Obsidian-friendly mode?** Obsidian
   discovers files by walking its vault tree. A separate index page is
   redundant in the common Obsidian use case. Recommendation: emit it
   anyway (cheap; users can ignore or `.gitignore`-equivalent it), but
   add a `--no-index` flag as a follow-up if users complain.

2. **Where does the index live when `--filter-path` truncates the
   tree?** The index naturally goes at `output_dir/`, which is *above*
   the truncated tree. Recommendation: keep it at `output_dir/`. The
   alternative — putting it at the deepest common ancestor of the
   filtered projects — would surprise users (the path depends on which
   projects matched, which depends on cache state).

---

## Backwards compatibility

- Default behaviour with no new flags is **byte-identical** to current
  output (verified by snapshot tests after the change).
- Closing the `--output` gap for `--all-projects` is *not* a
  behaviour change because `--output` was previously silently ignored
  in that mode — users who passed it got the legacy path anyway.
  Documenting this in the changelog.
- `convert_project_path_to_claude_dir` (the forward direction) is
  unchanged. The new helper is the inverse and lives alongside it.

---

## Tests

### Unit (`test/test_path_projection.py`)

- `test_project_dir_to_real_path_uses_cache_cwd` — cache populated
  with explicit `cwd`; helper returns it verbatim.
- `test_project_dir_to_real_path_peeks_jsonl_when_no_cache` —
  no cache, but project dir has a JSONL whose first user/assistant
  entry carries `cwd`. Helper peeks, extracts, returns. Sampled
  corpus shapes:
  - `-home-cboos-bin` → JSONL with `cwd: "/home/cboos/bin"` → `/home/cboos/bin`
  - `-home-cboos--claude` → JSONL with `cwd: "/home/cboos/.claude"` → `/home/cboos/.claude`
- `test_project_dir_to_real_path_naive_last_resort` — no cache AND
  no JSONLs left (orphan archived dir); helper returns naïve
  `/`-for-`-` inversion. Documented as best-effort.
- `test_project_dir_to_real_path_disambiguates_via_cache` — two
  flat-encoded names that collide (both `-home-joe-x-y`) but the
  cache stores different `cwd`s; helper returns the right one for
  each.
- `test_project_destination_filter_match_expanded` — `--expand-paths
  --filter-path /home/joe`: filter against real path, destination
  is `output / relpath`.
- `test_project_destination_filter_miss_expanded` — same but real
  path doesn't match prefix; helper returns None.
- `test_project_destination_filter_match_flat` — `--filter-path
  -home-joe` (no expand): filter against flat name, destination is
  `output / <flat>` for matching projects.
- `test_project_destination_no_expand_no_filter` — flat copy under
  `output_dir`.
- `test_project_destination_expand_no_filter` — full real-path
  expansion under `output_dir`.

### Integration (`test/test_obsidian_output.py`)

- Mock `~/.claude/projects/` with two-three project shapes (using the
  existing test_data fixtures pattern; e.g. tmp_path with a couple
  `-home-fixture-*` dirs each with one JSONL).
- Drive the CLI with each flag combination from the matrix above;
  assert the produced directory tree.
- Format coverage: **Markdown only** for the integration test
  matrix. The flag mechanics are format-agnostic (no per-renderer
  logic), so HTML/JSON parity is asserted by inspection of the
  shared converter.py path rather than by re-running the matrix
  per format.

### Snapshot

`test/test_snapshot_html.py` should not need changes — only the
output destination changes, not the rendered content.

---

## Open questions for main — *resolved by user*

1. **JSON output**: format-agnostic (mechanics live in converter.py,
   not the renderers); test only the Markdown path and trust parity
   for HTML/JSON by code inspection.

2. **Filter without expand**: filter against the **unexpanded** flat
   project-dir name (`-home-joe-...`), not the resolved real path.
   No prefix truncation in this mode — truncation only meaningful
   with `--expand-paths`.

3. **No-cache fallback**: peek the first JSONL in the project dir,
   read just enough lines to find one entry with a `cwd` field, return
   it. Cheap, deterministic, no full-parse overhead. Naïve `/`-for-`-`
   inversion stays as the last resort (orphan dirs with no JSONLs).

4. **`--output <file>` vs `<dir>`**: simpler heuristic — if the
   `--output` value ends with a recognised extension suffix
   (`.html` / `.md` / `.markdown` / `.json`), treat as a file;
   otherwise treat as a directory. Both `--expand-paths` and
   `--filter-path` apply only in the directory case.

5. **Python 3.10 baseline**: confirmed; `Path.is_relative_to`
   (3.9+) is safe to use.

6. **Index page location with filter**: confirmed — keep at
   `output_dir/index.{html,md,json}`. Predictable, doesn't depend
   on which projects matched the filter.

---

## Follow-up / Open points

### Cache-freshness checks resolve against `project_path` (source), not the output destination

`cache.is_html_stale(html_path, ...)` and `cache.is_page_stale(...)` both compute their `actual_file` check as `self.project_path / html_path` — the **source** project dir under `~/.claude/projects/`, not the actual output destination (`dest_dir`). With the legacy in-place behaviour the two are identical, so the check works as intended. With `--output` projecting to a different tree, the source path never has a `combined_transcripts.html`, so `is_html_stale` returns "file_missing" / "stale" on every run.

**Practical implication** — both runs of the same source against two different `--output` dirs both produce correct output (the `not output_path.exists()` term in `process_projects_hierarchy`'s `needs_work` and the per-session-file existence checks force regeneration). But every `--output` switch always re-renders, even when the destination is already up-to-date. JSONL parsing is still cache-hit ("X sessions" instead of "X files updated"), only rendering re-runs.

```
Run 1 (--output /tmp/A):  4.4s  (8 projects updated)
Run 2 (--output /tmp/B):  2.3s  (cache-hit on JSONL parse,
                                  but rendering re-ran)
Run 3 (--output /tmp/A):  ~2.3s (same — A's existing files
                                  are not consulted)
```

**Future optimisation** — make the html-cache row's freshness check destination-aware (e.g. record the absolute destination path when writing, compare against it on next run). Bounded value: only matters when users alternate between several `--output` destinations on the same source. Not worth the complexity until someone hits the slowdown in practice.

### Other follow-ups (already noted in the implementation)

- **Archived projects with `--output`** — index links point to projected paths whose files won't exist until the user re-renders. Two plausible mitigations: exclude archived projects from the index in `--output` mode, or always link to the original on-disk location regardless of `--output` / `--expand-paths`. (Surfaced by monk; left for follow-up.)
- **`_peek_jsonl_for_cwd` debug logging** — current shape is silent on tier-2→tier-3 fallthroughs; a `logger.debug(...)` would help when someone is debugging an unexpected naive-tier hit. Zero-noise default kept.

### User-surfaced ergonomics gaps

#### Absolute `--filter-path` without `--expand-paths` silently excludes everything

Symmetric inverse of the footgun monk caught (relative `--filter-path` with `--expand-paths` excludes everything via `Path.relative_to`). Reproduced empirically:

```
$ uv run claude-code-log -o .examples/.../ccl --all-projects \
      --filter-path /home/cboos/Workspace/github/daain \
      --detail low --compact --format md
Processed 665 projects in 1.3s
  Index regenerated
$ ls .examples/.../ccl
index.md   # ← no per-project output
```

The Q2 resolution says: without `--expand-paths`, the filter matches against the encoded flat dir name (`-home-cboos-...`). An absolute path starting with `/` matches no encoded name, so all 665 projects filter out. No error, no warning — only the index lands.

Two fixes to consider (same shape as the existing footgun guards):

- **(A) Reject** at click parse time when `--filter-path.startswith("/")` and `--expand-paths` is unset. Symmetric with monk's relative-filter rejection.
- **(B) Auto-imply `--expand-paths`** when `--filter-path` is absolute. Friendlier; encoded-form filtering is the niche case.

Lean toward (B). Either is straightforward.

#### `--filter-path` should imply `--all-projects`

Filtering only makes sense over a set of projects — without `--all-projects` there's nothing for `--filter-path` to filter. Currently it's warned-about-and-ignored; auto-imply is friendlier.

**Asymmetry note** (worth recording): `--expand-paths` *cannot* safely imply `--all-projects` because the flag has independent meaning in single-session / single-project mode (next item — project one artefact under `<output>/<real-path>/<filename>`). Implying `--all-projects` from `--expand-paths` would silently switch from "expand this one input" to "scan ~/.claude/projects/", which is a much bigger surprise than `--filter-path` could ever be. So the auto-imply is `--filter-path` only; `--expand-paths` keeps the current behaviour matrix.

#### `--expand-paths` for single-session / single-project mode

Today `--expand-paths` is wired only through `process_projects_hierarchy`. Reasonable extension: when a single-session or single-project export is requested with `--output <dir>` and `--expand-paths`, project that one artefact into `<output>/<real-path>/<filename>` using the same path-projection helper. Same convention, same matrix shape — just narrower scope.

#### `--dry-run` mode

Show what would be generated (projected destinations, filter selections) without actually rendering or writing. Useful for sanity-checking a flag combination — especially with the path-projection logic where the destination depends on cache state and JSONL peek results. Pairs naturally with `--filter-path` + `--expand-paths` exploration.

Implementation sketch: a top-level CLI flag that, when set, prints the per-project decision (`source -> dest` or `<source>: filter excluded`) and exits before any file I/O. Cheap to implement on top of `project_destination()` since the helper is already pure.

#### `--combined yes/no/only` (or `both/none/only`) — suppress combined transcripts

For Obsidian usage, having *both* the combined `combined_transcripts.md` and the per-session `session-{id}.md` files is pointless duplication — Obsidian discovers sessions individually via the file tree, and the combined file is just dead weight that confuses graph view. The current default emits both.

Proposed flag: `--combined yes|no|only` (or equivalent `both|none|only`):

| Value | Combined | Per-session | Default for |
|---|---|---|---|
| `yes` / `both` | ✓ | ✓ | Current behaviour (HTML / non-Obsidian flow) |
| `no` / `none` | ✗ | ✓ | **Recommended default for `--expand-paths`** |
| `only` | ✓ | ✗ | When the user explicitly wants the rollup-only view |

When combined is suppressed, the index page must link **directly to each `session-{id}.md`** rather than to `combined_transcripts*.md`. The `html_file` field in `project_summaries` would become a list of session links instead of one combined link.

#### Markdown index: bullet-list directory hierarchy under `--expand-paths`

In Markdown + `--expand-paths` mode, the natural index shape is a nested bullet list mirroring the directory tree:

```markdown
- home/joe
  - project/A
    - [session-aabbccdd](home/joe/project/A/session-aabbccdd.md) — 2026-03-21 *14 messages*
    - [session-eeff0011](home/joe/project/A/session-eeff0011.md) — 2026-03-22 *9 messages*
  - project/B
    - [session-22334455](home/joe/project/B/session-22334455.md) — 2026-03-23 *31 messages*
- home/jane
  - project/C
    - [session-66778899](home/jane/project/C/session-66778899.md) — 2026-03-20 *5 messages*
```

Each directory appears as a parent bullet with its sessions (or sub-dirs) as nested children. Walks the same path-projection tree the file system was projected into, but at the index level. Renders nicely in Obsidian's preview AND in plain Markdown viewers. Especially good when combined with the no-combined-transcripts mode (above), since each leaf bullet then directly points to the session file the user wants to open.

#### **CRITICAL**: Markdown renderer must emit per-message timestamps

This is "absolutely need" tier, not a nice-to-have — it's what enables a cross-session narrative / episodic-memory layer in Obsidian. Without per-message timestamps in the Markdown output, the user can't reconstruct *when* something happened, which kills the whole "transcript as Obsidian note" workflow.

**Current Markdown output (with `--compact`):**

```markdown
## 🤷 User: *Nice! Please commit and reply to bob that…*

Nice! Please commit and reply to bob that you did it.

### 🤖 Assistant: *Done! I've:*

> Done! I've:
>
> 1. **Committed** the WebFetch tool renderer implementation (commit `da363b8`) …
> 2. **Replied** to bob (mail #250) …

> No response requested.
```

**Required:**

```markdown
## 🤷 User: *Nice! Please commit and reply to bob that…*
*2026-03-21 18:40:44*

Nice! Please commit and reply to bob that you did it.

### 🤖 Assistant: *Done! I've:*
*2026-03-21 18:44:22*

> Done! I've:
>
> 1. **Committed** the WebFetch tool renderer implementation (commit `da363b8`) …
> 2. **Replied** to bob (mail #250) …

> No response requested.
```

One italics line per message, immediately after the heading. Format: `*YYYY-MM-DD HH:MM:SS*` (matches the existing HTML timestamp rendering at the message level).

The HTML renderer already emits timestamps; this is purely a Markdown-side omission to fix. Should be a small change in `claude_code_log/markdown/renderer.py` at the per-message header emission point.

Considered out of scope for #151 (the path-projection PR), but should land **before** anyone seriously uses the Obsidian-friendly output for narrative work. Worth its own issue.

---

## Out of scope (mention for completeness)

- Obsidian-specific frontmatter (YAML at top of each `.md` for tags /
  links). Could be a follow-up `--obsidian-frontmatter` flag; not
  part of #151's bullet list.
- Wikilink generation (`[[…]]`) for cross-references between
  sessions. Same — follow-up.
- Symlink-based projection (write once, link from many places). The
  current write-then-copy model is fine for Obsidian; symlinks would
  complicate cache invalidation.
