from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .app_pack import AppPack
from .data_contract import DataContractRepository, matches_type
from .store import DocumentStore


def database_path(root: Path) -> Path:
    return root / "var" / "prompt-os.sqlite"


def contract_root(root: Path) -> Path:
    return root / "var" / "data-contracts"


class ToolService:
    """Transport-neutral implementation of the fundamental app tools."""

    def __init__(
        self,
        *,
        app_id: str,
        database: str | Path,
        contract_root: Path,
        timezone: str,
        functionality_sha256: str | None = None,
        trace_path: Path | None = None,
    ) -> None:
        self.app_id = app_id
        self.timezone = ZoneInfo(timezone)
        self.store = DocumentStore(database, app_id=app_id)
        self.contracts = DataContractRepository(contract_root)
        self.functionality_sha256 = functionality_sha256
        self.trace_path = trace_path

    @classmethod
    def for_pack(cls, root: Path, pack: AppPack, *, timezone: str = "UTC") -> "ToolService":
        database_path(root).parent.mkdir(parents=True, exist_ok=True)
        return cls(
            app_id=pack.id,
            database=database_path(root),
            contract_root=contract_root(root),
            timezone=timezone,
            functionality_sha256=pack.functionality_sha256,
            trace_path=pack.trace_path(root),
        )

    def close(self) -> None:
        self.store.close()

    def now(self) -> dict[str, Any]:
        value = datetime.now(self.timezone)
        return {
            "iso": value.isoformat(),
            "local_date": value.date().isoformat(),
            "timezone": str(self.timezone),
        }

    def put(
        self,
        document: Mapping[str, Any],
        *,
        collection: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        contract = self.contracts.current(self.app_id)
        target = collection or "inbox"
        if contract is None:
            if target != "inbox":
                raise ValueError("no promoted contract; records must enter inbox")
        else:
            if collection is None:
                raise ValueError("collection is required after a contract is promoted")
            self._validate_document(contract, target, document)
        return self.store.put(target, document, document_id).payload()

    def get(self, document_id: str) -> dict[str, Any]:
        return self.store.get(document_id).payload()

    def query(
        self,
        *,
        collection: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            doc.payload()
            for doc in self.store.query(collection=collection, where=where, limit=limit)
        ]

    def search(
        self, query: str, *, collection: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return [
            doc.payload()
            for doc in self.store.search(query, collection=collection, limit=limit)
        ]

    def archive(self, document_id: str, reason: str) -> dict[str, Any]:
        return self.store.archive(document_id, reason).payload()

    def history(self, document_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return [item.payload() for item in self.store.history(document_id, limit=limit)]

    def describe(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "contract": self.contracts.current(self.app_id),
            "document_counts": self.store.describe(),
        }

    def aggregate(self, operation: str, **arguments: Any) -> dict[str, Any]:
        return self.store.aggregate(operation, **arguments)

    def propose_contract(
        self,
        contract: Mapping[str, Any],
        *,
        rationale: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        candidate = self.contracts.propose(
            self.app_id,
            contract,
            rationale=rationale,
            evidence=evidence,
            available_evidence=self.contract_evidence(),
        )
        return {
            "candidate_id": candidate.id,
            "version": candidate.contract["version"],
            "based_on": candidate.contract["based_on"],
            "status": "candidate",
        }

    def contract_evidence(self) -> dict[str, dict[str, Any]]:
        available = self.store.evidence_documents()
        if self.functionality_sha256:
            evidence_id = f"functionality:{self.functionality_sha256}"
            available[evidence_id] = {
                "kind": "functionality",
                "sha256": self.functionality_sha256,
            }
        if self.trace_path and self.trace_path.is_file():
            for line in self.trace_path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                record = json.loads(line)
                if record.get("app_id") == self.app_id and record.get("trace_id"):
                    available[f"trace:{record['trace_id']}"] = {
                        "kind": "trace",
                        "trace_id": record["trace_id"],
                        "sha256": hashlib.sha256(
                            json.dumps(
                                record,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                        "outcome": record.get("outcome"),
                        "tool_names": [
                            call.get("name")
                            for call in record.get("tool_calls", [])
                            if isinstance(call, dict) and call.get("name")
                        ],
                    }
        return available

    def replay_contract(self, candidate_id: str) -> dict[str, Any]:
        return self.contracts.replay(
            self.app_id,
            candidate_id,
            available_evidence=self.contract_evidence(),
        )

    def listed_contract_evidence(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("evidence limit must be between 1 and 500")
        available = self.contract_evidence()
        functionality = [
            (evidence_id, value)
            for evidence_id, value in available.items()
            if value.get("kind") == "functionality"
        ]
        other = [
            (evidence_id, value)
            for evidence_id, value in available.items()
            if value.get("kind") != "functionality"
        ]
        capacity = max(0, limit - len(functionality))
        selected = functionality + (other[-capacity:] if capacity else [])
        return [{"id": evidence_id, **value} for evidence_id, value in selected]

    def promote_contract(self, candidate_id: str, replay_id: str) -> dict[str, Any]:
        return self.contracts.promote(
            self.app_id,
            candidate_id,
            replay_id=replay_id,
            available_evidence=self.contract_evidence(),
        )

    @staticmethod
    def _validate_document(
        contract: Mapping[str, Any], collection: str, document: Mapping[str, Any]
    ) -> None:
        collections = contract["collections"]
        if collection not in collections:
            raise ValueError(f"collection {collection!r} is not in the promoted contract")
        fields = collections[collection]["fields"]
        unknown = sorted(set(document) - set(fields))
        if unknown:
            raise ValueError(f"unknown fields: {', '.join(unknown)}")
        missing = sorted(
            name
            for name, definition in fields.items()
            if definition["required"] and name not in document
        )
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        for name, value in document.items():
            expected = fields[name]["type"]
            if not matches_type(expected, value):
                raise ValueError(f"field {name!r} must be {expected}")
