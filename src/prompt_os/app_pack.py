from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from .tool_catalog import tool_blocks


APP_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
DATA_POLICIES = {"persistent", "persistent-sensitive", "optional-history"}
TRACE_MODE_BY_DATA_POLICY: dict[str, Literal["full", "metadata", "off"]] = {
    "persistent": "full",
    "persistent-sensitive": "metadata",
    "optional-history": "off",
}
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

    @property
    def trace_mode(self) -> Literal["full", "metadata", "off"]:
        return TRACE_MODE_BY_DATA_POLICY[self.data_policy]

    @property
    def functionality_sha256(self) -> str:
        return hashlib.sha256((self.functionality.rstrip() + "\n").encode("utf-8")).hexdigest()

    def trace_path(self, root: Path) -> Path:
        return root / "var" / "traces" / f"{self.id}.jsonl"

    @classmethod
    def load(cls, path: Path) -> "AppPack":
        manifest_path = path / "app.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError(f"{manifest_path}: manifest must be an object")
        required = {"id", "name", "version", "functionality", "capabilities", "data_policy"}
        missing = sorted(required - manifest.keys())
        if missing:
            raise ValueError(f"{manifest_path}: missing {', '.join(missing)}")
        extra = sorted(manifest.keys() - required)
        if extra:
            raise ValueError(f"{manifest_path}: unknown fields {', '.join(extra)}")
        for name in ("id", "name", "version", "functionality", "data_policy"):
            if not isinstance(manifest[name], str) or not manifest[name].strip():
                raise ValueError(f"{manifest_path}: {name} must be a non-empty string")
        if not APP_ID.fullmatch(manifest["id"]):
            raise ValueError(f"{manifest_path}: invalid application id")
        if manifest["id"] != path.name:
            raise ValueError(f"{manifest_path}: id must match its directory")
        if not SEMVER.fullmatch(manifest["version"]):
            raise ValueError(f"{manifest_path}: version must be semantic")
        capabilities = manifest["capabilities"]
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(
                not isinstance(item, str) or not CAPABILITY.fullmatch(item)
                for item in capabilities
            )
        ):
            raise ValueError(f"{manifest_path}: capabilities must be a non-empty list of names")
        if len(capabilities) != len(set(capabilities)):
            raise ValueError(f"{manifest_path}: capabilities must be unique")
        if manifest["data_policy"] not in DATA_POLICIES:
            raise ValueError(f"{manifest_path}: unsupported data policy")
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
            capabilities=tuple(capabilities),
            data_policy=manifest["data_policy"],
            functionality=functionality,
            path=path,
        )


def discover_app_packs(root: Path, *, tool_catalog: Path | None = None) -> list[AppPack]:
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    names = index.get("applications")
    if not isinstance(names, list) or not names:
        raise ValueError(f"{index_path}: applications must be a non-empty list")
    if any(not isinstance(name, str) or not APP_ID.fullmatch(name) for name in names):
        raise ValueError(f"{index_path}: applications must contain valid application ids")
    if len(names) != len(set(names)):
        raise ValueError(f"{index_path}: duplicate application id")
    packs = [AppPack.load(root / name) for name in names]
    catalog_path = tool_catalog or root.parent / "contracts" / "tool-catalog.json"
    if catalog_path.is_file():
        blocks = set(tool_blocks(catalog_path).values())
        for pack in packs:
            missing_blocks = sorted(set(pack.capabilities) - blocks)
            if missing_blocks:
                raise ValueError(
                    f"{pack.path / 'app.json'}: capabilities have no tools: "
                    + ", ".join(missing_blocks)
                )
    return packs
