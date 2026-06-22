# Claude Code Log GitHub Action

Generate readable HTML transcripts from Claude Code sessions after running the Claude Code GitHub Action.

## Usage

### Basic Usage (after Claude Code Action)

```yaml
- name: Run Claude Code
  uses: anthropics/claude-code-base-action@v1
  with:
    prompt: "Fix the bug in auth.ts"
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}

- name: Generate HTML logs
  uses: iansherr/claude-code-log@main
```

### With Custom Options

```yaml
- name: Generate HTML logs
  uses: iansherr/claude-code-log@main
  with:
    output_dir: 'claude-code-logs'
    format: 'html'
    detail: 'high'
    from_date: 'yesterday'
    to_date: 'today'
    compact: 'true'
```

### As Standalone Action

```yaml
- name: Generate logs from all sessions
  uses: iansherr/claude-code-log@main
  with:
    claude_projects_dir: '~/.claude/projects'
    output_dir: 'all-logs'
    detail: 'low'
```

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `claude_projects_dir` | Path to Claude projects directory | `~/.claude/projects/` |
| `output_dir` | Directory for generated files | `claude-code-logs` |
| `format` | Output format: `html` or `md` | `html` |
| `detail` | Detail level: `full`, `high`, `low`, `minimal`, `user-only` | `full` |
| `compact` | Compact mode (works with md format) | `false` |
| `from_date` | Filter from date (natural language) | - |
| `to_date` | Filter until date (natural language) | - |
| `open_browser` | Open HTML in browser after generation | `false` |

## Outputs

| Output | Description |
|--------|-------------|
| `html_path` | Path to generated index file |
| `session_count` | Number of sessions processed |

## Example Workflow

```yaml
name: Claude Code with Logs

on:
  issue_comment:
    types: [created]

jobs:
  claude:
    if: contains(github.event.comment.body, '@claude')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run Claude Code
        id: claude
        uses: anthropics/claude-code-base-action@v1
        with:
          prompt: ${{ github.event.comment.body }}
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          allowed_tools: "Bash(git:*),View,GlobTool,GrepTool"

      - name: Generate HTML logs
        uses: iansherr/claude-code-log@main
        if: always()  # Generate logs even if Claude fails

      - name: Upload logs artifact
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: claude-code-logs
          path: claude-code-logs/
          retention-days: 30
```

## Notes

- The action reads JSONL transcripts from `~/.claude/projects/` (or custom path)
- HTML files are uploaded as GitHub Actions artifacts with 30-day retention
- Works best when combined with `anthropics/claude-code-base-action`
- Can also run standalone to process existing Claude sessions
