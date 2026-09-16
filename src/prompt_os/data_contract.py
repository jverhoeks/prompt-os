from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
import uuid

from jsonschema import Draft202012Validator


APP_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


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
    """Validate, replay and version generated schema-neutral contract artifacts."""

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
        available_evidence: Mapping[str, Mapping[str, Any]],
    ) -> ContractCandidate:
        if not rationale.strip():
            raise ValueError("candidate rationale is required")
        value = dict(contract)
        self._validate_contract(value)
        current = self.current(app_id)
        expected_base = current["version"] if current else None
        if value["based_on"] != expected_base:
            raise ValueError(f"based_on must be {expected_base!r}")
        if current and self._semver(value["version"]) <= self._semver(current["version"]):
            raise ValueError("candidate version must be newer than the current contract")
        self._validate_evidence(value, evidence, available_evidence)

        candidate_id = f"{value['version']}-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(UTC).isoformat()
        candidate_root = self._app_root(app_id) / "candidates" / candidate_id
        candidate_root.mkdir(parents=True, exist_ok=False)
        candidate_path = candidate_root / "contract.json"
        candidate_path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        metadata = {
            "id": candidate_id,
            "app_id": app_id,
            "rationale": rationale.strip(),
            "evidence": evidence,
            "candidate_sha256": self._hash_file(candidate_path),
            "created_at": created_at,
            "status": "candidate",
        }
        self._write_json(candidate_root / "metadata.json", metadata)
        return ContractCandidate(
            id=candidate_id,
            app_id=app_id,
            contract=value,
            rationale=metadata["rationale"],
            evidence=tuple(evidence),
            created_at=created_at,
            path=candidate_root,
        )

    def replay(
        self,
        app_id: str,
        candidate_id: str,
        *,
        available_evidence: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        candidate_root, contract, metadata = self._candidate(app_id, candidate_id)
        checks: list[dict[str, Any]] = []

        try:
            self._validate_contract(contract)
        except ValueError as exc:
            checks.append({"name": "structure", "passed": False, "detail": str(exc)})
        else:
            checks.append({"name": "structure", "passed": True})

        current = self.current(app_id)
        expected_base = current["version"] if current else None
        base_passed = contract.get("based_on") == expected_base
        checks.append(
            {
                "name": "base-version",
                "passed": base_passed,
                "expected": expected_base,
                "actual": contract.get("based_on"),
            }
        )

        try:
            self._validate_evidence(contract, metadata["evidence"], available_evidence)
        except ValueError as exc:
            checks.append({"name": "evidence", "passed": False, "detail": str(exc)})
        else:
            checks.append({"name": "evidence", "passed": True})

        candidate_path = candidate_root / "contract.json"
        replay = {
            "replay_id": str(uuid.uuid4()),
            "candidate_id": candidate_id,
            "candidate_sha256": self._hash_file(candidate_path),
            "based_on": contract.get("based_on"),
            "evidence_sha256": self._evidence_hash(metadata["evidence"], available_evidence),
            "created_at": datetime.now(UTC).isoformat(),
            "checks": checks,
            "passed": all(check["passed"] for check in checks),
        }
        self._write_json(candidate_root / "replay.json", replay)
        return replay

    def promote(
        self,
        app_id: str,
        candidate_id: str,
        *,
        replay_id: str,
        available_evidence: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Promote an operator-selected candidate using its persisted replay artifact."""
        candidate_root, contract, metadata = self._candidate(app_id, candidate_id)
        replay_path = candidate_root / "replay.json"
        if not replay_path.is_file():
            raise ValueError("candidate has no replay report")
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        candidate_path = candidate_root / "contract.json"
        if (
            replay.get("replay_id") != replay_id
            or replay.get("candidate_id") != candidate_id
            or replay.get("passed") is not True
            or replay.get("candidate_sha256") != self._hash_file(candidate_path)
            or replay.get("evidence_sha256")
            != self._evidence_hash(metadata["evidence"], available_evidence)
        ):
            raise ValueError("candidate replay is missing, stale or unsuccessful")
        current = self.current(app_id)
        expected_base = current["version"] if current else None
        if contract["based_on"] != expected_base:
            raise ValueError("candidate is stale")

        app_root = self._app_root(app_id)
        versions = app_root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        version_path = versions / f"{contract['version']}.json"
        if version_path.exists():
            if self._hash_file(version_path) != self._hash_file(candidate_path):
                raise ValueError("contract version already exists with different content")
        else:
            self._replace_from_file(candidate_path, version_path)
        current_path = app_root / "current.json"
        self._replace_from_file(candidate_path, current_path)

        metadata["status"] = "promoted"
        metadata["promoted_at"] = datetime.now(UTC).isoformat()
        metadata["replay_id"] = replay_id
        self._write_json(candidate_root / "metadata.json", metadata)
        return contract

    def candidates(self, app_id: str) -> list[dict[str, Any]]:
        candidates_root = self._app_root(app_id) / "candidates"
        if not candidates_root.is_dir():
            return []
        values = []
        for metadata_path in candidates_root.glob("*/metadata.json"):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            values.append(metadata)
        return sorted(values, key=lambda value: (value["created_at"], value["id"]), reverse=True)

    def candidate_contract(self, app_id: str, candidate_id: str) -> dict[str, Any]:
        return self._candidate(app_id, candidate_id)[1]

    def _candidate(
        self, app_id: str, candidate_id: str
    ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        if not candidate_id or Path(candidate_id).name != candidate_id:
            raise KeyError(candidate_id)
        candidate_root = self._app_root(app_id) / "candidates" / candidate_id
        candidate_path = candidate_root / "contract.json"
        metadata_path = candidate_root / "metadata.json"
        if not candidate_path.is_file() or not metadata_path.is_file():
            raise KeyError(candidate_id)
        contract = json.loads(candidate_path.read_text(encoding="utf-8"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("id") != candidate_id or metadata.get("app_id") != app_id:
            raise ValueError("candidate metadata does not match its location")
        if metadata.get("status") != "candidate":
            raise ValueError("candidate is not available for promotion")
        if metadata.get("candidate_sha256") != self._hash_file(candidate_path):
            raise ValueError("candidate content changed after proposal")
        return candidate_root, contract, metadata

    def _validate_contract(self, value: Mapping[str, Any]) -> None:
        errors = sorted(self._validator.iter_errors(value), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "$"
            raise ValueError(f"invalid data contract at {location}: {first.message}")

    @staticmethod
    def _validate_evidence(
        contract: Mapping[str, Any],
        evidence: list[str],
        available: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("candidate evidence must contain unique evidence identifiers")
        unknown = sorted(set(evidence) - set(available))
        if unknown:
            raise ValueError(f"unknown evidence identifiers: {', '.join(unknown)}")
        declared = set(evidence)
        for collection in contract["collections"].values():
            for name, field in collection["fields"].items():
                field_evidence = field["evidence"]
                if not set(field_evidence) <= declared:
                    raise ValueError(f"field {name!r} uses undeclared evidence")
                if field["required"]:
                    if len(set(field_evidence)) < 3:
                        raise ValueError(
                            f"required field {name!r} needs at least three supporting examples"
                        )
                    document_ids: set[str] = set()
                    for evidence_id in field_evidence:
                        record = available[evidence_id]
                        document = record.get("document")
                        if record.get("kind") != "document" or not isinstance(document, dict):
                            raise ValueError(
                                f"required field {name!r} needs document evidence"
                            )
                        document_ids.add(str(record.get("document_id", evidence_id)))
                        if name not in document:
                            raise ValueError(
                                f"evidence {evidence_id!r} does not contain field {name!r}"
                            )
                        if not DataContractRepository._matches_type(
                            field["type"], document[name]
                        ):
                            raise ValueError(
                                f"evidence {evidence_id!r} has the wrong type for field {name!r}"
                            )
                    if len(document_ids) < 3:
                        raise ValueError(
                            f"required field {name!r} needs three distinct documents"
                        )

    @staticmethod
    def _evidence_hash(
        evidence: list[str], available: Mapping[str, Mapping[str, Any]]
    ) -> str:
        material = {key: available.get(key) for key in sorted(evidence)}
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _matches_type(expected: str, value: Any) -> bool:
        if expected in {"string", "datetime", "reference"}:
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "decimal":
            return isinstance(value, str) and DECIMAL.fullmatch(value) is not None
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "list":
            return isinstance(value, list)
        return False

    def _app_root(self, app_id: str) -> Path:
        if not APP_ID.fullmatch(app_id):
            raise ValueError("invalid application id")
        path = self.root / app_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            stream.write(encoded)
            temporary = Path(stream.name)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_from_file(source: Path, target: Path) -> None:
        with NamedTemporaryFile(
            "wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            stream.write(source.read_bytes())
            temporary = Path(stream.name)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _semver(value: str) -> tuple[int, int, int]:
        return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]
