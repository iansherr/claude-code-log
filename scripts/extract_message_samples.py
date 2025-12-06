#!/usr/bin/env python3
"""Extract abbreviated sample messages for documentation.

This script finds examples of each message type and content type from
real session data and creates abbreviated JSON files for documentation.
"""

import json
from pathlib import Path
from typing import Any

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
    for key in ["type", "sessionId", "timestamp", "uuid", "parentUuid", "isSidechain"]:
        if key in msg:
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


def find_samples(
    data_dirs: list[Path],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Find sample messages of each type."""
    samples: dict[str, list[dict]] = {
        "user_text": [],
        "user_tool_result": [],
        "user_image": [],
        "assistant_text": [],
        "assistant_tool_use": [],
        "assistant_thinking": [],
        "system": [],
        "summary": [],
        "queue_operation": [],
        "file_history_snapshot": [],
    }

    tool_samples: dict[str, list[dict]] = {}

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

                        msg_type = msg.get("type", "")

                        if msg_type == "user":
                            content = msg.get("message", {}).get("content", [])
                            if isinstance(content, str):
                                if len(samples["user_text"]) < 2:
                                    samples["user_text"].append(msg)
                            elif isinstance(content, list):
                                for item in content:
                                    item_type = item.get("type", "")
                                    if (
                                        item_type == "text"
                                        and len(samples["user_text"]) < 2
                                    ):
                                        samples["user_text"].append(msg)
                                    elif item_type == "tool_result":
                                        if len(samples["user_tool_result"]) < 2:
                                            samples["user_tool_result"].append(msg)
                                    elif (
                                        item_type == "image"
                                        and len(samples["user_image"]) < 2
                                    ):
                                        samples["user_image"].append(msg)

                        elif msg_type == "assistant":
                            content = msg.get("message", {}).get("content", [])
                            if isinstance(content, list):
                                has_text = has_thinking = has_tool = False
                                for item in content:
                                    item_type = item.get("type", "")
                                    if item_type == "text":
                                        has_text = True
                                    elif item_type == "thinking":
                                        has_thinking = True
                                    elif item_type == "tool_use":
                                        has_tool = True
                                        tool_name = item.get("name", "")
                                        if tool_name and tool_name not in tool_samples:
                                            tool_samples[tool_name] = []
                                        if (
                                            tool_name
                                            and len(tool_samples[tool_name]) < 1
                                        ):
                                            tool_samples[tool_name].append(msg)

                                if has_text and len(samples["assistant_text"]) < 2:
                                    samples["assistant_text"].append(msg)
                                if (
                                    has_thinking
                                    and len(samples["assistant_thinking"]) < 2
                                ):
                                    samples["assistant_thinking"].append(msg)
                                if has_tool and len(samples["assistant_tool_use"]) < 2:
                                    samples["assistant_tool_use"].append(msg)

                        elif msg_type == "system":
                            if len(samples["system"]) < 2:
                                samples["system"].append(msg)

                        elif msg_type == "summary":
                            if len(samples["summary"]) < 2:
                                samples["summary"].append(msg)

                        elif msg_type == "queue-operation":
                            if len(samples["queue_operation"]) < 2:
                                samples["queue_operation"].append(msg)

                        elif msg_type == "file-history-snapshot":
                            if len(samples["file_history_snapshot"]) < 1:
                                samples["file_history_snapshot"].append(msg)

            except Exception as e:
                print(f"Error processing {jsonl_file}: {e}")

    return samples, tool_samples


def main():
    test_data = Path(__file__).parent.parent / "test" / "test_data"
    data_dirs = [
        test_data / "sessions",
        test_data / "real_projects",
    ]

    output_dir = Path(__file__).parent.parent / "dev-docs" / "messages"
    output_dir.mkdir(parents=True, exist_ok=True)

    samples, tool_samples = find_samples(data_dirs)

    # Write samples
    for sample_type, messages in samples.items():
        if not messages:
            continue
        out_file = output_dir / f"{sample_type}.json"
        abbreviated = [abbreviate_message(m) for m in messages[:1]]
        with open(out_file, "w") as f:
            json.dump(
                abbreviated[0] if len(abbreviated) == 1 else abbreviated, f, indent=2
            )
        print(f"Wrote {out_file}")

    # Write tool samples
    tools_dir = output_dir / "tools"
    tools_dir.mkdir(exist_ok=True)
    for tool_name, messages in sorted(tool_samples.items()):
        if not messages:
            continue
        out_file = tools_dir / f"{tool_name.lower()}.json"
        abbreviated = abbreviate_message(messages[0])
        with open(out_file, "w") as f:
            json.dump(abbreviated, f, indent=2)
        print(f"Wrote {out_file}")


if __name__ == "__main__":
    main()
