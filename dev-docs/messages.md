# Message Types in Claude Code Transcripts

This document describes all message types found in Claude Code JSONL transcript files and their corresponding output representations. The goal is to define an **intermediate representation** that captures the logical message structure independent of HTML rendering.

## Overview

Claude Code transcripts contain messages in JSONL format. Each line represents an input message that gets transformed through:

1. **Input Layer** (JSONL): Raw Claude Code transcript data
2. **Intermediate Layer** (TemplateMessage): Format-neutral logical representation
3. **Output Layer** (HTML): Rendered visual output

This document maps input types to their intermediate and output representations.

## Message Categories Summary

| Input Type | `.type` Field | CSS Class | Description |
|------------|---------------|-----------|-------------|
| `user` + text | `user` | `user` | Regular user prompt |
| `user` + text (compacted) | `user` | `user compacted` | Compacted conversation summary |
| `user` + text (isMeta) | `user` | `user slash-command` | Expanded slash command prompt |
| `user` + text (sidechain) | `user` | `user sidechain` | Sub-agent user prompt (skipped) |
| `user` + tool_result | `tool_result` | `tool_result` | Tool execution result |
| `user` + tool_result (error) | `tool_result` | `tool_result error` | Tool execution error |
| `user` + image | `image` | `image` | User-attached image |
| `assistant` + text | `assistant` | `assistant` | Assistant response |
| `assistant` + text (sidechain) | `assistant` | `assistant sidechain` | Sub-agent response |
| `assistant` + thinking | `thinking` | `thinking` | Extended thinking content |
| `assistant` + tool_use | `tool_use` | `tool_use` | Tool invocation |
| `system` (command-name) | `system` | `system` | User-initiated command |
| `system` (command-output) | `system` | `system command-output` | Command output |
| `system` (level=info) | `system` | `system system-info` | Info message |
| `system` (level=warning) | `system` | `system system-warning` | Warning message |
| `system` (level=error) | `system` | `system system-error` | Error message |
| `system` (hook summary) | `system` | `system system-hook` | Hook execution summary |
| `queue-operation` (remove) | `queue-operation` | `queue-operation steering` | User steering (rendered) |
| (internal) | `session-header` | `session-header` | Session header |
| (fallback) | `unknown` | `unknown` | Unknown content type |
| `summary` | — | — | Session summary (metadata only) |
| `queue-operation` (other) | — | — | Queue control (not rendered) |
| `file-history-snapshot` | — | — | File snapshot (not rendered) |

---

## Intermediate Representation: TemplateMessage

The intermediate representation is `TemplateMessage`, a Python class (in `renderer.py`) that captures all fields needed for rendering.

**Important**: Traits like "sidechain", "compacted", "slash-command", "error" are NOT stored as boolean fields. They are encoded in the `css_class` string (e.g., `"user sidechain"`, `"tool_result error"`). This is a current limitation - a truly format-neutral representation would store these as explicit fields.

### Actual Fields (Current Implementation)

```python
class TemplateMessage:
    # Identity
    type: str                  # Base type: "user", "assistant", "tool_use", etc.
    message_id: str            # Unique ID within session (e.g., "msg-0", "tool-1")
    uuid: str                  # Original JSONL uuid
    parent_uuid: Optional[str] # Parent message uuid for hierarchy

    # Content
    content_html: str          # Rendered HTML content

    # Display
    message_title: str         # Display title (e.g., "User", "🔗 Sub-assistant")
    css_class: str             # CSS classes (encodes type + traits like "sidechain")

    # Metadata
    raw_timestamp: str         # ISO 8601 timestamp
    formatted_timestamp: str   # Human-readable timestamp
    session_id: str            # Session UUID

    # Hierarchy
    ancestry: List[str]        # Parent message IDs for fold/unfold
    has_children: bool         # True if has descendant messages
    children: List[TemplateMessage]  # Child messages (tree mode)
    immediate_children_count: int    # Direct children only
    total_descendants_count: int     # All descendants recursively
    immediate_children_by_type: Dict[str, int]  # {"assistant": 2, "tool_use": 3}
    total_descendants_by_type: Dict[str, int]   # All descendants by type

    # Pairing
    is_paired: bool            # True if part of a pair
    pair_role: Optional[str]   # "pair_first", "pair_last", "pair_middle"
    pair_duration: Optional[str]  # Duration for pair_last

    # Tool-specific
    tool_use_id: Optional[str]  # ID linking tool_use to tool_result
    title_hint: Optional[str]   # Additional title info (e.g., file path)

    # Agent-specific
    agent_id: Optional[str]     # Agent ID for sidechain messages

    # Session-specific (for session headers)
    is_session_header: bool     # True for session header messages
    session_summary: Optional[str]  # Summary text for ToC
    session_subtitle: Optional[str] # Working directory info
    token_usage: Optional[str]  # Token usage string

    # Deduplication
    raw_text_content: Optional[str]  # For sidechain/Task result dedup

    # Rendering hints
    has_markdown: bool         # True if content should be rendered as markdown
```

