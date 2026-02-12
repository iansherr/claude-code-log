# DAG-Based Message Architecture

Replaces timestamp-based ordering with `parentUuid` → `uuid` graph traversal.

Reference: [Messages as Commits: Claude Code's Git-Like DAG of Conversations](https://piebald.ai/blog/messages-as-commits-claude-codes-git-like-dag-of-conversations)

Related issues: #79, #85, #90, #91

---

## Motivation

Currently, messages are sorted by timestamp and then patched with post-hoc
fixups (pair reordering, sidechain reordering by `agentId`). This is fragile:

- **Sync agents**: Works "well enough" because timestamps align with causality
- **Async agents** (#90): Agent runs in background; launch and notification
  are temporally distant; agent transcript interleaves arbitrarily
- **Teammates** (#91): Multiple agents send messages concurrently
- **Resume/fork** (#85): Conversation branches share a prefix; timestamp
  ordering can't express the branching structure

The transcript data already contains the structural information we need:
each message's `parentUuid` points to its predecessor, forming a DAG.

---

## Core Concepts

### The DAG

Every message has a `uuid` and a `parentUuid` (null for first messages).
Together they form a directed acyclic graph. The graph is the authoritative
ordering; timestamps are metadata, not structure.

### Sessions and DAG-lines

A **session** is the set of messages sharing a `sessionId`. Each session
forms a single contiguous chain in the DAG — its **DAG-line**. A session's
DAG-line contains only the messages unique to that session (after
deduplication).

**Assertion**: Within a session, the `parentUuid` chain is linear (no
branching). If data violates this, we log a warning and fall back to
timestamp ordering within that session.

### Junction Points

A **junction point** is a message whose `uuid` is referenced as
`parentUuid` by messages from **different sessions**. This is where
resume/fork happens.

Junction points are **annotations on messages**, not splits of DAG-lines.
A session's DAG-line remains intact; the junction point simply records
"session N forks/continues from here."

### Session Tree

Sessions form a tree:

- **Root sessions**: Their first message has `parentUuid: null` (or points
  to a message not in any loaded session, e.g. after a `/clear`)
- **Child sessions**: Their first unique message's `parentUuid` points into
  a parent session's DAG-line

Children are ordered chronologically (by their first message's timestamp).

Example:

```
Session 1: a → b → c → d → e → f → g
                             ↑           ↑
                             |           |
Session 3: k → l → m        Session 2: h → i → j
(fork from e)                (continues from g)
```

Session tree:
```
- Session 1
  - Session 2 (continues from g)
  - Session 3 (forks from e)
```

Rendered message sequence (depth-first, chronological children):
```
s1, a, b, c, d, e, f, g, s2, h, i, j, s3, k, l, m
```

Where `s1`, `s2`, `s3` are synthesized session header messages.

### Navigation Links

- **Forward links** on junction points: "Session N forks/continues here"
  (shown on message `e` and `g` in the example above)
- **Backlinks** on session headers: "Continues from message X in Session Y"
  (shown on `s2` and `s3`)

### Deduplication

When session 2 resumes session 1, Claude Code may replay prefix messages
(d', e', f', g') into session 2's file. These duplicates share the same
`uuid` but have a different `sessionId`.

Resolution: deduplicate by `uuid`, keeping the instance from the
**earliest session** (by first message timestamp). The "new" messages in
session 2 (those with previously-unseen `uuid`) form its DAG-line.

### Agent Transcripts

Agent transcripts also form DAG-lines. They come in two flavors:

1. **Continuing agents**: Their `parentUuid` chains into a previous agent's
   DAG-line (same session, different `agentId`). These naturally fit the
   DAG.

2. **Top-level agents**: `parentUuid` is null. These need explicit
   **parenting** — splicing them into the main session's DAG-line at the
   appropriate point.

   For `x → y → z` where `y` is a Task, and agent transcript `u → v` needs
   to be rooted at `y`, the result is: `x → y → u → v → z`.

**Parenting strategies** (by agent type):

| Agent type | Link mechanism | Parent at |
|------------|---------------|-----------|
| Sync Task | `agentId` on tool_result | Task tool_result message |
| Async Task (#90) | `agentId` on launch tool_result, `task-id` in `<task-notification>` | Launch tool_result |
| Teammate (#91) | `team_name` + agent name | TBD — likely TeamCreate or Task-with-team |

---

## Algorithm

### Phase 1: Load All Sessions

Load **all** `.jsonl` files for a project directory. Build a unified message
index:

```python
messages_by_uuid: dict[str, TranscriptEntry]   # uuid → entry (oldest wins)
children_by_uuid: dict[str, list[str]]          # parentUuid → [child uuids]
sessions: dict[str, list[str]]                  # sessionId → [uuids in chain order]
```

When targeting a single session, still load all files but only render
that session's subtree. Optionally warn that context from other sessions
is available.

### Phase 2: Build DAG and Deduplicate

1. Parse all entries, index by `uuid`
2. For duplicate `uuid`s, keep the one from the earliest `sessionId`
3. Build `children_by_uuid` from `parentUuid` links
4. Group messages by `sessionId`

### Phase 3: Extract Session DAG-lines

For each session:
1. Identify the session's unique messages (those whose authoritative
   `sessionId` matches)
2. Order them by following `parentUuid` chains (not timestamps)
3. Verify linearity (no branching within a session)

### Phase 4: Build Session Tree

1. For each session, find where its DAG-line attaches to the DAG:
   - Walk back from the session's first unique message via `parentUuid`
   - The first message belonging to a **different** session is the
     attachment point
2. The session whose message is the attachment point is the parent session
3. Root sessions have no attachment point (first message is `parentUuid: null`
   or points outside loaded data)
4. Order children chronologically

### Phase 5: Identify Junction Points

A message is a junction point if `children_by_uuid[msg.uuid]` contains
messages from multiple sessions, or from a session different than the
message's own.

Annotate junction points with their target sessions for forward-link
rendering.

### Phase 6: Splice Agent Transcripts

For each agent transcript (identified by `agentId`):
1. Determine parenting strategy (see table above)
2. Find the anchor message in the main session's DAG-line
3. Splice the agent's DAG-line after the anchor

This replaces the current `_reorder_sidechain_template_messages` approach
with a principled graph operation.

### Phase 7: Process and Render

Within each DAG-line, apply existing processing:
- Pairing (tool_use+tool_result, thinking+assistant, etc.)
- Hierarchy building
- Tree construction

Pairing should be **scoped to DAG-lines** — no pairing across session
boundaries. This is both correct and faster.

---

## Assertions / Invariants

These should be checked at runtime (log warnings, don't crash):

1. **Session linearity**: Each session's messages form a single chain
   (no branching within a `sessionId`)
2. **DAG acyclicity**: No cycles in `parentUuid` chains
3. **Unique ownership**: After deduplication, each `uuid` belongs to
   exactly one session
4. **Agent parenting**: Every top-level agent transcript has an identifiable
   anchor in the main session

---

## Impact on Existing Code

### What changes

| Component | Current | After |
|-----------|---------|-------|
| `converter.py` | Load single file + agent files; timestamp sort | Load all project files; build DAG |
| `renderer.py` message ordering | Timestamp sort + pair reorder + sidechain reorder | DAG-line traversal; pairing within DAG-lines |
| Session index | Flat list sorted by timestamp | Session tree with parent/child relationships |
| Agent handling | `agentId`-based insertion after timestamp sort | Agent DAG-line splicing at anchor points |

### What stays

- Factory layer (transcript entry → MessageContent)
- TemplateMessage wrapper and RenderingContext
- Hierarchy building within sessions (user → assistant → tools)
- Renderer dispatch and format_* methods
- HTML templates and JavaScript (fold, timeline, filters)
- Deduplication heuristics (sidechain cleanup, etc.) — may simplify over time

---

## Implementation Plan

### Phase A: DAG Infrastructure (new module: `dag.py`)

1. **Message indexing**: Load all session files, build `uuid` index,
   deduplicate
2. **DAG construction**: Build parent→children graph
3. **Session extraction**: Group by `sessionId`, extract DAG-lines,
   verify linearity
4. **Session tree**: Build parent/child session relationships, identify
   junction points

This phase is purely additive — new code alongside existing. Tests can
validate DAG construction against known transcripts.

### Phase B: Integration with Rendering Pipeline

1. Replace `load_transcript` / `load_directory_transcripts` with
   DAG-based loading in `converter.py`
2. Pass DAG-lines (per session) into `generate_template_messages`
3. Scope pairing to DAG-lines
4. Generate session headers with navigation links (forward/back)
5. Update session index from flat to hierarchical

### Phase C: Agent Transcript Rework

1. Implement parenting strategies for each agent type
2. Replace `_reorder_sidechain_template_messages` with DAG-line splicing
3. Simplify or remove `_cleanup_sidechain_duplicates` (dedup now
   happens at DAG level)

### Phase D: Async Agent and Teammate Support

1. Parse `<task-notification>` to extract `task-id` for async agent linking
2. Implement teammate parenting strategy (#91)
3. This is where #90 and #91 get properly resolved

---

## Related Documentation

- [rendering-architecture.md](rendering-architecture.md) — Current pipeline
- [messages.md](messages.md) — Message type reference
- [rendering-next.md](rendering-next.md) — Future rendering improvements
