# Refactoring Plan: Content → Message Naming

## Goal

Clarify the naming by using consistent suffixes:
- `*Content` = ContentItem members (JSONL parsing layer)
- `*Input` / `*Output` = Tool-specific parsing
- `*Message` = MessageContent subclasses (rendering layer)
- `*Model` = Pydantic JSONL transcript models

## Phase 1: Free up "Message" names

Rename Pydantic transcript message models to add `Model` suffix:

| Current | New |
|---------|-----|
| `UserMessage` | `UserMessageModel` |
| `AssistantMessage` | `AssistantMessageModel` |

These are only used in `UserTranscriptEntry.message` and `AssistantTranscriptEntry.message` for Pydantic deserialization.

## Phase 2: Rename MessageContent subclasses to ...Message

| Current | New |
|---------|-----|
| `UserTextContent` | `UserTextMessage` |
| `UserSteeringContent` | `UserSteeringMessage` |
| `UserSlashCommandContent` | `UserSlashCommandMessage` |
| `UserMemoryContent` | `UserMemoryMessage` |
| `AssistantTextContent` | `AssistantTextMessage` |
| `SlashCommandContent` | `SlashCommandMessage` |
| `CommandOutputContent` | `CommandOutputMessage` |
| `CompactedSummaryContent` | `CompactedSummaryMessage` |
| `BashInputContent` | `BashInputMessage` |
| `BashOutputContent` | `BashOutputMessage` |
| `SystemContent` | `SystemMessage` |
| `HookSummaryContent` | `HookSummaryMessage` |
| `SessionHeaderContent` | `SessionHeaderMessage` |
| `DedupNoticeContent` | `DedupNoticeMessage` |
| `UnknownContent` | `UnknownMessage` |
| `ThinkingContentModel` | `ThinkingMessage` |
| `ToolResultContentModel` | `ToolResultMessage` |

Also update:
- `CSS_CLASS_REGISTRY` in `html/utils.py`
- All formatters in `html/*_formatters.py`
- All usages in `renderer.py`, `parser.py`, etc.

## Phase 3: Tool message wrapper pattern with typed inputs/outputs

### New type aliases

```python
# Union of all specialized input types + ToolUseContent as generic fallback
ToolInput = Union[
    BashInput, ReadInput, WriteInput, EditInput, MultiEditInput,
    GlobInput, GrepInput, TaskInput, TodoWriteInput, AskUserQuestionInput,
    ExitPlanModeInput, NotebookEditInput, WebFetchInput, WebSearchInput,
    KillShellInput,
    ToolUseContent,  # Generic fallback when no specialized parser
]

# Renamed from ToolUseResult for symmetry
# Union of all specialized output types + ToolResultContent as generic fallback
ToolOutput = Union[
    ReadOutput, EditOutput,  # ... more as they're implemented
    ToolResultContent,  # Generic fallback for unparsed results
]
```

### New ToolUseMessage

```python
@dataclass
class ToolUseMessage(MessageContent):
    """Message for tool invocations."""
    input: ToolInput  # Specialized (BashInput, etc.) or generic (ToolUseContent)
    tool_use_id: str  # From ToolUseContent.id
    tool_name: str    # From ToolUseContent.name
```

### New ToolResultMessage

```python
@dataclass
class ToolResultMessage(MessageContent):
    """Message for tool results."""
    output: ToolOutput  # Specialized (ReadOutput, etc.) or generic (ToolResultContent)
    tool_use_id: str
    tool_name: Optional[str] = None
    file_path: Optional[str] = None

    @property
    def is_error(self) -> bool:
        if isinstance(self.output, ToolResultContent):
            return self.output.is_error or False
        return False
```

### Simple ThinkingMessage (no wrapper)

```python
@dataclass
class ThinkingMessage(MessageContent):
    thinking_text: str  # The thinking content
    signature: Optional[str] = None
```

## Phase 4: Update CSS_CLASS_REGISTRY

Update to use new names:

```python
CSS_CLASS_REGISTRY: dict[type[MessageContent], list[str]] = {
    # System messages
    SystemMessage: ["system"],
    HookSummaryMessage: ["system", "system-hook"],
    # User messages
    UserTextMessage: ["user"],
    UserSteeringMessage: ["user", "steering"],
    SlashCommandMessage: ["user", "slash-command"],
    UserSlashCommandMessage: ["user", "slash-command"],
    UserMemoryMessage: ["user"],
    CompactedSummaryMessage: ["user", "compacted"],
    CommandOutputMessage: ["user", "command-output"],
    # Assistant messages
    AssistantTextMessage: ["assistant"],
    # Tool messages
    ToolUseMessage: ["tool_use"],
    ToolResultMessage: ["tool_result"],
    # Other messages
    ThinkingMessage: ["thinking"],
    SessionHeaderMessage: ["session_header"],
    BashInputMessage: ["bash-input"],
    BashOutputMessage: ["bash-output"],
    UnknownMessage: ["unknown"],
}
```

## Naming Pattern Summary

| Suffix | Layer | Examples |
|--------|-------|----------|
| `*Content` | ContentItem (JSONL parsing) | `TextContent`, `ToolUseContent`, `ToolResultContent`, `ThinkingContent`, `ImageContent` |
| `*Input` | Tool input parsing | `BashInput`, `ReadInput`, `TaskInput`, ... |
| `*Output` | Tool output parsing | `ReadOutput`, `EditOutput`, ... |
| `*Message` | MessageContent (rendering) | `UserTextMessage`, `ToolUseMessage`, `ThinkingMessage` |
| `*Model` | Pydantic JSONL models | `UserMessageModel`, `AssistantMessageModel` |

## Files to Update

| File | Changes |
|------|---------|
| `models.py` | All renames, new ToolInput/ToolOutput unions |
| `parser.py` | Update imports and usages |
| `renderer.py` | Update imports and usages |
| `html/utils.py` | Update CSS_CLASS_REGISTRY |
| `html/renderer.py` | Update dispatcher and imports |
| `html/user_formatters.py` | Update function signatures and imports |
| `html/assistant_formatters.py` | Update function signatures and imports |
| `html/tool_formatters.py` | Update to use ToolUseMessage/ToolResultMessage |
| `html/system_formatters.py` | Update function signatures and imports |
| `converter.py` | Update imports |
| `dev-docs/messages.md` | Update documentation |

## Execution Order

1. Phase 1: Rename `UserMessage` → `UserMessageModel`, `AssistantMessage` → `AssistantMessageModel`
2. Phase 2: Rename all MessageContent subclasses to `*Message`
3. Phase 3: Create `ToolInput`, `ToolOutput` unions; update `ToolUseMessage`, `ToolResultMessage`
4. Phase 4: Update CSS_CLASS_REGISTRY
5. Run tests, fix any remaining issues
6. Update documentation
