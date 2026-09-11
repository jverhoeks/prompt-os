from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal, Mapping
import uuid


class TraceWriter:
    """Append one self-contained JSON record per application turn."""

    def __init__(
        self,
        path: Path,
        *,
        mode: Literal["full", "metadata", "off"] = "full",
        session_id: str | None = None,
    ) -> None:
        self.path = path
        self.mode = mode
        self.session_id = session_id or str(uuid.uuid4())
        self._turn_index = 0

    def write(
        self,
        *,
        app_id: str,
        model: str,
        user_message: str,
        started_at: str,
        outcome: str,
        reply: str = "",
        tool_calls: list[Mapping[str, Any]] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        self._turn_index += 1
        record: dict[str, Any] = {
            "trace_id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "turn_index": self._turn_index,
            "app_id": app_id,
            "model": model,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "outcome": outcome,
        }
        if self.mode == "full":
            record.update(
                user_message=user_message,
                tool_calls=list(tool_calls or []),
                reply=reply,
                error=error,
            )
        elif self.mode == "metadata":
            record["tool_calls"] = [
                {
                    key: call[key]
                    for key in ("id", "name", "status")
                    if key in call
                }
                for call in (tool_calls or [])
            ]
            record["content_redacted"] = True
        elif self.mode != "off":
            raise ValueError(f"unsupported trace mode {self.mode!r}")
        if self.mode == "off":
            return record | {"stored": False}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
