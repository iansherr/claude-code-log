"""Reference test plugin for claude-code-log.

Demonstrates both transformer-shape capabilities described in
``work/tool-renderer-plugins.md``:

- ``transformers.hook_demotion`` — UserTextMessage rewrite by text-prefix
  match, returning an existing core MessageContent variant
  (TaskNotificationMessage) so the test can observe the demotion.

- ``transformers.tool_communicate`` — ToolUseMessage rewrite for a
  specific MCP tool name, producing a plugin-defined subclass that
  carries its own class-side format_markdown / format_html / title
  methods (exercises Strategy 2 of _dispatch_format).

This is intentionally minimal — the goal is contract coverage, not
realistic clmail rendering. A real clmail plugin would target
``mcp__plugin_clmail_clmail__communicate`` and ship richer formatters.
"""
