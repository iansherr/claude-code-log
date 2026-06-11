"""Tests for the hybrid JSON/Markdown value rendering in render_params_table.

String values render as (escaped) Markdown unless they look like
XML/HTML or JSON; dict/list values recurse into nested tables with a
depth guard; scalars and long-value collapsibility keep their legacy
behavior.
"""

from claude_code_log.html.tool_formatters import (
    _PARAMS_TABLE_MAX_DEPTH,
    render_params_table,
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
        """Structured-table folds get the explicit hint + rows-toggle
        button; plain string folds and the JSON fallback do not."""
        value = {f"key_{i}": f"value {i}" for i in range(20)}
        html = render_params_table({"cfg": value})
        assert "tool-param-collapsible-rows" in html
        assert "tool-param-collapse-hint" in html
        assert "tool-param-rows-toggle" in html
        assert "expand rows" in html

        string_fold = render_params_table({"v": "plain words " * 20})
        assert "tool-param-rows-toggle" not in string_fold

        json_fallback = render_params_table({"v": "{" + "x" * 300})
        assert "tool-param-rows-toggle" not in json_fallback

    def test_empty_containers_fall_back_to_json_dump(self):
        html = render_params_table({"a": {}, "b": []})
        assert html.count("tool-param-structured") == 2
        assert "{}" in html and "[]" in html


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
