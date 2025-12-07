# Message Rendering Refactoring Plan

This document tracks the ongoing refactoring effort to improve the message rendering code in `renderer.py`.

## Current State (dev/message-tree-refactoring)

As of December 2024, `renderer.py` has grown to **4246 lines** with several new subsystems:

| Function/System | Lines | Notes |
|-----------------|-------|-------|
| `_process_messages_loop()` | ~687 | Main message processing - needs decomposition |
| `_convert_ansi_to_html()` | ~252 | Self-contained, could be extracted |
| `_identify_message_pairs()` | ~227 | Complex pairing logic |
| `_reorder_paired_messages()` | ~104 | Pair reordering |
| Hierarchy system | ~150 | `_build_message_hierarchy`, `_mark_messages_with_children` |
| Tree building | ~60 | `_build_message_tree()` - NEW: builds children hierarchy |
| Tool formatters | ~600 | Various `format_*_tool_content` functions |

**New systems added since initial plan:**
- **Message pairing** - System command + slash-command, tool use + result
- **Hierarchy/fold system** - Level-based ancestry for fold/unfold UI
- **Message processors** - `_process_command_message`, `_process_bash_input`, etc.
- **ANSI color conversion** - Full terminal color to HTML support
- **Message tree** - `_build_message_tree()` and `TemplateMessage.flatten()` methods (Phase 1-2 of TEMPLATE_MESSAGE_CHILDREN.md)

## Motivation

The refactoring aims to:

1. **Improve maintainability** - Functions are too large (some 600+ lines)
2. **Better separation of concerns** - Move specialized utilities to dedicated modules
3. **Improve type safety** - Use typed objects instead of generic dictionaries
4. **Enable testing** - Large functions are difficult to unit test
5. **Performance profiling** - Timing instrumentation to identify bottlenecks

## Related Refactoring Branches

### dev/message-tree-refactoring (Current Branch)

This branch builds the foundation for tree-based message rendering. See [TEMPLATE_MESSAGE_CHILDREN.md](TEMPLATE_MESSAGE_CHILDREN.md) for details.

**Completed Work:**
- ✅ Phase 1: Added `children: List[TemplateMessage]` field to TemplateMessage
- ✅ Phase 1: Added `flatten()` and `flatten_all()` methods for backward compatibility
- ✅ Phase 2: Implemented `_build_message_tree()` function
- ✅ Phase 2: Tree is built after hierarchy processing but flat list still used for templates

**Integration with MESSAGE_REFACTORING.md:**
- The tree structure enables future **recursive template rendering** (Phase 3 in TEMPLATE_MESSAGE_CHILDREN.md)
- Provides foundation for **Visitor pattern** output formats (HTML, Markdown, JSON)
- `flatten_all()` ensures backward compatibility during migration

### golergka's text-output-format Branch (ada7ef5)

Adds text/markdown/chat output formats via new `content_extractor.py` module.

**Key Changes:**
- Created `content_extractor.py` with dataclasses: `ExtractedText`, `ExtractedThinking`, `ExtractedToolUse`, `ExtractedToolResult`, `ExtractedImage`
- Refactored `render_message_content()` to use extraction layer (~70 lines changed)
- Added `text_renderer.py` for text-based output (426 lines)
- CLI `--format` option: html, text, markdown, chat

**Relationship to This Refactoring:**

| Aspect | golergka's Approach | This Refactoring |
|--------|---------------------|------------------|
| Focus | Multi-format output | Code organization |
| Data layer | ContentItem → ExtractedContent | TemplateMessage tree |
| Presentation | Separate renderers per format | Modular HTML renderer |
| Compatibility | Parallel to HTML | Refactor existing HTML |

**Integration Assessment:**
- **Complementary**: golergka's extraction layer operates at ContentItem level, this refactoring at TemplateMessage level
- **Low conflict**: `content_extractor.py` is a new module, doesn't touch hierarchy/pairing code
- **Synergy opportunity**: Text renderer could benefit from tree structure for nested output
- **Risk**: `render_message_content()` changes in golergka's PR conflict with local changes

**Recommendation:** Consider integrating golergka's work **after** completing Phase 3 (ANSI extraction) and Phase 4 (Tool formatters extraction). The content extraction layer is useful for multi-format support, but is tangential to the core refactoring goals of reducing renderer.py complexity.

## Completed Phases

