from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Mapping
import uuid


class TraceWriter:
    """Append one self-contained JSON record per application turn."""

    def __init__(self, path: Path) -> None:
        self.path = path

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
        record = {
            "trace_id": str(uuid.uuid4()),
            "app_id": app_id,
            "model": model,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "outcome": outcome,
            "user_message": user_message,
            "tool_calls": list(tool_calls or []),
            "reply": reply,
            "error": error,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record


def utc_now() -> str:
    return datetime.now(UTC).isoformat()

