from __future__ import annotations

import json
from pathlib import Path


def tool_blocks(path: Path) -> dict[str, str]:
    """Map each fundamental tool name to the capability block that exposes it."""
    tools = json.loads(path.read_text(encoding="utf-8"))["tools"]
    return {tool["name"]: tool["block"] for tool in tools}


def allowed_tool_names(path: Path, capabilities: tuple[str, ...]) -> set[str]:
    blocks = tool_blocks(path)
    missing = sorted(set(capabilities) - set(blocks.values()))
    if missing:
        raise ValueError(f"capabilities have no tools: {', '.join(missing)}")
    allowed_blocks = set(capabilities) | {"data-contract", "presentation"}
    return {name for name, block in blocks.items() if block in allowed_blocks}
