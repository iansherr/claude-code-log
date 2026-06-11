"""Tests for the hybrid JSON/Markdown value rendering in render_params_table.

String values render as (escaped) Markdown unless they look like
XML/HTML or JSON; dict/list values recurse into nested tables with a
depth guard; scalars and long-value collapsibility keep their legacy
behavior.
"""

from claude_code_log.html.tool_formatters import (
    _PARAMS_TABLE_MAX_DEPTH,
    _PARAMS_TABLE_MAX_ITEMS,
    format_tool_result_content_raw,
    render_params_table,
)
from claude_code_log.models import ToolResultContent


def _tool_result(content: str, is_error: bool = False) -> ToolResultContent:
    return ToolResultContent(
        type="tool_result", tool_use_id="t1", content=content, is_error=is_error
    )


class TestMarkdownStrings:
    """String values are treated as potential Markdown."""

    def test_short_markdown_string_renders_inline(self):
        html = render_params_table({"note": "use `rg` over **grep**"})
        assert "<code>rg</code>" in html
        assert "<strong>grep</strong>" in html

    def test_long_markdown_string_uses_collapsible_renderer(self):
        body = "\n".join(f"- item {i} with `code`" for i in range(30))
        html = render_params_table({"prompt": body})
        assert "tool-param-markdown" in html
        # Long multi-line content folds via the markdown collapsible.
        assert "<details" in html
        assert "<code>code</code>" in html

    def test_markdown_path_escapes_raw_html(self):
        """XSS posture: the Markdown path must escape, never inject."""
        html = render_params_table({"v": "hello <script>alert(1)</script> world"})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_long_markdown_path_escapes_raw_html(self):
        body = "start\n\n<script>alert(1)</script>\n\n" + "filler\n" * 30
        html = render_params_table({"v": body})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestMarkupLikeStrings:
    """Strings that look like XML/HTML or JSON keep escaped raw text."""

    def test_xmlish_string_stays_escaped_raw(self):
        html = render_params_table({"v": "<task>do *not* render</task>"})
        assert "&lt;task&gt;do *not* render&lt;/task&gt;" in html
        assert "<em>" not in html

    def test_jsonish_object_string_stays_escaped_raw(self):
        html = render_params_table({"v": '{"a": "*x*"}'})
        assert "&quot;*x*&quot;" in html
        assert "<em>" not in html

    def test_jsonish_array_string_stays_escaped_raw(self):
        html = render_params_table({"v": '  ["*x*"]'})
        assert "<em>" not in html

    def test_long_raw_string_still_collapsible(self):
        value = "<" + "x" * 200
        html = render_params_table({"v": value})
        assert "tool-param-collapsible" in html
        assert "tool-param-preview" in html


