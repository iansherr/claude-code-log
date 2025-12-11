#!/usr/bin/env python3
"""Extract sample messages for documentation.

This script finds examples of each message type and content type from
real session data and creates:
1. Abbreviated JSON files (readable, for documentation)
2. Full JSONL lines (complete data, for reference)

The output message categories map input JSONL types to rendered HTML:

INPUT (JSONL)              -> OUTPUT (HTML css_class)
---------------------------------------------------------
user + text content        -> "user"
user + text (compacted)    -> "user compacted"
user + text (slash-command)-> "user slash-command"
user + text (sidechain)    -> "user sidechain" (skipped in main)
user + tool_result         -> "tool_result" (separate message)
user + tool_result error   -> "tool_result error"
user + image               -> "image"
assistant + text           -> "assistant"
assistant + text (sidechain)-> "assistant sidechain"
assistant + thinking       -> "thinking"
assistant + tool_use       -> "tool_use"
system (command-name)      -> "system"
system (command-output)    -> "system command-output"
system (level=info)        -> "system system-info"
system (level=warning)     -> "system system-warning"
system (level=error)       -> "system system-error"
system (hook summary)      -> "system system-hook"
session header             -> "session-header"
summary                    -> (not rendered as message)
queue-operation            -> (not rendered as message)
file-history-snapshot      -> (not rendered as message)
"""

import json
import sys
from pathlib import Path
from typing import Any, Callable, TypedDict


class CategoryDef(TypedDict):
    """Type definition for OUTPUT_CATEGORIES entries."""

    css_class: str | None
    description: str
    input_type: str
    subdir: str
    filter: Callable[[dict[str, Any]], bool]


# Add project root to path to import from claude_code_log
sys.path.insert(0, str(Path(__file__).parent.parent))

# Smallest valid base64 PNG (8x8 transparent)
TINY_BASE64_IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAIAQMAAAD+wSzIAAAABlBMVEX///+/v7+jQ3Y5AAAADklEQVQI12P4AIX8EAgALgAD/aNpbtEAAAAASUVORK5CYII"


def truncate_text(text: str, max_lines: int = 3, max_len: int = 200) -> str:
    """Truncate text to a few lines."""
    if not text:
        return text
    lines = text.split("\n")
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines]) + "\n... [truncated]"
    if len(text) > max_len:
        text = text[:max_len] + "... [truncated]"
    return text


