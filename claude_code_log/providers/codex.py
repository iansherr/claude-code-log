"""Codex CLI session provider."""

import json
from pathlib import Path
from typing import Any, Iterator, Optional, cast

from claude_code_log.models import TranscriptEntry

from .base import (
    BaseProvider,
    SessionInfo,
    make_assistant_entry,
    make_thinking_entry,
    make_tool_result_entry,
    make_tool_use_entry,
)


class CodexProvider(BaseProvider):
    def get_provider_name(self) -> str:
        return "codex"

    def get_session_format(self) -> str:
        return "jsonl"

    def get_data_dir(self) -> Optional[Path]:
        data_dir = Path.home() / ".codex" / "sessions"
        return data_dir if data_dir.exists() else None

    def discover_sessions(self) -> Iterator[SessionInfo]:
        data_dir = self.get_data_dir()
        if data_dir is None:
            return

        for year_dir in data_dir.iterdir():
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir() or not month_dir.name.isdigit():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir() or not day_dir.name.isdigit():
                        continue
                    for rollout_file in day_dir.glob("rollout-*.jsonl"):
                        yield SessionInfo(
                            provider="codex",
                            session_id=rollout_file.stem,
                            created_at=f"{year_dir.name}-{month_dir.name}-{day_dir.name}",
                        )

    def load_session(
        self, session_id: str, max_messages: Optional[int] = None
    ) -> Iterator[TranscriptEntry]:
        data_dir = self.get_data_dir()
        if data_dir is None:
            raise ValueError("Codex data directory not found")
        rollout_file = self._find_session_file(data_dir, session_id)
        if rollout_file is None:
            raise FileNotFoundError(f"Session {session_id} not found")
        yield from self._parse_rollout_file(rollout_file, max_messages)

    def _find_session_file(self, data_dir: Path, session_id: str) -> Optional[Path]:
        for year_dir in data_dir.iterdir():
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir() or not month_dir.name.isdigit():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir() or not day_dir.name.isdigit():
                        continue
                    exact = day_dir / f"{session_id}.jsonl"
                    if exact.exists():
                        return exact
                    for f in day_dir.glob("rollout-*.jsonl"):
                        if session_id in f.stem:
                            return f
        return None

    def _parse_rollout_file(
        self, rollout_file: Path, max_messages: Optional[int] = None
    ) -> Iterator[TranscriptEntry]:
        session_id = rollout_file.stem
        counter = 0

        with open(rollout_file, "r", encoding="utf-8") as f:
            for line in f:
                if max_messages is not None and counter >= max_messages:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    raw: Any = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(raw, dict):
                    continue
                entry = cast(dict[str, Any], raw)
                if "timestamp" not in entry or "type" not in entry:
                    continue

                entry_type = entry.get("type")
                payload_raw = entry.get("payload", {})
                payload: dict[str, Any] = (
                    cast(dict[str, Any], payload_raw)
                    if isinstance(payload_raw, dict)
                    else {}
                )
                timestamp = str(entry.get("timestamp", ""))
                uuid = f"{session_id}-{counter}"

                if entry_type == "response_item":
                    yield from self._parse_response_item(
                        payload, session_id, timestamp, uuid
                    )
                elif entry_type == "event_msg":
                    yield from self._parse_event_msg(
                        payload, session_id, timestamp, uuid
                    )

                counter += 1

    def _parse_response_item(
        self,
        payload: dict[str, Any],
        session_id: str,
        timestamp: str,
        uuid: str,
    ) -> Iterator[TranscriptEntry]:
        payload_type = payload.get("type")

        if payload_type == "message":
            role = payload.get("role")
            content_raw = payload.get("content", [])
            content: list[Any] = (
                cast(list[Any], content_raw) if isinstance(content_raw, list) else []
            )

            if role in ("assistant", "developer"):
                text_parts: list[str] = []
                for item in content:
                    item_dict = (
                        cast(dict[str, Any], item) if isinstance(item, dict) else None
                    )
                    if item_dict is not None and item_dict.get("type") == "output_text":
                        text_parts.append(str(item_dict.get("text", "")))

                if text_parts:
                    joined = "\n".join(text_parts)
                    if role == "developer":
                        yield make_thinking_entry(
                            session_id, uuid, timestamp, "codex", joined
                        )
                    else:
                        yield make_assistant_entry(
                            session_id, uuid, timestamp, "codex", joined
                        )

        elif payload_type == "function_call":
            name = str(payload.get("name", "unknown"))
            arguments_str = str(payload.get("arguments", "{}"))
            call_id = str(payload.get("call_id", uuid))

            try:
                arguments_raw: Any = json.loads(arguments_str) if arguments_str else {}
                arguments: dict[str, Any] = (
                    cast(dict[str, Any], arguments_raw)
                    if isinstance(arguments_raw, dict)
                    else {"raw": arguments_str}
                )
            except json.JSONDecodeError:
                arguments = {"raw": arguments_str}

            yield make_tool_use_entry(
                session_id, uuid, timestamp, "codex", call_id, name, arguments
            )

        elif payload_type == "function_call_output":
            call_id_out = str(payload.get("call_id", uuid))
            output = str(payload.get("output", ""))
            yield make_tool_result_entry(
                session_id, uuid, timestamp, call_id_out, output
            )

    def _parse_event_msg(
        self,
        payload: dict[str, Any],
        session_id: str,
        timestamp: str,
        uuid: str,
    ) -> Iterator[TranscriptEntry]:
        payload_type = payload.get("type")

        if payload_type == "agent_message":
            message = str(payload.get("message", ""))
            if message:
                yield make_thinking_entry(session_id, uuid, timestamp, "codex", message)