class TestNestedStructures:
    """Dict and list values recurse into nested tables."""

    def test_nested_dict_renders_table(self):
        html = render_params_table({"cfg": {"mode": "fast", "retries": 3}})
        assert html.count("<table class='tool-params-table") == 2
        assert "tool-params-nested" in html
        assert "mode" in html and "fast" in html
        # No JSON dump for the recursed value.
        assert "tool-param-structured" not in html

    def test_list_renders_indexed_rows(self):
        html = render_params_table({"items": ["alpha", "beta"]})
        assert "tool-params-nested" in html
        assert "<td class='tool-param-key'>0</td>" in html
        assert "<td class='tool-param-key'>1</td>" in html
        assert "alpha" in html and "beta" in html

    def test_structures_always_fold_regardless_of_size(self):
        """Even short dict/list values render collapsed — sibling rows
        must look consistent (no size-based auto-expand)."""
        html = render_params_table({"a": {"k": 1}, "b": [1, 2]})
        assert html.count("<details class='tool-param-collapsible") == 2
        # Short JSON: the preview is the full dump, no ellipsis.
        assert "..." not in html

    def test_fold_button_label_matches_container_kind(self):
        dict_html = render_params_table({"cfg": {"k": {"nested": 1}}})
        assert "expand all properties" in dict_html
        assert "data-kind='properties'" in dict_html

        list_html = render_params_table({"items": [[1], [2]]})
        assert "expand all rows" in list_html
        assert "data-kind='rows'" in list_html

    def test_no_rows_toggle_without_row_folds(self):
        """All-scalar containers fold but carry no dead button; a long
        string value counts as a row fold (it opens via the button)."""
        scalar_only = render_params_table({"cfg": {"id": 2, "status": "ok"}})
        assert "tool-param-collapsible" in scalar_only
        assert "tool-param-rows-toggle" not in scalar_only
        assert "tool-param-collapsible-rows" not in scalar_only

        with_string_fold = render_params_table(
            {"cfg": {"id": 2, "desc": "long prose " * 20}}
        )
        assert "tool-param-rows-toggle" in with_string_fold

    def test_list_of_dicts_recurses_both_levels(self):
        html = render_params_table({"qs": [{"q": "one"}, {"q": "two"}]})
        # Outer + list + two element tables.
        assert html.count("<table class='tool-params-table") == 4
        assert "one" in html and "two" in html

    def test_markdown_leaf_inside_nested_dict(self):
        html = render_params_table({"cfg": {"desc": "see `cli.py`"}})
        assert "<code>cli.py</code>" in html

    def test_long_structure_folds_with_json_preview(self):
        value = {f"key_{i}": f"value {i}" for i in range(20)}
        html = render_params_table({"cfg": value})
        assert "tool-param-collapsible" in html
        assert "tool-param-preview" in html
        # The expanded body is a table, not a JSON dump.
        assert "tool-params-nested" in html

    def test_table_fold_carries_rows_toggle(self):
        """Structured-table folds with foldable rows get the explicit
        hint + rows-toggle button; plain string folds and the JSON
        fallback do not."""
        value = {f"key_{i}": {"nested": i} for i in range(5)}
        html = render_params_table({"cfg": value})
        assert "tool-param-collapsible-rows" in html
        assert "tool-param-collapse-hint" in html
        assert "tool-param-rows-toggle" in html
        assert "expand all properties" in html

        string_fold = render_params_table({"v": "plain words " * 20})
        assert "tool-param-rows-toggle" not in string_fold

        json_fallback = render_params_table({"v": "{" + "x" * 300})
        assert "tool-param-rows-toggle" not in json_fallback

    def test_empty_containers_fall_back_to_json_dump(self):
        html = render_params_table({"a": {}, "b": []})
        assert html.count("tool-param-structured") == 2
        assert "{}" in html and "[]" in html


class TestExpandAllControl:
    """Top-level expand-all button above renderers that contain folds."""

    def test_control_present_when_folds_exist(self):
        html = render_params_table({"cfg": {"k": 1}})
        assert "tool-params-root" in html
        assert "tool-params-expand-all" in html
        assert "expand all" in html
        # The control sits above the table.
        assert html.index("tool-params-expand-all") < html.index("<table")

    def test_no_control_on_flat_params(self):
        html = render_params_table({"path": "/src", "mode": "fast"})
        assert "tool-params-root" not in html
        assert "tool-params-expand-all" not in html

    def test_string_fold_counts_as_fold(self):
        html = render_params_table({"prompt": "words " * 40})
        assert "tool-params-expand-all" in html

    def test_json_result_with_folds_gets_control(self):
        import json

        content = json.dumps([{"id": i, "msg": f"row {i}"} for i in range(40)])
        html = format_tool_result_content_raw(_tool_result(content))
        assert "tool-result-json" in html
        assert "tool-params-expand-all" in html

    def test_small_flat_json_result_has_no_control(self):
        html = format_tool_result_content_raw(_tool_result('{"a": "b"}'))
        assert "tool-params-expand-all" not in html


class TestDepthGuard:
    """Past the max depth, values fall back to the JSON dump."""

    def test_pathological_nesting_falls_back_to_json(self):
        value = {"leaf": "*md*"}
        for _ in range(_PARAMS_TABLE_MAX_DEPTH + 2):
            value = {"nested": value}
        html = render_params_table({"deep": value})
        # Table nesting is bounded...
        assert html.count("<table") <= _PARAMS_TABLE_MAX_DEPTH + 1
        # ...and the remainder renders as an escaped JSON dump.
        assert "tool-param-structured" in html
        assert "*md*" in html
        assert "<em>" not in html

    def test_depth_within_limit_renders_tables(self):
        html = render_params_table({"a": {"b": {"c": "leaf"}}})
        assert "tool-param-structured" not in html
        assert "leaf" in html


