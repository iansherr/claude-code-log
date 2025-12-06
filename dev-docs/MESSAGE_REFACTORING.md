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

## Planned Future Phases

### Phase 5: Message Processing Decomposition

**Goal**: Break down the 687-line `_process_messages_loop()` into smaller functions

**Current Structure** (lines 3400-4086):
```
_process_messages_loop()
├── Session header creation
├── Message type detection (user/assistant/system/summary)
├── Content extraction and rendering
├── Tool use context building
├── CSS class determination
├── TemplateMessage creation
├── Message processors (_process_command_message, etc.)
└── Stats accumulation
```

**Proposed Decomposition**:
1. **`_create_session_header()`** - Session header TemplateMessage creation
2. **`_process_user_message()`** - User message handling
3. **`_process_assistant_message()`** - Assistant message with tool use extraction
4. **`_process_system_message()`** - System message (commands, errors, info)
5. **`_process_summary_message()`** - Summary handling
6. **Message type router** - Dispatch to appropriate processor

**Key Insight**: The current processors (`_process_command_message`, `_process_bash_input`, etc.) return `(header, content, css_class, border_color)` tuples. Consider using a dataclass:

```python
@dataclass
class ProcessedContent:
    header: str
    content: str
    css_class: str
    border_color: str
    preview_content: str = ""
    additional_css: str = ""
```

**Expected Result**: `_process_messages_loop()` reduced to ~200 lines of orchestration

### Phase 6: Message Pairing Simplification

**Goal**: Simplify the complex pairing logic in `_identify_message_pairs()`

**Current Complexity** (227 lines):
- Multiple pairing strategies (tool use/result, command/output, system/slash)
- Nested conditionals for edge cases
- Magic string matching for message content

**Proposed Changes**:
1. Create explicit `PairingStrategy` classes:
   - `ToolUsePairingStrategy` - tool_use_id matching
   - `ParentUuidPairingStrategy` - parentUuid linking
   - `ContentMatchPairingStrategy` - content-based matching (command output)
2. Apply strategies in sequence
3. Better documentation of pairing rules

**Alternative**: If pairing logic is stable, leave as-is and focus on other phases first.

### Phase 7: Hierarchy System Documentation

**Goal**: Document the hierarchy/fold system architecture

**Current Functions**:
- `_get_message_hierarchy_level()` - Level from CSS class (simplified in v0.9)
- `_build_message_hierarchy()` - Ancestry building
- `_mark_messages_with_children()` - Descendant counting

**Document**:
- Level definitions (0=session, 1=user, 2=assistant/system, 3=tools)
- Ancestry calculation for fold/unfold
- Interaction with JavaScript fold controls
- Edge cases (sidechain, paired messages)

**Location**: `dev-docs/FOLD_STATE_DIAGRAM.md` (update existing)

### Phase 8: Testing Infrastructure

**Goal**: Improve test coverage for refactored modules

**Current Coverage**: ~78%

**Priority Tests**:
1. Unit tests for extracted ANSI module
2. Unit tests for tool formatters with edge cases
3. Integration tests for message pairing
4. Property-based tests for hierarchy calculation
5. Snapshot tests for new message types

**Test Data**:
- Add more representative JSONL samples to `test/test_data/`
- Create fixtures for common message patterns

## Recommended Execution Order

For maximum impact with minimum risk:

1. **Phase 3 (ANSI)** - Low risk, self-contained, immediate ~250 line reduction
2. **Phase 4 (Tools)** - Medium risk, large reduction (~600 lines), clear boundaries
3. **Phase 7 (Docs)** - No code changes, improves understanding for Phase 5-6
4. **Phase 5 (Processing)** - High impact, requires careful testing
5. **Phase 6 (Pairing)** - Only if pairing bugs persist; otherwise skip
6. **Phase 8 (Testing)** - Ongoing, add tests as modules are extracted

**Tree Refactoring Integration:**
- Tree building (TEMPLATE_MESSAGE_CHILDREN.md Phase 1-2) is complete and non-blocking
- Template migration (Phase 3) should wait until after Phase 4 (Tools) here
- golergka's text formats can be integrated after Phase 4, leveraging both extraction layers

**golergka Integration Timing:**
- Wait until Phase 3-4 complete to minimize merge conflicts
- When integrating, resolve `render_message_content()` conflicts carefully
- Consider whether tree structure benefits text renderer

## Metrics to Track

| Metric | Baseline (v0.9) | Current (Phase 3-4 done) | Target |
|--------|-----------------|-------------------------|--------|
| renderer.py lines | 4246 | 3730 | <3000 |
| Largest function | ~687 lines | ~687 lines | <100 lines |
| Module count | 3 (renderer, timings, models) | 5 (+ansi_colors, +renderer_code) | 6-7 |
| Test coverage | ~78% | ~78% | >85% |

**Progress**: 516 lines extracted from renderer.py (12% reduction)

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

- [renderer.py](../claude_code_log/renderer.py) - Main rendering module (3730 lines)
- [ansi_colors.py](../claude_code_log/ansi_colors.py) - ANSI color conversion (261 lines) - Phase 3
- [renderer_code.py](../claude_code_log/renderer_code.py) - Code highlighting & diffs (330 lines) - Phase 4
- [renderer_timings.py](../claude_code_log/renderer_timings.py) - Timing utilities
- [test/test_ansi_colors.py](../test/test_ansi_colors.py) - ANSI tests
- [test/test_preview_truncation.py](../test/test_preview_truncation.py) - Code preview tests
- [test/test_sidechain_agents.py](../test/test_sidechain_agents.py) - Integration tests
- [dev-docs/FOLD_STATE_DIAGRAM.md](FOLD_STATE_DIAGRAM.md) - Fold system documentation
- [dev-docs/TEMPLATE_MESSAGE_CHILDREN.md](TEMPLATE_MESSAGE_CHILDREN.md) - Tree architecture exploration
- [test/test_template_data.py](../test/test_template_data.py) - Tree building tests (TestTemplateMessageTree)
- golergka's branch: `remotes/golergka/feat/text-output-format` (commit ada7ef5)
