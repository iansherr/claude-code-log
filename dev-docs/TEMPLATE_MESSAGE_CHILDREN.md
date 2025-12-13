# Template Message Children Architecture

This document tracks the exploration of a children-based architecture for `TemplateMessage`, where messages can have nested children to form an explicit tree structure.

## Current Architecture (2025-12-13)

### Data Flow
```
TranscriptEntry[] → generate_template_messages() → root_messages (tree)
                                                          ↓
                    HtmlRenderer._flatten_preorder() → flat_list
                                                          ↓
                              template.render(messages=flat_list)
```

### TemplateMessage (current)
- `generate_template_messages()` returns **tree roots** (typically session headers)
- Each message has `children: List[TemplateMessage]` populated
- `ancestry` field preserved for CSS classes / JavaScript fold/unfold
- HtmlRenderer flattens via pre-order traversal before template rendering

### Hierarchy Levels
```
Level 0: Session headers (tree roots)
Level 1: User messages
Level 2: Assistant, System, Thinking
Level 3: Tool use/result
Level 4: Sidechain assistant/thinking
Level 5: Sidechain tools
```

### Template Rendering (current)
- Single `{% for message in messages %}` loop over flattened list
- Ancestry rendered as CSS classes for JavaScript DOM queries
- Fold/unfold uses `document.querySelectorAll('.message.${targetId}')`
- Tree structure used internally but template still receives flat list

## Future: Recursive Template Rendering

The next step would be to pass tree roots directly to the template and use a recursive macro, eliminating the flatten step.

### Template Rendering (future)
Recursive macro approach:
```jinja2
{% macro render_message(message, depth=0) %}
<div class='message {{ message.css_class }}' data-depth='{{ depth }}'>
    <div class='content'>{{ message.content_html | safe }}</div>
    {% if message.children %}
    <div class='children'>
        {% for child in message.children %}
        {{ render_message(child, depth + 1) }}
        {% endfor %}
    </div>
    {% endif %}
</div>
{% endmacro %}

{% for root in roots %}
{{ render_message(root) }}
{% endfor %}
```

### JavaScript Simplification (future)
With nested DOM structure, fold/unfold becomes trivial:
```javascript
// Hide all children
messageEl.querySelector('.children').style.display = 'none';
// Show children
messageEl.querySelector('.children').style.display = '';
```

This would require updating the fold/unfold JavaScript to work with the nested structure rather than CSS class queries.

## Exploration Log

### Phase 1: Foundation ✅ COMPLETE
- [x] Add `children` field to TemplateMessage (commit `7077f68`)
- [x] Keep existing flat-list behavior working
- [x] Add `flatten()` method for backward compatibility (commit `ed4d7b3`)
  - Instance method `flatten()` returns self + all descendants in depth-first order
  - Static method `flatten_all()` flattens list of root messages
  - Unit tests in `test/test_template_data.py::TestTemplateMessageTree`

### Phase 2: Tree Building ✅ COMPLETE
- [x] Create `_build_message_tree()` function (commit `83fcf31`)
  - Takes flat list with `message_id` and `ancestry` already set
  - Populates `children` field based on ancestry
  - Returns list of root messages (those with empty ancestry)
- [x] Called after `_mark_messages_with_children()` in render pipeline
- [x] Integration tests verify tree building doesn't break HTML generation

### Phase 2.5: Tree-First Architecture ✅ COMPLETE (2025-12-13)
- [x] `generate_template_messages()` now returns tree roots, not flat list (commit `c5048b9`)
- [x] `HtmlRenderer._flatten_preorder()` traverses tree, formats content, builds flat list
- [x] Content formatting happens during pre-order traversal (no separate pass)
- [x] Template unchanged - still receives flat list

**Key insight:** The flat list was being passed to template AND the same messages had children populated. This caused confusion about which structure was authoritative. Now the tree is authoritative and the flat list is derived.

### Phase 3: Template Migration (TODO - Future Work)
- [ ] Create recursive render macro
- [ ] Update DOM structure to use nested `.children` divs
- [ ] Migrate JavaScript fold/unfold to use nested DOM
- [ ] Pass `root_messages` directly to template (eliminate flatten step)

### Challenges & Notes

**Current State (2025-12-13):**
- Tree is the primary structure returned from `generate_template_messages()`
- HtmlRenderer flattens via pre-order traversal for template rendering
- This is cleaner than before: tree in → flat list out (explicit transformation)

**Performance (2025-12-13):**
- Benchmark: 3.35s for 3917 messages across 5 projects
- Pre-order traversal + formatting is O(n)
- No caching needed - each message formatted exactly once

**Why Keep Flat Template (for now):**
1. JavaScript fold/unfold relies on CSS class queries
2. Changing DOM structure requires JS migration
3. Current approach works correctly

## Related Work

### golergka's text-output-format PR
Created `content_extractor.py` for shared content parsing:
- Separates data extraction from presentation
- Dataclasses for extracted content: `ExtractedText`, `ExtractedToolUse`, etc.
- Could be extended for the tree-building approach

### Visitor Pattern Consideration
For multi-format output (HTML, Markdown, JSON), consider:
- TemplateMessage as a tree data structure (no rendering logic)
- Visitor implementations for each output format
- Preparation in converter.py before any rendering