class TestBreadthCap:
    """Wider containers than _PARAMS_TABLE_MAX_ITEMS fall back to the
    JSON dump — the table HTML must not even be generated."""

    def test_wide_list_falls_back_to_json_dump(self):
        value = list(range(_PARAMS_TABLE_MAX_ITEMS + 1))
        html = render_params_table({"items": value})
        assert "tool-param-structured" in html
        assert "tool-params-nested" not in html

    def test_wide_dict_falls_back_to_json_dump(self):
        value = {f"k{i}": i for i in range(_PARAMS_TABLE_MAX_ITEMS + 1)}
        html = render_params_table({"cfg": value})
        assert "tool-param-structured" in html
        assert "tool-params-nested" not in html

    def test_at_cap_still_renders_table(self):
        value = list(range(_PARAMS_TABLE_MAX_ITEMS))
        html = render_params_table({"items": value})
        assert "tool-params-nested" in html
        assert "tool-param-structured" not in html

    def test_wide_json_result_keeps_legacy_text_rendering(self):
        import json

        content = json.dumps(list(range(_PARAMS_TABLE_MAX_ITEMS + 1)))
        html = format_tool_result_content_raw(_tool_result(content))
        assert "tool-result-json" not in html
        assert "collapsible-details" in html


class TestScalarsAndLegacy:
    """Scalars and the empty-params card are unchanged."""

    def test_scalars_render_plain(self):
        html = render_params_table({"n": 42, "flag": True, "none": None, "f": 1.5})
        assert ">42<" in html
        assert ">True<" in html
        assert ">None<" in html
        assert ">1.5<" in html

    def test_empty_params(self):
        assert "tool-params-empty" in render_params_table({})

    def test_key_is_escaped(self):
        html = render_params_table({"<key>": "v"})
        assert "&lt;key&gt;" in html


class TestJsonToolResults:
    """Generic tool results that parse as JSON render as tables."""

    def test_object_result_renders_table(self):
        result = _tool_result('{"status": "ok", "count": 3}')
        html = format_tool_result_content_raw(result)
        assert "tool-result-json" in html
        assert "tool-params-table" in html
        assert "status" in html and "ok" in html

    def test_array_result_renders_indexed_table(self):
        result = _tool_result('[{"id": 1}, {"id": 2}]')
        html = format_tool_result_content_raw(result)
        assert "tool-result-json" in html
        assert "<td class='tool-param-key'>0</td>" in html
        assert "<td class='tool-param-key'>1</td>" in html

    def test_invalid_json_stays_text(self):
        result = _tool_result('{"status": "ok", trailing')
        html = format_tool_result_content_raw(result)
        assert "tool-result-json" not in html
        assert "<pre>" in html

    def test_non_json_text_unchanged(self):
        result = _tool_result("plain output\nlines")
        html = format_tool_result_content_raw(result)
        assert "tool-result-json" not in html

    def test_scalar_and_empty_json_stay_text(self):
        for content in ("42", '"quoted"', "{}", "[]"):
            html = format_tool_result_content_raw(_tool_result(content))
            assert "tool-result-json" not in html, content

    def test_error_result_stays_text(self):
        result = _tool_result('{"error": "boom"}', is_error=True)
        html = format_tool_result_content_raw(result)
        assert "tool-result-json" not in html

    def test_json_result_values_are_escaped(self):
        result = _tool_result('{"v": "<script>alert(1)</script>"}')
        html = format_tool_result_content_raw(result)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_large_result_folds_with_rows_toggle(self):
        """Breadth guard: a big JSON array result starts collapsed (like
        the legacy >200-char text fold) with the expand-all button."""
        import json

        content = json.dumps([{"id": i, "msg": f"row {i} " * 10} for i in range(50)])
        html = format_tool_result_content_raw(_tool_result(content))
        # The top-level table is inside a fold, not bare in the card.
        assert html.startswith("<div class='tool-result-json'>")
        before_table = html.split("<table", 1)[0]
        assert "tool-param-collapsible-rows" in before_table
        assert "expand all rows" in before_table

    def test_large_scalar_only_result_folds_plain(self):
        import json

        content = json.dumps({f"key_{i}": f"v{i}" for i in range(30)})
        assert len(content) > 200
        html = format_tool_result_content_raw(_tool_result(content))
        before_table = html.split("<table", 1)[0]
        assert "tool-param-collapsible" in before_table
        assert "tool-param-rows-toggle" not in html

    def test_small_result_table_stays_unfolded(self):
        html = format_tool_result_content_raw(_tool_result('{"status": "ok"}'))
        assert "<details" not in html
        assert "tool-params-table" in html
