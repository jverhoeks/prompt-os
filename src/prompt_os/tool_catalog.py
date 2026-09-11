from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class ToolRecord:
    name: str
    block: str
    purpose: str
    mutates: bool


def load_tool_catalog(path: Path) -> tuple[ToolRecord, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValueError(f"{path}: unsupported tool catalog format")
    values = payload.get("tools")
    if not isinstance(values, list):
        raise ValueError(f"{path}: tools must be a list")
    records: list[ToolRecord] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"{path}: tool {index} must be an object")
        if set(value) != {"name", "block", "purpose", "mutates"}:
            raise ValueError(f"{path}: tool {index} has invalid fields")
        if any(
            not isinstance(value[field], str) or not value[field]
            for field in ("name", "block", "purpose")
        ) or not isinstance(value["mutates"], bool):
            raise ValueError(f"{path}: tool {index} has invalid values")
        records.append(ToolRecord(**value))
    names = [record.name for record in records]
    if len(names) != len(set(names)):
        raise ValueError(f"{path}: duplicate tool name")
    return tuple(records)


def allowed_tool_names(path: Path, capabilities: tuple[str, ...]) -> set[str]:
    records = load_tool_catalog(path)
    blocks = {record.block for record in records}
    missing = sorted(set(capabilities) - blocks)
    if missing:
        raise ValueError(f"capabilities have no tools: {', '.join(missing)}")
    allowed_blocks = set(capabilities) | {"data-contract", "presentation"}
    return {record.name for record in records if record.block in allowed_blocks}
