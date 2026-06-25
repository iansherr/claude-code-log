"""OpenCode session provider."""

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
    make_user_entry,
)


class OpenCodeProvider(BaseProvider):
    def get_provider_name(self) -> str:
        return "opencode"

    def get_session_format(self) -> str:
        return "json"

    def get_data_dir(self) -> Optional[Path]:
        data_dir = Path.home() / ".local" / "share" / "opencode" / "storage"
        return data_dir if data_dir.exists() else None

    def discover_sessions(self) -> Iterator[SessionInfo]:
        data_dir = self.get_data_dir()
        if data_dir is None:
            return

        session_dir = data_dir / "session"
        if not session_dir.exists():
            return

        for project_dir in session_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for session_file in project_dir.glob("*.json"):
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        raw: Any = json.load(f)
                    if not isinstance(raw, dict):
                        continue
                    sd = cast(dict[str, Any], raw)

                    session_id = str(sd.get("id", session_file.stem))
                    title = str(sd.get("title", ""))
                    created_at = self._parse_timestamp(sd, "time")

                    yield SessionInfo(
                        provider="opencode",
                        session_id=session_id,
                        title=title,
                        created_at=created_at,
                        project_path=project_dir,
                    )
                except (json.JSONDecodeError, OSError):
                    continue

    def load_session(
        self, session_id: str, max_messages: Optional[int] = None
    ) -> Iterator[TranscriptEntry]:
        data_dir = self.get_data_dir()
        if data_dir is None:
            raise ValueError("OpenCode data directory not found")
        session_data = self._load_session_data(data_dir, session_id)
        if session_data is None:
            raise FileNotFoundError(f"Session {session_id} not found")
        yield from self._parse_session(data_dir, session_id, session_data, max_messages)

    def _load_session_data(
        self, data_dir: Path, session_id: str
    ) -> Optional[dict[str, Any]]:
        session_dir = data_dir / "session"
        if not session_dir.exists():
            return None
        for project_dir in session_dir.iterdir():
            if not project_dir.is_dir():
                continue
            session_file = project_dir / f"{session_id}.json"
            if session_file.exists():
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        raw: Any = json.load(f)
                    if isinstance(raw, dict):
                        return cast(dict[str, Any], raw)
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def _parse_session(
        self,
        data_dir: Path,
        session_id: str,
        session_data: dict[str, Any],
        max_messages: Optional[int] = None,
    ) -> Iterator[TranscriptEntry]:
        message_dir = data_dir / "message" / session_id
        if not message_dir.exists():
            return

        part_dir = data_dir / "part"
        counter = 0

        for message_file in sorted(message_dir.glob("*.json")):
            if max_messages is not None and counter >= max_messages:
                break

            try:
                with open(message_file, "r", encoding="utf-8") as f:
                    raw_msg: Any = json.load(f)
                if not isinstance(raw_msg, dict):
                    continue
                message_data = cast(dict[str, Any], raw_msg)
            except (json.JSONDecodeError, OSError):
                continue

            role = str(message_data.get("role", ""))
            message_id = str(message_data.get("id", message_file.stem))
            timestamp = self._parse_timestamp(message_data, "time")
            model_id = str(message_data.get("modelID", "unknown"))
            provider_id = str(message_data.get("providerID", "unknown"))
            model_name = f"{provider_id}/{model_id}"
            parts = self._load_parts(part_dir, message_id)

            if role == "user":
                text_parts: list[str] = []
                for part in parts:
                    if part.get("type") == "text":
                        text_parts.append(str(part.get("text", "")))
                if text_parts:
                    yield make_user_entry(
                        session_id,
                        f"{session_id}-{counter}",
                        timestamp,
                        "\n".join(text_parts),
                    )
                    counter += 1

            elif role == "assistant":
                for part in parts:
                    part_type = part.get("type")
                    part_text = str(part.get("text", ""))

                    if part_type == "text" and part_text:
                        yield make_assistant_entry(
                            session_id,
                            f"{session_id}-{counter}",
                            timestamp,
                            model_name,
                            part_text,
                        )
                        counter += 1

                    elif part_type == "reasoning" and part_text:
                        yield make_thinking_entry(
                            session_id,
                            f"{session_id}-{counter}",
                            timestamp,
                            model_name,
                            part_text,
                        )
                        counter += 1

                    elif part_type and part_type.startswith("tool-"):
                        tool_name = part_type[5:]
                        tool_call_id = str(
                            part.get("toolCallId", f"{session_id}-{counter}")
                        )
                        input_data_raw = part.get("input", {})
                        input_data: dict[str, Any] = (
                            cast(dict[str, Any], input_data_raw)
                            if isinstance(input_data_raw, dict)
                            else {"raw": str(input_data_raw)}
                        )

                        yield make_tool_use_entry(
                            session_id,
                            f"{session_id}-{counter}",
                            timestamp,
                            model_name,
                            tool_call_id,
                            tool_name,
                            input_data,
                        )
                        counter += 1

                        state_raw = part.get("state")
                        if state_raw == "output" or isinstance(state_raw, dict):
                            output = part.get("output", "")
                            if output:
                                yield make_tool_result_entry(
                                    session_id,
                                    f"{session_id}-{counter}",
                                    timestamp,
                                    tool_call_id,
                                    str(output),
                                )
                                counter += 1

    def _load_parts(self, part_dir: Path, message_id: str) -> list[dict[str, Any]]:
        message_part_dir = part_dir / message_id
        if not message_part_dir.exists():
            return []
        parts: list[dict[str, Any]] = []
        for part_file in sorted(message_part_dir.glob("*.json")):
            try:
                with open(part_file, "r", encoding="utf-8") as f:
                    raw: Any = json.load(f)
                if isinstance(raw, dict):
                    parts.append(cast(dict[str, Any], raw))
            except (json.JSONDecodeError, OSError):
                continue
        return parts

    def _parse_timestamp(self, data: dict[str, Any], key: str) -> str:
        from datetime import datetime

        time_raw = data.get(key)
        time_data: dict[str, Any] = (
            cast(dict[str, Any], time_raw) if isinstance(time_raw, dict) else {}
        )
        created = time_data.get("created")
        if created is not None and isinstance(created, (int, float)):
            return datetime.fromtimestamp(created / 1000).isoformat()
        return ""