### Traits Encoded in css_class

The `css_class` field encodes the base type plus modifier traits:

| css_class | Base Type | Traits |
|-----------|-----------|--------|
| `"user"` | user | (none) |
| `"user compacted"` | user | compacted conversation |
| `"user slash-command"` | user | isMeta=true |
| `"user sidechain"` | user | isSidechain=true |
| `"assistant sidechain"` | assistant | isSidechain=true |
| `"tool_result error"` | tool_result | is_error=true |
| `"system system-info"` | system | level=info |
| `"system system-warning"` | system | level=warning |

**Note**: Some CSS class combinations (marked in [css-classes.md](css-classes.md)) lack CSS rules and may not render correctly.

---

## JSONL Entry Types (Top Level)

Each line in a `.jsonl` file is a JSON object with a `type` field:

```
Session
├── user                    # User input or tool results
│   ├── text content        # User typed message
│   ├── tool_result         # Result from tool execution
│   └── image               # User attached image
│
├── assistant               # Claude's response
│   ├── text content        # Assistant's text response
│   ├── thinking content    # Extended thinking (when enabled)
│   └── tool_use content    # Tool invocation
│       ├── Read, Edit, Write, Glob, Grep
│       ├── Bash, BashOutput, KillShell
│       ├── Task (spawns sidechain)
│       ├── TodoWrite, AskUserQuestion
│       ├── WebFetch, WebSearch
│       └── ExitPlanMode, etc.
│
├── system                  # System messages (init command, notifications)
│
├── summary                 # Session summary (generated after session ends)
│
├── queue-operation         # Steering messages (interrupt/continue)
│
└── file-history-snapshot   # File state snapshots
```

---

## User Messages

User messages (`type: "user"`) represent human input. They have several variants based on content and flags.

### Regular User Prompt