### Phase 1: Timing Infrastructure (Commits: 56b2807, 8426f39)

**Goal**: Centralize timing utilities and standardize timing instrumentation patterns

**Changes**:
- ✅ Extracted timing utilities to `renderer_timings.py` module
- ✅ Moved `DEBUG_TIMING` environment variable handling to timing module
- ✅ Standardized `log_timing` context manager pattern - work goes INSIDE the `with` block
- ✅ Added support for dynamic phase names using lambda expressions
- ✅ Removed top-level `os` import from renderer.py (no longer needed)

**Benefits**:
- All timing-related code centralized in one module
- Consistent timing instrumentation throughout renderer
- Easy to enable/disable timing with `CLAUDE_CODE_LOG_DEBUG_TIMING` environment variable
- Better insight into rendering performance

### Phase 2: Tool Use Context Optimization (Commit: 56b2807)

**Goal**: Simplify tool use context management and eliminate unnecessary pre-processing

**Analysis**:
- `tool_use_context` was only used when processing tool results
- The "prompt" member stored for Task tools wasn't actually used in lookups
- Tool uses always appear before tool results chronologically
- No need for separate pre-processing pass

**Changes**:
- ✅ Removed `_define_tool_use_context()` function (68 lines eliminated)
- ✅ Changed `tool_use_context` from `Dict[str, Dict[str, Any]]` to `Dict[str, ToolUseContent]`
- ✅ Build index inline when creating ToolUseContent objects during message processing
- ✅ Use attribute access instead of dictionary access for better type safety
- ✅ Replaced dead code in `render_message_content` with warnings

**Benefits**:
- Eliminated entire pre-processing pass through messages
- Better type safety with ToolUseContent objects
- Cleaner code with inline index building
- ~70 lines of code removed

### Phase 3: ANSI Color Module Extraction ✅ COMPLETE

**Goal**: Extract ANSI color conversion to dedicated module

**Changes**:
- ✅ Created `claude_code_log/ansi_colors.py` (261 lines)
- ✅ Moved `_convert_ansi_to_html()` → `convert_ansi_to_html()`
- ✅ Updated imports in `renderer.py`
- ✅ Updated test imports in `test_ansi_colors.py`

**Result**: 242 lines removed from renderer.py (4246 → 4004)

### Phase 4: Code Rendering Module Extraction ✅ COMPLETE

**Goal**: Extract code-related rendering (Pygments highlighting, diff rendering) to dedicated module

**Changes**:
- ✅ Created `claude_code_log/renderer_code.py` (330 lines)
- ✅ Moved `_highlight_code_with_pygments()` → `highlight_code_with_pygments()`
- ✅ Moved `_truncate_highlighted_preview()` → `truncate_highlighted_preview()`
- ✅ Moved `_render_single_diff()` → `render_single_diff()`
- ✅ Moved `_render_line_diff()` → `render_line_diff()`
- ✅ Updated imports in `renderer.py`
- ✅ Updated test imports in `test_preview_truncation.py`
- ✅ Removed unused Pygments imports from renderer.py

**Result**: 274 lines removed from renderer.py (4004 → 3730)

**Note**: The original Phase 4 plan targeted tool formatters (~600 lines), but due to tight coupling with `escape_html`, `render_markdown`, and other utilities, we extracted a cleaner subset: code highlighting and diff rendering. The remaining tool formatters could be extracted in a future phase once the shared utilities are better factored.

### Phase 5: Message Processing Decomposition ✅ PARTIAL

**Goal**: Break down the 687-line `_process_messages_loop()` into smaller functions

**Changes**:
- ✅ Created `_process_system_message()` function (~88 lines) - handles hook summaries, commands, system messages
- ✅ Created `ToolItemResult` dataclass for structured tool processing results
- ✅ Created `_process_tool_use_item()` function (~84 lines) - handles tool_use content items
- ✅ Created `_process_tool_result_item()` function (~71 lines) - handles tool_result content items
- ✅ Created `_process_thinking_item()` function (~21 lines) - handles thinking content
- ✅ Created `_process_image_item()` function (~17 lines) - handles image content
- ✅ Replaced ~220 lines of nested conditionals with clean dispatcher pattern

**Result**: `_process_messages_loop()` reduced from ~687 to ~460 lines (33% smaller)

**Note**: File size increased slightly (3730 → 3814 lines) due to new helper functions, but the main loop is now much more maintainable with focused, testable helper functions. Further decomposition (session tracking, token usage extraction) could reduce it to ~200 lines but would require more complex parameter passing.

