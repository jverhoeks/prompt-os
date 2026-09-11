from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import ast
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .data_contract import DataContractRepository
from .store import Document, DocumentStore


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
    ) -> None:
        self.app_id = app_id
        self.timezone = ZoneInfo(timezone)
        self.store = DocumentStore(database, app_id=app_id)
        self.contracts = DataContractRepository(contract_root, contract_schema)

    def close(self) -> None:
        self.store.close()

    def now(self) -> dict[str, Any]:
        value = datetime.now(self.timezone)
        return {"iso": value.isoformat(), "local_date": value.date().isoformat(), "timezone": str(self.timezone)}

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
        return [self._document(doc) for doc in self.store.query(collection=collection, where=where, limit=limit)]

    def scan(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self._document(doc) for doc in self.store.scan(limit=limit)]

    def archive(self, document_id: str, reason: str) -> dict[str, Any]:
        return self._document(self.store.archive(document_id, reason))

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
            self.app_id, contract, rationale=rationale, evidence=evidence
        )
        return {
            "candidate_id": candidate.id,
            "version": candidate.contract["version"],
            "based_on": candidate.contract["based_on"],
            "status": "candidate",
        }

    def calculate(self, expression: str) -> dict[str, str]:
        tree = ast.parse(expression, mode="eval")
        value = self._evaluate_math(tree.body)
        return {"expression": expression, "value": format(value, "f")}

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
            "store.scan": lambda: self.scan(limit=arguments.get("limit", 100)),
            "store.archive": lambda: self.archive(arguments["document_id"], arguments["reason"]),
            "store.describe": lambda: self.describe(),
            "store.aggregate": lambda: self.aggregate(
                arguments["operation"],
                collection=arguments.get("collection"),
                field=arguments.get("field"),
                group_by=arguments.get("group_by"),
                where=arguments.get("where"),
            ),
            "contract.current": lambda: {"contract": self.contracts.current(self.app_id)},
            "contract.propose": lambda: self.propose_contract(
                arguments["contract"],
                rationale=arguments["rationale"],
                evidence=arguments["evidence"],
            ),
            "math.evaluate": lambda: self.calculate(arguments["expression"]),
        }
        if name not in dispatch:
            raise KeyError(f"unknown tool {name!r}")
        return dispatch[name]()

    @staticmethod
    def _evaluate_math(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = ToolService._evaluate_math(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = ToolService._evaluate_math(node.left)
            right = ToolService._evaluate_math(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow) and right == int(right) and abs(right) <= 20:
                return left ** int(right)
        raise ValueError("expression contains an unsupported operation")

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
            name for name, definition in fields.items() if definition["required"] and name not in document
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
        }
