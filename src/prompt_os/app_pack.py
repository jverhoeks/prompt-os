from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


APP_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
DISALLOWED_PRODUCT_LANGUAGE = (
    "you are an ai",
    "system prompt",
    "chain of thought",
    "prompt engineering",
    "call the tool",
)


@dataclass(frozen=True)
class AppPack:
    id: str
    name: str
    version: str
    capabilities: tuple[str, ...]
    data_policy: str
    functionality: str
    path: Path

    @classmethod
    def load(cls, path: Path) -> "AppPack":
        manifest_path = path / "app.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = {"id", "name", "version", "functionality", "capabilities", "data_policy"}
        missing = sorted(required - manifest.keys())
        if missing:
            raise ValueError(f"{manifest_path}: missing {', '.join(missing)}")
        if not APP_ID.fullmatch(manifest["id"]):
            raise ValueError(f"{manifest_path}: invalid application id")
        if manifest["id"] != path.name:
            raise ValueError(f"{manifest_path}: id must match its directory")
        if not SEMVER.fullmatch(manifest["version"]):
            raise ValueError(f"{manifest_path}: version must be semantic")
        functionality_path = path / manifest["functionality"]
        if functionality_path.parent != path or not functionality_path.is_file():
            raise ValueError(f"{manifest_path}: invalid functionality path")
        functionality = functionality_path.read_text(encoding="utf-8").strip()
        if not functionality:
            raise ValueError(f"{functionality_path}: functionality is empty")
        lowered = functionality.lower()
        for phrase in DISALLOWED_PRODUCT_LANGUAGE:
            if phrase in lowered:
                raise ValueError(f"{functionality_path}: contains runtime language {phrase!r}")
        return cls(
            id=manifest["id"],
            name=manifest["name"],
            version=manifest["version"],
            capabilities=tuple(manifest["capabilities"]),
            data_policy=manifest["data_policy"],
            functionality=functionality,
            path=path,
        )


def discover_app_packs(root: Path) -> list[AppPack]:
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    names = index.get("applications")
    if not isinstance(names, list) or not names:
        raise ValueError(f"{index_path}: applications must be a non-empty list")
    if len(names) != len(set(names)):
        raise ValueError(f"{index_path}: duplicate application id")
    return [AppPack.load(root / name) for name in names]