### Phase 6: Message Pairing Simplification ✅ COMPLETE

**Goal**: Simplify the complex pairing logic in `_identify_message_pairs()`

**Changes**:
- ✅ Created `PairingIndices` dataclass to hold all lookup indices in one place
- ✅ Extracted `_build_pairing_indices()` function (~35 lines) - builds all indices in single pass
- ✅ Extracted `_mark_pair()` utility (~8 lines) - marks first/last message pairing
- ✅ Extracted `_try_pair_adjacent()` function (~25 lines) - handles adjacent message pairs
- ✅ Extracted `_try_pair_by_index()` function (~30 lines) - handles index-based pairing
- ✅ Simplified `_identify_message_pairs()` from ~120 lines to ~37 lines (69% smaller)

**Result**: Pairing logic decomposed into focused helpers with clear responsibilities:
- `_build_pairing_indices()`: O(n) index building for tool_use, tool_result, uuid, slash_command lookups
- `_try_pair_adjacent()`: Handles system+slash, command+output, tool_use+result adjacent pairs
- `_try_pair_by_index()`: Handles index-based pairing for non-adjacent messages

**Note**: File size increased slightly (3814 → 3853 lines) due to new helper functions, but the main pairing function is now much cleaner and each helper is independently testable.

## Planned Future Phases

### Phase 7: Message Type Documentation ✅ COMPLETE

**Goal**: Document message types and CSS classes comprehensively

**Completed Work**:
- ✅ Created comprehensive [css-classes.md](css-classes.md) with:
  - Complete CSS class combinations (19 semantic patterns)
  - CSS rule support status (24 full, 7 partial, 1 none)
  - Pairing behavior documentation (pair_first/pair_last rules)
  - Fold-bar support analysis
- ✅ Updated [messages.md](messages.md) with:
  - Complete css_class trait mapping table
  - Pairing patterns and rules by type
  - Full tool table (16 tools with model info)
  - Cross-references to css-classes.md

### Phase 8: Testing Infrastructure ✅ COMPLETE

**Goal**: Improve test coverage for refactored modules

**Completed Work**:
- ✅ Created `test/test_phase8_message_variants.py` with tests for:
  - Slash command rendering (`isMeta=True` flag)
  - Queue operations skip behavior (enqueue/dequeue not rendered)
  - CSS class modifiers composition (`error`, `sidechain`, combinations)
  - Deduplication with modifiers
- ✅ Created `test/test_renderer.py` with edge case tests for:
  - System message handling
  - Write and Edit tool rendering
- ✅ Created `test/test_renderer_code.py` with tests for:
  - Pygments highlighting (pattern matching, unknown extensions, ClassNotFound)
  - Truncated highlighted preview
  - Diff rendering edge cases (consecutive removals, hint line skipping)
- ✅ Simplified CSS by removing redundant `paired-message` class
- ✅ Updated snapshot tests and documentation

**Test Files Added**:
- [test/test_phase8_message_variants.py](../test/test_phase8_message_variants.py) - Message type variants
- [test/test_renderer.py](../test/test_renderer.py) - Renderer edge cases
- [test/test_renderer_code.py](../test/test_renderer_code.py) - Code highlighting/diff tests

**Coverage Notes**:
- Some lines in `renderer_code.py` (116-118, 319) are unreachable due to algorithm behavior
- Pygments `ClassNotFound` exception path covered via mock testing

### Phase 9: Type Safety Improvements ✅ COMPLETE

**Goal**: Replace string-based type checking with enums and typed structures

**Completed Work**:
- ✅ Added `MessageType(str, Enum)` in `models.py` with all message types
- ✅ Added type guards for TranscriptEntry union narrowing (available for future use)
- ✅ Updated `renderer.py` to use `MessageType` enum for key comparisons
- ✅ Maintained backward compatibility via `str` base class

**MessageType Enum Values**:
- JSONL entry types: `USER`, `ASSISTANT`, `SYSTEM`, `SUMMARY`, `QUEUE_OPERATION`
- Rendering types: `TOOL_USE`, `TOOL_RESULT`, `THINKING`, `IMAGE`, `BASH_INPUT`, `BASH_OUTPUT`, `SESSION_HEADER`, `UNKNOWN`
- System subtypes: `SYSTEM_INFO`, `SYSTEM_WARNING`, `SYSTEM_ERROR`