def abbreviate_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Abbreviate a message for documentation."""
    result = {}

    # Keep essential fields
    for key in [
        "type",
        "sessionId",
        "timestamp",
        "uuid",
        "parentUuid",
        "isSidechain",
        "isMeta",
        "level",
        "subtype",
        "content",
    ]:
        if key in msg:
            if key == "content" and isinstance(msg[key], str):
                result[key] = truncate_text(msg[key])
            else:
                result[key] = msg[key]

    # Abbreviate message content
    if "message" in msg:
        message = msg["message"]
        result["message"] = {}

        for key in ["role", "type", "model", "id"]:
            if key in message:
                result["message"][key] = message[key]

        if "content" in message:
            content = message["content"]
            if isinstance(content, str):
                result["message"]["content"] = truncate_text(content)
            elif isinstance(content, list):
                result["message"]["content"] = []
                for item in content[:3]:  # Max 3 content items
                    abbrev_item = abbreviate_content_item(item)
                    result["message"]["content"].append(abbrev_item)
                if len(content) > 3:
                    result["message"]["content"].append(
                        {"_note": f"... +{len(content) - 3} more items"}
                    )

    # Abbreviate tool use result
    if "toolUseResult" in msg:
        tur = msg["toolUseResult"]
        result["toolUseResult"] = {}
        if "type" in tur:
            result["toolUseResult"]["type"] = tur["type"]
        if "stdout" in tur:
            result["toolUseResult"]["stdout"] = truncate_text(tur["stdout"])
        if "stderr" in tur:
            result["toolUseResult"]["stderr"] = truncate_text(tur["stderr"])
        if "file" in tur:
            result["toolUseResult"]["file"] = {
                "filePath": tur["file"].get("filePath", ""),
                "content": truncate_text(tur["file"].get("content", "")),
            }

    # Abbreviate summary
    if "summary" in msg:
        result["summary"] = truncate_text(msg["summary"])
    if "leafUuid" in msg:
        result["leafUuid"] = msg["leafUuid"]

    return result


def abbreviate_content_item(item: dict[str, Any]) -> dict[str, Any]:
    """Abbreviate a content item."""
    result = {"type": item.get("type", "unknown")}

    if item.get("type") == "text":
        result["text"] = truncate_text(item.get("text", ""))

    elif item.get("type") == "thinking":
        result["thinking"] = truncate_text(item.get("thinking", ""))

    elif item.get("type") == "tool_use":
        result["id"] = item.get("id", "")
        result["name"] = item.get("name", "")
        inp = item.get("input", {})
        # Abbreviate input
        if isinstance(inp, dict):
            result["input"] = {}
            for k, v in list(inp.items())[:3]:
                if isinstance(v, str):
                    result["input"][k] = truncate_text(v, max_lines=2, max_len=100)
                else:
                    result["input"][k] = v
            if len(inp) > 3:
                result["input"]["_note"] = f"... +{len(inp) - 3} more fields"

    elif item.get("type") == "tool_result":
        result["tool_use_id"] = item.get("tool_use_id", "")
        result["is_error"] = item.get("is_error", False)
        content = item.get("content", "")
        if isinstance(content, str):
            result["content"] = truncate_text(content)
        elif isinstance(content, list):
            result["content"] = [{"_note": f"{len(content)} items"}]

    elif item.get("type") == "image":
        source = item.get("source", {})
        result["source"] = {
            "type": source.get("type", "base64"),
            "media_type": source.get("media_type", "image/png"),
            "data": TINY_BASE64_IMAGE + " [abbreviated]",
        }

    return result


# Output message categories - maps to CSS classes
# Each category specifies the subdirectory where samples are written
OUTPUT_CATEGORIES: dict[str, CategoryDef] = {
    # User message variants -> user/
    "user": {
        "css_class": "user",
        "description": "Regular user prompt",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (
            m.get("type") == "user"
            and not m.get("isSidechain")
            and not m.get("isMeta")
            and _has_text_content(m)
            and not _is_compacted(m)
        ),
    },
    "user_compacted": {
        "css_class": "user compacted",
        "description": "Compacted conversation summary",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (
            m.get("type") == "user" and not m.get("isSidechain") and _is_compacted(m)
        ),
    },
    "user_slash_command": {
        "css_class": "user slash-command",
        "description": "Expanded slash command prompt (isMeta=true)",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (m.get("type") == "user" and m.get("isMeta")),
    },
    "user_sidechain": {
        "css_class": "user sidechain",
        "description": "Sub-agent user prompt (usually skipped)",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (
            m.get("type") == "user"
            and m.get("isSidechain")
            and _has_text_content(m)
            and not m.get("isMeta")
        ),
    },
    "image": {
        "css_class": "image",
        "description": "User-attached image",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (m.get("type") == "user" and _has_image(m)),
    },
    "bash_input": {
        "css_class": "user",
        "description": "Bash command input (from background Bash tool)",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (m.get("type") == "user" and _has_bash_input(m)),
    },
    "bash_output": {
        "css_class": "user",
        "description": "Bash command output (stdout/stderr)",
        "input_type": "user",
        "subdir": "user",
        "filter": lambda m: (m.get("type") == "user" and _has_bash_output(m)),
    },
    # Assistant message variants -> assistant/
    "assistant": {
        "css_class": "assistant",
        "description": "Assistant text response",
        "input_type": "assistant",
        "subdir": "assistant",
        "filter": lambda m: (
            m.get("type") == "assistant"
            and not m.get("isSidechain")
            and _has_text_content(m)
        ),
    },
    "assistant_sidechain": {
        "css_class": "assistant sidechain",
        "description": "Sub-agent assistant response",
        "input_type": "assistant",
        "subdir": "assistant",
        "filter": lambda m: (
            m.get("type") == "assistant"
            and m.get("isSidechain")
            and _has_text_content(m)
        ),
    },
    "thinking": {
        "css_class": "thinking",
        "description": "Extended thinking content",
        "input_type": "assistant",
        "subdir": "assistant",
        "filter": lambda m: (m.get("type") == "assistant" and _has_thinking(m)),
    },
    # System message variants -> system/
    "system_command": {
        "css_class": "system",
        "description": "User-initiated command (e.g., /context)",
        "input_type": "system",
        "subdir": "system",
        "filter": lambda m: (
            m.get("type") == "system" and _has_command_name(m.get("content", ""))
        ),
    },
    "system_command_output": {
        "css_class": "system command-output",
        "description": "Command output (e.g., from /context)",
        "input_type": "system",
        "subdir": "system",
        "filter": lambda m: (
            m.get("type") == "system" and _has_command_output(m.get("content", ""))
        ),
    },
    "system_info": {
        "css_class": "system system-info",
        "description": "System info message",
        "input_type": "system",
        "subdir": "system",
        "filter": lambda m: (
            m.get("type") == "system"
            and m.get("level") == "info"
            and not _has_command_name(m.get("content", ""))
            and not _has_command_output(m.get("content", ""))
        ),
    },
    "system_warning": {
        "css_class": "system system-warning",
        "description": "System warning message",
        "input_type": "system",
        "subdir": "system",
        "filter": lambda m: (m.get("type") == "system" and m.get("level") == "warning"),
    },
    "system_error": {
        "css_class": "system system-error",
        "description": "System error message",
        "input_type": "system",
        "subdir": "system",
        "filter": lambda m: (m.get("type") == "system" and m.get("level") == "error"),
    },
    "system_hook": {
        "css_class": "system system-hook",
        "description": "Hook execution summary",
        "input_type": "system",
        "subdir": "system",
        "filter": lambda m: (
            m.get("type") == "system" and m.get("subtype") == "stop_hook_summary"
        ),
    },
    # Non-rendered types -> system/
    "summary": {
        "css_class": None,
        "description": "Session summary (not rendered as message)",
        "input_type": "summary",
        "subdir": "system",
        "filter": lambda m: m.get("type") == "summary",
    },
    "queue_operation": {
        "css_class": None,
        "description": "Queue operation (not rendered as message)",
        "input_type": "queue-operation",
        "subdir": "system",
        "filter": lambda m: m.get("type") == "queue-operation",
    },
    "file_history_snapshot": {
        "css_class": None,
        "description": "File history snapshot (not rendered as message)",
        "input_type": "file-history-snapshot",
        "subdir": "system",
        "filter": lambda m: m.get("type") == "file-history-snapshot",
    },
}


def _has_text_content(msg: dict) -> bool:
    """Check if message has text content."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(item.get("type") == "text" for item in content)
    return False


