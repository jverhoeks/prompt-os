from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .calculate import evaluate as evaluate_expression
from .calculate import sample as sample_expression
from .data_contract import DataContractRepository
from .store import DECIMAL, Document, DocumentRevision, DocumentStore
from .units import convert


class ToolService:
    """Transport-neutral implementation of the fundamental app tools."""

    def __init__(
        self,
        *,
        app_id: str,
        database: str | Path,
        contract_root: Path,
        contract_schema: Path,
        timezone: str,
        functionality_sha256: str | None = None,
        trace_path: Path | None = None,
    ) -> None:
        self.app_id = app_id
        self.timezone = ZoneInfo(timezone)
        self.store = DocumentStore(database, app_id=app_id)
        self.contracts = DataContractRepository(contract_root, contract_schema)
        self.functionality_sha256 = functionality_sha256
        self.trace_path = trace_path

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
        return self._document(self.store.put(target, document, document_id))

    def get(self, document_id: str) -> dict[str, Any]:
        return self._document(self.store.get(document_id))

    def query(
        self,
        *,
        collection: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            self._document(doc)
            for doc in self.store.query(
                collection=collection, where=where, limit=limit
            )
        ]

    def search(
        self, query: str, *, collection: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return [
            self._document(doc)
            for doc in self.store.search(query, collection=collection, limit=limit)
        ]

    def scan(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self._document(doc) for doc in self.store.scan(limit=limit)]

    def archive(self, document_id: str, reason: str) -> dict[str, Any]:
        return self._document(self.store.archive(document_id, reason))

    def history(self, document_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self._revision(item) for item in self.store.history(document_id, limit=limit)]

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

    def calculate(self, expression: str) -> dict[str, str]:
        return evaluate_expression(expression)

    def sample(
        self,
        expression: str,
        *,
        start: str | int | float,
        end: str | int | float,
        points: int = 240,
        variable: str = "x",
    ) -> dict[str, Any]:
        return sample_expression(
            expression, start=start, end=end, points=points, variable=variable
        )

    def convert(
        self, value: str | int | float, from_unit: str, to_unit: str
    ) -> dict[str, Any]:
        return convert(value, from_unit, to_unit)

    def call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        dispatch = {
            "system.now": lambda: self.now(),
            "store.put": lambda: self.put(
                arguments["document"],
                collection=arguments.get("collection"),
                document_id=arguments.get("document_id"),
            ),
            "store.get": lambda: self.get(arguments["document_id"]),
            "store.query": lambda: self.query(
                collection=arguments.get("collection"),
                where=arguments.get("where"),
                limit=arguments.get("limit", 100),
            ),
            "store.search": lambda: self.search(
                arguments["query"],
                collection=arguments.get("collection"),
                limit=arguments.get("limit", 20),
            ),
            "store.scan": lambda: self.scan(limit=arguments.get("limit", 100)),
            "store.archive": lambda: self.archive(arguments["document_id"], arguments["reason"]),
            "store.history": lambda: self.history(
                arguments["document_id"], limit=arguments.get("limit", 100)
            ),
            "store.describe": lambda: self.describe(),
            "store.aggregate": lambda: self.aggregate(
                arguments["operation"],
                collection=arguments.get("collection"),
                field=arguments.get("field"),
                group_by=arguments.get("group_by"),
                where=arguments.get("where"),
            ),
            "contract.current": lambda: {"contract": self.contracts.current(self.app_id)},
            "contract.evidence": lambda: {
                "evidence": self.listed_contract_evidence(
                    limit=arguments.get("limit", 100)
                )
            },
            "contract.propose": lambda: self.propose_contract(
                arguments["contract"],
                rationale=arguments["rationale"],
                evidence=arguments["evidence"],
            ),
            "math.evaluate": lambda: self.calculate(arguments["expression"]),
            "math.sample": lambda: self.sample(
                arguments["expression"],
                start=arguments["start"],
                end=arguments["end"],
                points=arguments.get("points", 240),
                variable=arguments.get("variable", "x"),
            ),
            "unit.convert": lambda: self.convert(
                arguments["value"], arguments["from_unit"], arguments["to_unit"]
            ),
        }
        if name not in dispatch:
            raise KeyError(f"unknown tool {name!r}")
        return dispatch[name]()

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
            if not ToolService._matches_type(expected, value):
                raise ValueError(f"field {name!r} must be {expected}")

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

    @staticmethod
    def _document(document: Document) -> dict[str, Any]:
        return {
            "id": document.id,
            "collection": document.collection,
            "document": document.value,
            "created_at": document.created_at,
            "updated_at": document.updated_at,
            "archived_at": document.archived_at,
            "archive_reason": document.archive_reason,
            "revision": document.revision,
        }

    @staticmethod
    def _revision(revision: DocumentRevision) -> dict[str, Any]:
        return {
            "id": revision.id,
            "revision": revision.revision,
            "event": revision.event,
            "collection": revision.collection,
            "document": revision.value,
            "recorded_at": revision.recorded_at,
            "archived_at": revision.archived_at,
            "archive_reason": revision.archive_reason,
        }