**Type Guards Added**:
- `is_user_entry()`, `is_assistant_entry()`, `is_system_entry()`, `is_summary_entry()`, `is_queue_operation_entry()`
- `is_tool_use_content()`, `is_tool_result_content()`, `is_thinking_content()`, `is_image_content()`, `is_text_content()`

**Note**: MessageModifiers dataclass deferred - existing boolean flags work well for now

### Phase 10: Parser Simplification ✅ COMPLETE

**Goal**: Simplify `extract_text_content()` using isinstance checks

**Completed Work**:
- ✅ Added imports for Anthropic SDK types: `TextBlock`, `ThinkingBlock`
- ✅ Simplified `extract_text_content()` with clean isinstance checks
- ✅ Removed defensive `hasattr`/`getattr` patterns
- ✅ 23% code reduction (17 lines → 13 lines)

**Before** (defensive pattern):
```python
if hasattr(item, "type") and getattr(item, "type") == "text":
    text = getattr(item, "text", "")
    if text:
        text_parts.append(text)
```

**After** (clean isinstance):
```python
if isinstance(item, (TextContent, TextBlock)):
    text_parts.append(item.text)
elif isinstance(item, (ThinkingContent, ThinkingBlock)):
    continue
```

**Testing Evidence**: All 431 tests pass with simplified version
**Risk**: Low - maintains same behavior, fully tested

### Phase 11: Tool Model Enhancement ✅ COMPLETE

**Goal**: Add typed models for tool inputs (currently all generic `Dict[str, Any]`)

**Completed Work**:
- ✅ Added 9 typed input models to `models.py`:
  - `BashInput`, `ReadInput`, `WriteInput`, `EditInput`, `MultiEditInput`
  - `GlobInput`, `GrepInput`, `TaskInput`, `TodoWriteInput`
- ✅ Created `ToolInput` union type for type-safe tool input handling
- ✅ Added `TOOL_INPUT_MODELS` mapping for tool name → model class lookup
- ✅ Added `parse_tool_input()` helper function with fallback to raw dict

**Typed Input Models Added**:
```python
class BashInput(BaseModel):
    command: str
    description: Optional[str] = None
    timeout: Optional[int] = None
    run_in_background: Optional[bool] = None
    dangerouslyDisableSandbox: Optional[bool] = None

class ReadInput(BaseModel):
    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None

class EditInput(BaseModel):
    file_path: str
    old_string: str
    new_string: str
    replace_all: Optional[bool] = None
```

**Note**: The `ToolUseContent.input` field remains `Dict[str, Any]` for backward compatibility.
The new typed models are available for optional use via `parse_tool_input()`. Existing
code continues to work unchanged with dictionary access.

### Phase 12: Renderer Decomposition - Format Neutral

**Goal**: Separate format-neutral logic from HTML-specific generation

**Current Architecture**:
```
renderer.py (3853 lines)
├── Message processing (format-neutral)
│   ├── _process_messages_loop() - 460 lines
│   ├── Pairing logic
│   ├── Hierarchy building
│   └── Token usage extraction
├── HTML generation (format-specific)
│   ├── Template rendering
│   ├── CSS class computation
│   └── Content HTML formatting
└── Tool formatters (mixed)
```

**Target Architecture**:
```
message_processor.py (format-neutral)
├── MessageProcessor class
├── Pairing logic
├── Hierarchy building
└── Token aggregation

html_renderer.py (HTML-specific)
├── CSS class computation
├── Template rendering
└── Tool HTML formatters

text_renderer.py (future - golergka's work)
├── Text/markdown output
└── Chat format
```

**Dependencies**:
- Requires Phase 9 (type safety) for clean interfaces
- Benefits from Phase 10 (parser simplification)
- Enables golergka's multi-format integration

**Risk**: High - requires careful refactoring
**Priority**: Medium-term goal

## Recommended Execution Order

For maximum impact with minimum risk:

### Completed
1. ✅ **Phase 3 (ANSI)** - Low risk, self-contained, immediate ~250 line reduction
2. ✅ **Phase 4 (Code rendering)** - Medium risk, ~274 line reduction, clear boundaries
3. ✅ **Phase 5 (Processing)** - High impact, main loop 33% smaller
4. ✅ **Phase 6 (Pairing)** - Pairing function 69% smaller, clear helpers
5. ✅ **Phase 7 (Documentation)** - Complete CSS/message docs
6. ✅ **Phase 8 (Testing)** - Coverage gap tests, message variant tests, CSS simplification
7. ✅ **Phase 9 (Type Safety)** - MessageType enum and type guards added

