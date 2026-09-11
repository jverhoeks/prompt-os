from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Mapping


COLLECTION = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


@dataclass(frozen=True)
class Document:
    app_id: str
    id: str
    collection: str
    value: dict[str, Any]
    created_at: str
    updated_at: str
    archived_at: str | None
    archive_reason: str | None


class DocumentStore:
    """Schema-neutral storage for JSON documents."""

    def __init__(self, database: str | Path = ":memory:", *, app_id: str = "default") -> None:
        if not re.fullmatch(r"^[a-z][a-z0-9-]{0,62}$", app_id):
            raise ValueError("invalid application id")
        self.app_id = app_id
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS docs (
                app_id TEXT NOT NULL,
                id TEXT NOT NULL,
                collection TEXT NOT NULL,
                value_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                archive_reason TEXT,
                PRIMARY KEY (app_id, id)
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def put(
        self,
        collection: str,
        value: Mapping[str, Any],
        document_id: str | None = None,
    ) -> Document:
        if not COLLECTION.fullmatch(collection):
            raise ValueError("invalid collection name")
        encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        now = datetime.now(UTC).isoformat()
        resolved_id = document_id or str(uuid.uuid4())
        existing = self._connection.execute(
            "SELECT created_at FROM docs WHERE app_id = ? AND id = ?",
            (self.app_id, resolved_id),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        self._connection.execute(
            """
            INSERT INTO docs (app_id, id, collection, value_json, created_at, updated_at, archived_at, archive_reason)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(app_id, id) DO UPDATE SET
                collection = excluded.collection,
                value_json = excluded.value_json,
                updated_at = excluded.updated_at,
                archived_at = NULL,
                archive_reason = NULL
            """,
            (self.app_id, resolved_id, collection, encoded, created_at, now),
        )
        self._connection.commit()
        return self.get(resolved_id, include_archived=True)

    def get(self, document_id: str, *, include_archived: bool = False) -> Document:
        condition = "" if include_archived else " AND archived_at IS NULL"
        row = self._connection.execute(
            f"SELECT * FROM docs WHERE app_id = ? AND id = ?{condition}",
            (self.app_id, document_id),
        ).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._from_row(row)

    def query(
        self,
        *,
        collection: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[Document]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if collection is not None and not COLLECTION.fullmatch(collection):
            raise ValueError("invalid collection name")
        sql = "SELECT * FROM docs WHERE app_id = ? AND archived_at IS NULL"
        params: list[Any] = [self.app_id]
        if collection is not None:
            sql += " AND collection = ?"
            params.append(collection)
        for field, expected in (where or {}).items():
            if not isinstance(field, str) or not field or any(part == "" for part in field.split(".")):
                raise ValueError("filter fields must be non-empty dotted paths")
            path = "$." + ".".join(part.replace('"', '\\"') for part in field.split("."))
            sql += " AND json_extract(value_json, ?) = ?"
            params.extend((path, expected))
        sql += " ORDER BY updated_at DESC, id ASC LIMIT ?"
        params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def scan(self, *, limit: int = 100) -> list[Document]:
        return self.query(limit=limit)

    def describe(self) -> dict[str, int]:
        rows = self._connection.execute(
            """
            SELECT collection, COUNT(*) AS count
            FROM docs
            WHERE app_id = ? AND archived_at IS NULL
            GROUP BY collection
            ORDER BY collection
            """,
            (self.app_id,),
        ).fetchall()
        return {row["collection"]: row["count"] for row in rows}

    def aggregate(
        self,
        operation: str,
        *,
        collection: str | None = None,
        field: str | None = None,
        group_by: str | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"count", "sum"}:
            raise ValueError("operation must be count or sum")
        if operation == "sum" and not field:
            raise ValueError("sum requires a field")
        documents = self.query(collection=collection, where=where, limit=500)
        groups: dict[str, int | Decimal] = {}
        for document in documents:
            group_value = self._field(document.value, group_by) if group_by else "all"
            key = json.dumps(group_value, ensure_ascii=False, sort_keys=True)
            if operation == "count":
                if field is None or self._field(document.value, field, missing=None) is not None:
                    groups[key] = int(groups.get(key, 0)) + 1
                continue
            value = self._field(document.value, field or "")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"field {field!r} contains a non-numeric value")
            groups[key] = Decimal(groups.get(key, Decimal(0))) + Decimal(str(value))
        return {
            "operation": operation,
            "field": field,
            "group_by": group_by,
            "groups": [
                {
                    "key": json.loads(key),
                    "value": str(value) if isinstance(value, Decimal) else value,
                }
                for key, value in sorted(groups.items())
            ],
        }

    def archive(self, document_id: str, reason: str) -> Document:
        if not reason.strip():
            raise ValueError("archive reason is required")
        now = datetime.now(UTC).isoformat()
        cursor = self._connection.execute(
            "UPDATE docs SET archived_at = ?, archive_reason = ?, updated_at = ? "
            "WHERE app_id = ? AND id = ? AND archived_at IS NULL",
            (now, reason.strip(), now, self.app_id, document_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(document_id)
        self._connection.commit()
        return self.get(document_id, include_archived=True)

    @staticmethod
    def _field(value: Mapping[str, Any], path: str, *, missing: Any = ...) -> Any:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                if missing is ...:
                    raise ValueError(f"field {path!r} is missing")
                return missing
            current = current[part]
        return current

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Document:
        return Document(
            app_id=row["app_id"],
            id=row["id"],
            collection=row["collection"],
            value=json.loads(row["value_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
            archive_reason=row["archive_reason"],
        )