- **Input**: `user` with text content, `isSidechain: false`, `isMeta: false`
- **Intermediate**: `message_type: "user"`, `css_class: "user"`
- **Files**: [user.json](messages/user/user.json) | [user.jsonl](messages/user/user.jsonl)

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [{ "type": "text", "text": "..." }]
  },
  "isSidechain": false
}
```

### Compacted Conversation

- **Input**: `user` with text containing "(compacted conversation)"
- **Intermediate**: `message_type: "user"`, `is_compacted: true`, `css_class: "user compacted"`
- **Files**: *(No sample in real_projects)*

Rendered with a collapsible summary showing the compacted conversation content.

### Slash Command Expansion

- **Input**: `user` with `isMeta: true`
- **Intermediate**: `message_type: "user"`, `is_meta: true`, `css_class: "user slash-command"`
- **Files**: [user_slash_command.json](messages/user/user_slash_command.json) | [user_slash_command.jsonl](messages/user/user_slash_command.jsonl)

```json
{
  "type": "user",
  "message": {
    "content": "Caveat: The messages below were generated..."
  },
  "isMeta": true
}
```

The `isMeta` field indicates this is an LLM-generated prompt from a slash command.

### Sidechain User (Sub-agent)

- **Input**: `user` with `isSidechain: true`
- **Intermediate**: `message_type: "user"`, `is_sidechain: true`, `css_class: "user sidechain"`
- **Files**: [user_sidechain.json](messages/user/user_sidechain.json) | [user_sidechain.jsonl](messages/user/user_sidechain.jsonl)

**Note**: These are typically **skipped** during rendering because they duplicate the Task tool input prompt.

---

## Tool Results

Tool results are contained within `user` messages as `tool_result` content items.

### Successful Tool Result

- **Input**: `user` with `tool_result` content, `is_error: false`
- **Intermediate**: `message_type: "tool_result"`, `css_class: "tool_result"`
- **Files**: See [messages/tools/](messages/tools/) for tool-specific samples (e.g., `Read-tool_result.json`)

```json
{
  "type": "user",
  "message": {
    "content": [{
      "type": "tool_result",
      "tool_use_id": "toolu_xxx",
      "is_error": false,
      "content": "..."
    }]
  }
}
```

### Error Tool Result

- **Input**: `user` with `tool_result` content, `is_error: true`
- **Intermediate**: `message_type: "tool_result"`, `is_error: true`, `css_class: "tool_result error"`
- **Files**: *(No sample in real_projects)*

---

## Images

- **Input**: `user` with `image` content item
- **Intermediate**: `message_type: "image"`, `css_class: "image"`
- **Files**: [image.json](messages/user/image.json) | [image.jsonl](messages/user/image.jsonl)

```json
{
  "type": "user",
  "message": {
    "content": [{
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "iVBORw0KGgo..."
      }
    }]
  }
}
```

---

## Assistant Messages

Assistant messages (`type: "assistant"`) contain Claude's responses.

### Assistant Text Response

- **Input**: `assistant` with text content, `isSidechain: false`
- **Intermediate**: `message_type: "assistant"`, `css_class: "assistant"`
- **Files**: [assistant.json](messages/assistant/assistant.json) | [assistant.jsonl](messages/assistant/assistant.jsonl)

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "model": "claude-opus-4-1-20250805",
    "content": [{ "type": "text", "text": "..." }]
  }
}
```

### Sidechain Assistant (Sub-agent)

- **Input**: `assistant` with `isSidechain: true`
- **Intermediate**: `message_type: "assistant"`, `is_sidechain: true`, `css_class: "assistant sidechain"`
- **Files**: [assistant_sidechain.json](messages/assistant/assistant_sidechain.json) | [assistant_sidechain.jsonl](messages/assistant/assistant_sidechain.jsonl)

Displayed with title "🔗 Sub-assistant".

### Thinking Content

- **Input**: `assistant` with `thinking` content item
- **Intermediate**: `message_type: "thinking"`, `css_class: "thinking"`
- **Files**: [thinking.json](messages/assistant/thinking.json) | [thinking.jsonl](messages/assistant/thinking.jsonl)

```json
{
  "type": "assistant",
  "message": {
    "content": [{ "type": "thinking", "thinking": "..." }]
  }
}
```

Extended thinking is rendered in a collapsible block.

### Tool Use

- **Input**: `assistant` with `tool_use` content item
- **Intermediate**: `message_type: "tool_use"`, `tool_name: "..."`, `css_class: "tool_use"`
- **Files**: See [messages/tools/](messages/tools/) for tool-specific samples (e.g., `Read-tool_use.json`)

```json
{
  "type": "assistant",
  "message": {
    "content": [{
      "type": "tool_use",
      "id": "toolu_xxx",
      "name": "Read",
      "input": { "file_path": "/path/to/file" }
    }]
  }
}
```

See [messages/tools/](messages/tools/) for samples of each tool type.

---

## System Messages

System messages (`type: "system"`) convey commands and notifications.

### User Command

- **Input**: `system` with `<command-name>` tag in content
- **Intermediate**: `message_type: "system"`, `css_class: "system"`
- **Files**: *(No sample in real_projects)*

Shows the command name (e.g., `/context`, `/init`) in a styled block.

### Command Output

