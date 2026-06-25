"""Gemini CLI session provider."""

import json
from pathlib import Path
from typing import Any, Iterator, Optional, cast

from claude_code_log.models import TranscriptEntry

from .base import (
    BaseProvider,
    SessionInfo,
    extract_text,
    file_mtime_iso,
    make_assistant_entry,
    make_thinking_entry,
    make_tool_result_entry,
    make_tool_use_entry,
    make_user_entry,
)


class GeminiProvider(BaseProvider):
    def get_provider_name(self) -> str:
        return "gemini"

    def get_session_format(self) -> str:
        return "jsonl"

    def get_data_dir(self) -> Optional[Path]:
        data_dir = Path.home() / ".gemini" / "tmp"
        return data_dir if data_dir.exists() else None

    def discover_sessions(self) -> Iterator[SessionInfo]:
        data_dir = self.get_data_dir()
        if data_dir is None:
            return

        for project_dir in data_dir.iterdir():
            if not project_dir.is_dir():
                continue
            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue
            for session_file in chats_dir.glob("session-*.jsonl"):
                yield SessionInfo(
                    provider="gemini",
                    session_id=session_file.stem,
                    project_path=project_dir,
                    created_at=file_mtime_iso(session_file),
                )

    def load_session(
        self, session_id: str, max_messages: Optional[int] = None
    ) -> Iterator[TranscriptEntry]:
        data_dir = self.get_data_dir()
        if data_dir is None:
            raise ValueError("Gemini data directory not found")
        session_file = self._find_session_file(data_dir, session_id)
        if session_file is None:
            raise FileNotFoundError(f"Session {session_id} not found")
        yield from self._parse_session_file(session_file, max_messages)

    def _find_session_file(self, data_dir: Path, session_id: str) -> Optional[Path]:
        for project_dir in data_dir.iterdir():
            if not project_dir.is_dir():
                continue
            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue
            session_file = chats_dir / f"{session_id}.jsonl"
            if session_file.exists():
                return session_file
        return None

    def _parse_session_file(
        self, session_file: Path, max_messages: Optional[int] = None
    ) -> Iterator[TranscriptEntry]:
        session_id = session_file.stem
        counter = 0

        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                if max_messages is not None and counter >= max_messages:
                    break
                counter += 1
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

                if "$rewindTo" in entry or "$set" in entry:
                    continue
                if (
                    "sessionId" in entry
                    and "projectHash" in entry
                    and "messages" not in entry
                ):
                    continue
                if "type" not in entry or "timestamp" not in entry:
                    continue

                yield from self._parse_message(entry, session_id, counter)

    def _parse_message(
        self,
        entry: dict[str, Any],
        session_id: str,
        counter: int,
    ) -> Iterator[TranscriptEntry]:
        msg_type = entry.get("type")
        timestamp = str(entry.get("timestamp", ""))
        content_raw = entry.get("content", "")
        msg_id = str(entry.get("id", f"{session_id}-{counter}"))
        content = extract_text(content_raw)

        if msg_type == "user":
            yield make_user_entry(session_id, msg_id, timestamp, content)

        elif msg_type == "gemini":
            model = str(entry.get("model", "gemini"))

            if content:
                yield make_assistant_entry(
                    session_id, msg_id, timestamp, model, content
                )

            thoughts_raw = entry.get("thoughts", [])
            if isinstance(thoughts_raw, list):
                for thought in cast(list[Any], thoughts_raw):
                    thought_dict = (
                        cast(dict[str, Any], thought)
                        if isinstance(thought, dict)
                        else None
                    )
                    if thought_dict is None:
                        continue
                    thought_text = str(thought_dict.get("summary", ""))
                    if thought_text:
                        yield make_thinking_entry(
                            session_id,
                            f"{msg_id}-thought-{counter}",
                            str(thought_dict.get("timestamp", timestamp)),
                            model,
                            thought_text,
                        )

            tool_calls_raw = entry.get("toolCalls", [])
            if isinstance(tool_calls_raw, list):
                for tc in cast(list[Any], tool_calls_raw):
                    tc_dict = cast(dict[str, Any], tc) if isinstance(tc, dict) else None
                    if tc_dict is None:
                        continue
                    call_id = str(tc_dict.get("id", f"{msg_id}-tool-{counter}"))
                    name = str(tc_dict.get("name", "unknown"))
                    args_raw = tc_dict.get("args", {})
                    args: dict[str, Any] = (
                        cast(dict[str, Any], args_raw)
                        if isinstance(args_raw, dict)
                        else {}
                    )
                    result = tc_dict.get("result")

                    yield make_tool_use_entry(
                        session_id,
                        f"{msg_id}-tooluse-{counter}",
                        str(tc_dict.get("timestamp", timestamp)),
                        model,
                        call_id,
                        name,
                        args,
                    )

                    if result is not None:
                        yield make_tool_result_entry(
                            session_id,
                            f"{msg_id}-toolresult-{counter}",
                            str(tc_dict.get("timestamp", timestamp)),
                            call_id,
                            str(result),
                        )

        elif msg_type in ("info", "error", "warning"):
            yield make_assistant_entry(session_id, msg_id, timestamp, "gemini", content)