def _has_tool_result(msg: dict) -> bool:
    """Check if message has tool_result content."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(item.get("type") == "tool_result" for item in content)
    return False


def _has_tool_result_error(msg: dict) -> bool:
    """Check if message has tool_result with is_error=True."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(
            item.get("type") == "tool_result" and item.get("is_error")
            for item in content
        )
    return False


def _has_image(msg: dict) -> bool:
    """Check if message has image content."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(item.get("type") == "image" for item in content)
    return False


def _has_thinking(msg: dict) -> bool:
    """Check if message has thinking content."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(item.get("type") == "thinking" for item in content)
    return False


def _has_tool_use(msg: dict) -> bool:
    """Check if message has tool_use content."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(item.get("type") == "tool_use" for item in content)
    return False


def _is_compacted(msg: dict) -> bool:
    """Check if message is a compacted conversation."""
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                if "(compacted conversation)" in text:
                    return True
    return False


def _has_command_name(content: str) -> bool:
    """Check if system message has command-name tag."""
    return "<command-name>" in content


def _has_command_output(content: str) -> bool:
    """Check if system message has command output tag."""
    return "<local-command-stdout>" in content


def _has_bash_input(msg: dict[str, Any]) -> bool:
    """Check if user message has bash-input tag."""
    content = msg.get("message", {}).get("content", "")
    if isinstance(content, str):
        return "<bash-input>" in content
    return False


def _has_bash_output(msg: dict[str, Any]) -> bool:
    """Check if user message has bash-stdout tag."""
    content = msg.get("message", {}).get("content", "")
    if isinstance(content, str):
        return "<bash-stdout>" in content
    return False


def find_samples(data_dirs: list[Path]) -> dict[str, list[dict]]:
    """Find sample messages for each output category."""
    samples: dict[str, list[dict]] = {cat: [] for cat in OUTPUT_CATEGORIES}

    for data_dir in data_dirs:
        for jsonl_file in data_dir.rglob("*.jsonl"):
            try:
                with open(jsonl_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Check each category
                        for cat_name, cat_def in OUTPUT_CATEGORIES.items():
                            if len(samples[cat_name]) < 2:
                                try:
                                    if cat_def["filter"](msg):
                                        samples[cat_name].append(msg)
                                except Exception:
                                    pass

            except Exception as e:
                print(f"Error processing {jsonl_file}: {e}")

    return samples


def find_tool_samples(
    data_dirs: list[Path],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Find sample messages for each tool type.

    Returns:
        Tuple of (tool_use_samples, tool_result_samples)
        - tool_use_samples: assistant messages with tool_use content
        - tool_result_samples: user messages with tool_result content
    """
    tool_use_samples: dict[str, list[dict]] = {}
    tool_result_samples: dict[str, list[dict]] = {}

    # Track tool_use_id -> tool_name mapping
    tool_id_to_name: dict[str, str] = {}

    for data_dir in data_dirs:
        for jsonl_file in data_dir.rglob("*.jsonl"):
            try:
                with open(jsonl_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Collect tool_use samples from assistant messages
                        if msg.get("type") == "assistant":
                            content = msg.get("message", {}).get("content", [])
                            if isinstance(content, list):
                                for item in content:
                                    if item.get("type") == "tool_use":
                                        tool_name = item.get("name", "")
                                        tool_id = item.get("id", "")
                                        if tool_name:
                                            tool_id_to_name[tool_id] = tool_name
                                            if tool_name not in tool_use_samples:
                                                tool_use_samples[tool_name] = []
                                            if len(tool_use_samples[tool_name]) < 1:
                                                tool_use_samples[tool_name].append(msg)

                        # Collect tool_result samples from user messages
                        if msg.get("type") == "user":
                            content = msg.get("message", {}).get("content", [])
                            if isinstance(content, list):
                                for item in content:
                                    if item.get("type") == "tool_result":
                                        tool_id = item.get("tool_use_id", "")
                                        tool_name = tool_id_to_name.get(tool_id, "")
                                        if tool_name:
                                            if tool_name not in tool_result_samples:
                                                tool_result_samples[tool_name] = []
                                            if len(tool_result_samples[tool_name]) < 1:
                                                tool_result_samples[tool_name].append(
                                                    msg
                                                )

            except Exception as e:
                print(f"Error processing {jsonl_file}: {e}")

    return tool_use_samples, tool_result_samples


def write_sample(output_dir: Path, name: str, msg: dict) -> None:
    """Write a sample message as both .json and .jsonl files."""
    out_json = output_dir / f"{name}.json"
    abbreviated = abbreviate_message(msg)
    with open(out_json, "w") as f:
        json.dump(abbreviated, f, indent=2)

    out_jsonl = output_dir / f"{name}.jsonl"
    with open(out_jsonl, "w") as f:
        f.write(json.dumps(msg) + "\n")


def main():
    test_data = Path(__file__).parent.parent / "test" / "test_data"
    data_dirs = [
        test_data / "real_projects",  # Only use real_projects as instructed
    ]

    output_dir = Path(__file__).parent.parent / "dev-docs" / "messages"

    # Create subdirectories
    for subdir in ["user", "assistant", "system", "tools"]:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    print("Finding samples from real_projects...")
    samples = find_samples(data_dirs)
    tool_use_samples, tool_result_samples = find_tool_samples(data_dirs)

    # Write samples for each category to appropriate subdirectory
    for cat_name, messages in samples.items():
        if not messages:
            print(f"  {cat_name}: NO SAMPLES FOUND")
            continue

        cat_def = OUTPUT_CATEGORIES[cat_name]
        subdir = cat_def["subdir"]
        target_dir = output_dir / subdir

        write_sample(target_dir, cat_name, messages[0])
        print(f"  {subdir}/{cat_name}: wrote .json, .jsonl")

    # Write tool_use samples (assistant messages) -> tools/ToolName-tool_use
    tools_dir = output_dir / "tools"
    for tool_name, messages in sorted(tool_use_samples.items()):
        if not messages:
            continue
        write_sample(tools_dir, f"{tool_name}-tool_use", messages[0])
        print(f"  tools/{tool_name}-tool_use: wrote .json, .jsonl")

    # Write tool_result samples (user messages) -> tools/ToolName-tool_result
    for tool_name, messages in sorted(tool_result_samples.items()):
        if not messages:
            continue
        write_sample(tools_dir, f"{tool_name}-tool_result", messages[0])
        print(f"  tools/{tool_name}-tool_result: wrote .json, .jsonl")

    print(f"\nWrote samples to {output_dir}")


if __name__ == "__main__":
    main()