### Next Steps
8. ✅ **Phase 10 (Parser)** - Simplified extract_text_content() with isinstance checks
9. ✅ **Phase 11 (Tool Models)** - Added typed input models for 9 common tools
10. **Phase 12 (Format Neutral)** - Long-term goal, enables multi-format output

**Tree Refactoring Integration:**
- Tree building (TEMPLATE_MESSAGE_CHILDREN.md Phase 1-2) is complete and non-blocking
- Template migration (Phase 3) can now leverage MessageType enum
- golergka's text formats can be integrated using type guards

**golergka Integration Timing:**
- Phase 9 type guards available for interface clarity
- When integrating, resolve `render_message_content()` conflicts carefully
- Tree structure and MessageType enum benefit text renderer

## Metrics to Track

| Metric | Baseline (v0.9) | Current (Phase 11 done) | Target |
|--------|-----------------|-------------------------|--------|
| renderer.py lines | 4246 | 3853 | <3000 |
| Largest function | ~687 lines | ~460 lines | <100 lines |
| `_identify_message_pairs()` | ~120 lines | ~37 lines | - |
| `extract_text_content()` | ~17 lines | ~13 lines | - |
| Typed tool input models | 0 | 9 | - |
| Module count | 3 (renderer, timings, models) | 5 (+ansi_colors, +renderer_code) | 6-7 |
| Test coverage | ~78% | ~78% | >85% |

**Progress**: Main loop reduced by 33% (687 → 460 lines). Pairing function reduced by 69% (120 → 37 lines). MessageType enum and type guards added. Parser simplified with isinstance checks (Phase 10). 9 typed tool input models added (Phase 11).

## Quality Gates

Before merging any phase:

- [ ] `just test-all` passes
- [ ] `uv run pyright` passes with 0 errors
- [ ] `ruff check` passes
- [ ] Snapshot tests unchanged (or intentionally updated)
- [ ] No performance regression (check with `CLAUDE_CODE_LOG_DEBUG_TIMING=1`)

## Notes

- All changes should maintain backward compatibility
- Each phase should be committed separately for easy review
- Consider feature flags for large changes during development
- Run against real Claude projects to verify visual correctness

## References

### Code Modules
- [renderer.py](../claude_code_log/renderer.py) - Main rendering module (3853 lines)
- [ansi_colors.py](../claude_code_log/ansi_colors.py) - ANSI color conversion (261 lines) - Phase 3
- [renderer_code.py](../claude_code_log/renderer_code.py) - Code highlighting & diffs (330 lines) - Phase 4
- [renderer_timings.py](../claude_code_log/renderer_timings.py) - Timing utilities
- [models.py](../claude_code_log/models.py) - Pydantic models, MessageType enum, type guards - Phase 9
- [parser.py](../claude_code_log/parser.py) - JSONL parsing

### Documentation
- [css-classes.md](css-classes.md) - Complete CSS class reference with support status - Phase 7
- [messages.md](messages.md) - Message types and tool documentation - Phase 7
- [FOLD_STATE_DIAGRAM.md](FOLD_STATE_DIAGRAM.md) - Fold system documentation
- [TEMPLATE_MESSAGE_CHILDREN.md](TEMPLATE_MESSAGE_CHILDREN.md) - Tree architecture exploration

### Tests
- [test/test_ansi_colors.py](../test/test_ansi_colors.py) - ANSI tests
- [test/test_preview_truncation.py](../test/test_preview_truncation.py) - Code preview tests
- [test/test_sidechain_agents.py](../test/test_sidechain_agents.py) - Integration tests
- [test/test_template_data.py](../test/test_template_data.py) - Tree building tests (TestTemplateMessageTree)
- [test/test_phase8_message_variants.py](../test/test_phase8_message_variants.py) - Phase 8: Message variants
- [test/test_renderer.py](../test/test_renderer.py) - Phase 8: Renderer edge cases
- [test/test_renderer_code.py](../test/test_renderer_code.py) - Phase 8: Code highlighting/diff tests

### External
- golergka's branch: `remotes/golergka/feat/text-output-format` (commit ada7ef5)
