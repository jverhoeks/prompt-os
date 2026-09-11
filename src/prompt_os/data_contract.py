from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
import uuid

from jsonschema import Draft202012Validator


APP_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class ContractCandidate:
    id: str
    app_id: str
    contract: dict[str, Any]
    rationale: str
    evidence: tuple[str, ...]
    created_at: str
    path: Path


class DataContractRepository:
    """Versioned storage for generated data contracts.

    This class validates and versions generated artifacts. It never decides which
    business collections or fields an application should have.
    """

    def __init__(self, root: Path, schema_path: Path) -> None:
        self.root = root
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(schema)

    def current(self, app_id: str) -> dict[str, Any] | None:
        path = self._app_root(app_id) / "current.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def propose(
        self,
        app_id: str,
        contract: Mapping[str, Any],
        *,
        rationale: str,
        evidence: list[str],
    ) -> ContractCandidate:
        if not rationale.strip():
            raise ValueError("candidate rationale is required")
        if not evidence:
            raise ValueError("candidate evidence is required")
        value = dict(contract)
        errors = sorted(self._validator.iter_errors(value), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "$"
            raise ValueError(f"invalid data contract at {location}: {first.message}")
        current = self.current(app_id)
        expected_base = current["version"] if current else None
        if value["based_on"] != expected_base:
            raise ValueError(f"based_on must be {expected_base!r}")
        if current and self._semver(value["version"]) <= self._semver(current["version"]):
            raise ValueError("candidate version must be newer than the current contract")
        for collection in value["collections"].values():
            for name, field in collection["fields"].items():
                if field["required"] and len(field["evidence"]) < 3:
                    raise ValueError(
                        f"required field {name!r} needs at least three supporting examples"
                    )
        candidate_id = f"{value['version']}-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(UTC).isoformat()
        candidate_root = self._app_root(app_id) / "candidates" / candidate_id
        candidate_root.mkdir(parents=True, exist_ok=False)
        (candidate_root / "contract.json").write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        metadata = {
            "id": candidate_id,
            "app_id": app_id,
            "rationale": rationale.strip(),
            "evidence": evidence,
            "created_at": created_at,
            "status": "candidate",
        }
        (candidate_root / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return ContractCandidate(
            id=candidate_id,
            app_id=app_id,
            contract=value,
            rationale=metadata["rationale"],
            evidence=tuple(evidence),
            created_at=created_at,
            path=candidate_root,
        )

    def promote(self, app_id: str, candidate_id: str, *, replay_passed: bool) -> dict[str, Any]:
        """Promote an operator-approved candidate after replay.

        This method is intentionally not registered as a live MCP tool.
        """
        if not replay_passed:
            raise ValueError("candidate replay must pass before promotion")
        app_root = self._app_root(app_id)
        candidate_root = app_root / "candidates" / candidate_id
        candidate_path = candidate_root / "contract.json"
        if not candidate_path.is_file():
            raise KeyError(candidate_id)
        contract = json.loads(candidate_path.read_text(encoding="utf-8"))
        current = self.current(app_id)
        expected_base = current["version"] if current else None
        if contract["based_on"] != expected_base:
            raise ValueError("candidate is stale")
        versions = app_root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        version_path = versions / f"{contract['version']}.json"
        if version_path.exists():
            raise ValueError("contract version already exists")
        shutil.copyfile(candidate_path, version_path)
        shutil.copyfile(candidate_path, app_root / "current.json")
        metadata_path = candidate_root / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "promoted"
        metadata["promoted_at"] = datetime.now(UTC).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return contract

    def _app_root(self, app_id: str) -> Path:
        if not APP_ID.fullmatch(app_id):
            raise ValueError("invalid application id")
        path = self.root / app_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _semver(value: str) -> tuple[int, int, int]:
        return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]