- **Input**: `system` with `<local-command-stdout>` tag in content
- **Intermediate**: `message_type: "system"`, `css_class: "system command-output"`
- **Files**: *(No sample in real_projects)*

Shows the command output with ANSI color support.

### System Info

- **Input**: `system` with `level: "info"` (default)
- **Intermediate**: `message_type: "system"`, `css_class: "system system-info"`
- **Files**: [system_info.json](messages/system/system_info.json) | [system_info.jsonl](messages/system/system_info.jsonl)

```json
{
  "type": "system",
  "content": "Running PostToolUse:MultiEdit...",
  "level": "info"
}
```

### System Warning

- **Input**: `system` with `level: "warning"`
- **Intermediate**: `message_type: "system"`, `css_class: "system system-warning"`
- **Files**: *(No sample in real_projects)*

### System Error

- **Input**: `system` with `level: "error"`
- **Intermediate**: `message_type: "system"`, `css_class: "system system-error"`
- **Files**: *(No sample in real_projects)*

### Hook Summary

- **Input**: `system` with `subtype: "stop_hook_summary"`
- **Intermediate**: `message_type: "system"`, `css_class: "system system-hook"`
- **Files**: *(No sample in real_projects)*

---

## Metadata Messages (Not Rendered)

These message types are not rendered as visual messages but contain important metadata.

### Summary

- **Input**: `type: "summary"`
- **Files**: [summary.json](messages/system/summary.json) | [summary.jsonl](messages/system/summary.jsonl)

```json
{
  "type": "summary",
  "summary": "Claude Code warmup for deep-manifest project",
  "leafUuid": "b83b0f5f-8bfc-4b98-8368-16162a6e9320"
}
```

The `leafUuid` links the summary to the last message of the session for matching.

### Queue Operation

- **Input**: `type: "queue-operation"`
- **Files**: [queue_operation.json](messages/system/queue_operation.json) | [queue_operation.jsonl](messages/system/queue_operation.jsonl)

Used for user interrupts and steering during assistant responses.

### File History Snapshot

- **Input**: `type: "file-history-snapshot"`
- **Files**: [file_history_snapshot.json](messages/system/file_history_snapshot.json) | [file_history_snapshot.jsonl](messages/system/file_history_snapshot.jsonl)

Contains file state snapshots for undo/redo functionality.

---

## Message Hierarchy (Rendering)

When rendering, messages are organized hierarchically:

```
Level 0: Session header
└── Level 1: User message
    ├── Level 2: System message (info/warning)
    └── Level 2: Assistant response
        └── Level 3: Tool use/result (paired)
            └── Level 4: Sidechain assistant (from Task)
                └── Level 5: Sidechain tools
```

The `ancestry` field contains parent message IDs for hierarchy tracking.

## Message Pairing

Related messages are paired together:

- **System command + Command output**: Paired as expandable unit
- **Tool use + Tool result**: Paired by `tool_use_id`
- **User slash command + Expansion**: Paired by `parentUuid`

Pairing metadata:
- `is_paired`: True if part of a pair
- `pair_role`: "pair_first", "pair_last", or "pair_middle"
- `pair_duration`: Elapsed time for the second message

---

## Key Relationships

1. **Parent/Child**: `parentUuid` links messages in conversation order
2. **Tool Pairing**: `tool_use.id` matches `tool_result.tool_use_id`
3. **Sidechain Linking**: `agentId` links sidechain messages to Task results
4. **Summary Linking**: `summary.leafUuid` links to the last message's `uuid`

---

## Tool Types

Tools are invoked via `tool_use` content items in assistant messages, with results appearing as `tool_result` in subsequent user messages.

### File Operations
- **Read**: Read file contents
- **Edit**: Edit file with old_string/new_string replacement
- **Write**: Write entire file
- **MultiEdit**: Multiple edits in one operation
- **Glob**: Find files by pattern
- **Grep**: Search file contents

### Shell Operations
- **Bash**: Execute shell command
- **BashOutput**: Get output from background shell
- **KillShell**: Terminate background shell

### Agent/Task Operations
- **Task**: Spawn sub-agent (creates sidechain)
- **TodoWrite**: Update task list
- **AskUserQuestion**: Prompt user for input
- **ExitPlanMode**: Complete planning phase

### Web Operations
- **WebFetch**: Fetch URL content
- **WebSearch**: Search the web

**See:** [messages/tools/](messages/tools/) for samples of each tool type. Files are organized as:
- `ToolName-tool_use.json` / `.jsonl` - Tool invocation (assistant message)
- `ToolName-tool_result.json` / `.jsonl` - Tool result (user message)

Available tool samples:

| Tool | Use | Result |
|------|-----|--------|
| Bash | [Bash-tool_use](messages/tools/Bash-tool_use.json) | [Bash-tool_result](messages/tools/Bash-tool_result.json) |
| Read | [Read-tool_use](messages/tools/Read-tool_use.json) | [Read-tool_result](messages/tools/Read-tool_result.json) |
| Edit | [Edit-tool_use](messages/tools/Edit-tool_use.json) | [Edit-tool_result](messages/tools/Edit-tool_result.json) |
| Write | [Write-tool_use](messages/tools/Write-tool_use.json) | [Write-tool_result](messages/tools/Write-tool_result.json) |
| Glob | [Glob-tool_use](messages/tools/Glob-tool_use.json) | [Glob-tool_result](messages/tools/Glob-tool_result.json) |
| Grep | [Grep-tool_use](messages/tools/Grep-tool_use.json) | [Grep-tool_result](messages/tools/Grep-tool_result.json) |
| Task | [Task-tool_use](messages/tools/Task-tool_use.json) | [Task-tool_result](messages/tools/Task-tool_result.json) |
| TodoWrite | [TodoWrite-tool_use](messages/tools/TodoWrite-tool_use.json) | [TodoWrite-tool_result](messages/tools/TodoWrite-tool_result.json) |
| MultiEdit | [MultiEdit-tool_use](messages/tools/MultiEdit-tool_use.json) | [MultiEdit-tool_result](messages/tools/MultiEdit-tool_result.json) |
| WebFetch | [WebFetch-tool_use](messages/tools/WebFetch-tool_use.json) | [WebFetch-tool_result](messages/tools/WebFetch-tool_result.json) |
| WebSearch | [WebSearch-tool_use](messages/tools/WebSearch-tool_use.json) | [WebSearch-tool_result](messages/tools/WebSearch-tool_result.json) |

---

## Sidechains (Sub-agents)

When Claude uses the `Task` tool, a sub-agent is spawned. Messages from this sub-agent:
- Have `isSidechain: true`
- Have an `agentId` field linking them to the Task
- Appear in the transcript interleaved with main messages
- Are reordered during rendering to appear after their Task result

---

## Rendering Considerations

- Messages with same `uuid` but different `sessionId` are duplicates (from session resume)
- Multiple assistant messages may share the same `requestId` (streaming responses)
- Tool pairs should be visually grouped and foldable together
- Sidechains should be nested under their Task result
- Extended thinking should be collapsible

---

## Future: Neutral Intermediate Format

The current `TemplateMessage` includes `content_html` which ties it to HTML output. A truly format-neutral intermediate would:

1. Store raw content (text, markdown) without HTML
2. Use typed content blocks instead of HTML strings
3. Support multiple output renderers (HTML, Markdown, JSON, Text)

This aligns with golergka's `content_extractor.py` approach which extracts typed content items (`ExtractedText`, `ExtractedThinking`, etc.) from messages.

---

## References

- [renderer.py](../claude_code_log/renderer.py) - Main rendering module
- [models.py](../claude_code_log/models.py) - Pydantic models for transcript data
- [extract_message_samples.py](../scripts/extract_message_samples.py) - Sample extraction script
- [TEMPLATE_MESSAGE_CHILDREN.md](TEMPLATE_MESSAGE_CHILDREN.md) - Tree architecture exploration
- [MESSAGE_REFACTORING.md](MESSAGE_REFACTORING.md) - Refactoring plan
