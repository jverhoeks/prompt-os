from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
import uuid


COLLECTION = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
SEARCH_TERM = re.compile(r"\w+", re.UNICODE)


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
    revision: int


@dataclass(frozen=True)
class DocumentRevision:
    app_id: str
    id: str
    revision: int
    event: str
    collection: str
    value: dict[str, Any]
    recorded_at: str
    archived_at: str | None
    archive_reason: str | None


class DocumentStore:
    """Application-scoped current JSON documents with an append-only revision log."""

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
                revision INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (app_id, id)
            )
            """
        )
        columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(docs)")
        }
        if "revision" not in columns:
            self._connection.execute(
                "ALTER TABLE docs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_revisions (
                app_id TEXT NOT NULL,
                id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event TEXT NOT NULL,
                collection TEXT NOT NULL,
                value_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                archived_at TEXT,
                archive_reason TEXT,
                PRIMARY KEY (app_id, id, revision)
            )
            """
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO document_revisions (
                app_id, id, revision, event, collection, value_json,
                recorded_at, archived_at, archive_reason
            )
            SELECT app_id, id, revision, 'imported', collection, value_json,
                   updated_at, archived_at, archive_reason
            FROM docs
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS docs_app_collection_updated "
            "ON docs(app_id, collection, updated_at DESC)"
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
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        now = datetime.now(UTC).isoformat()
        resolved_id = document_id or str(uuid.uuid4())
        if not isinstance(resolved_id, str) or not resolved_id.strip() or len(resolved_id) > 200:
            raise ValueError("document id must be a non-empty string of at most 200 characters")
        existing = self._connection.execute(
            "SELECT created_at, archived_at, revision FROM docs WHERE app_id = ? AND id = ?",
            (self.app_id, resolved_id),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        revision = int(existing["revision"]) + 1 if existing else 1
        if existing and existing["archived_at"]:
            event = "restored"
        elif existing:
            event = "updated"
        else:
            event = "created"
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO docs (
                    app_id, id, collection, value_json, created_at, updated_at,
                    archived_at, archive_reason, revision
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(app_id, id) DO UPDATE SET
                    collection = excluded.collection,
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at,
                    archived_at = NULL,
                    archive_reason = NULL,
                    revision = excluded.revision
                """,
                (
                    self.app_id,
                    resolved_id,
                    collection,
                    encoded,
                    created_at,
                    now,
                    revision,
                ),
            )
            self._record_revision(
                document_id=resolved_id,
                revision=revision,
                event=event,
                collection=collection,
                value_json=encoded,
                recorded_at=now,
                archived_at=None,
                archive_reason=None,
            )
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
        return self._select(collection=collection, where=where, limit=limit)

    def scan(self, *, limit: int = 100) -> list[Document]:
        return self.query(limit=limit)

    def search(
        self, query: str, *, collection: str | None = None, limit: int = 20
    ) -> list[Document]:
        if not 1 <= limit <= 100:
            raise ValueError("search limit must be between 1 and 100")
        terms = tuple(dict.fromkeys(term.casefold() for term in SEARCH_TERM.findall(query)))
        if not terms:
            raise ValueError("search query must contain a word or number")
        scored: list[tuple[int, Document]] = []
        for document in self._select(collection=collection, limit=None):
            haystack = (
                document.collection + " " + json.dumps(document.value, ensure_ascii=False)
            ).casefold()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((score, document))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [document for _, document in scored[:limit]]

    def history(self, document_id: str, *, limit: int = 100) -> list[DocumentRevision]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._connection.execute(
            """
            SELECT * FROM document_revisions
            WHERE app_id = ? AND id = ?
            ORDER BY revision DESC
            LIMIT ?
            """,
            (self.app_id, document_id, limit),
        ).fetchall()
        if not rows:
            raise KeyError(document_id)
        return [self._revision_from_row(row) for row in reversed(rows)]

    def evidence_documents(self) -> dict[str, dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT id, revision, value_json FROM document_revisions
            WHERE app_id = ?
            ORDER BY id, revision
            """,
            (self.app_id,),
        ).fetchall()
        return {
            f"document:{row['id']}@{row['revision']}": {
                "kind": "document",
                "document_id": row["id"],
                "revision": row["revision"],
                "document": json.loads(row["value_json"]),
            }
            for row in rows
        }

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

    def revision_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM document_revisions WHERE app_id = ?",
            (self.app_id,),
        ).fetchone()
        return int(row["count"])

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
        documents = self._select(collection=collection, where=where, limit=None)
        groups: dict[str, int | Decimal] = {}
        for document in documents:
            group_value = self._field(document.value, group_by) if group_by else "all"
            key = json.dumps(group_value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if operation == "count":
                if field is None or self._field(document.value, field, missing=None) is not None:
                    groups[key] = int(groups.get(key, 0)) + 1
                continue
            value = self._decimal(self._field(document.value, field or ""), field or "")
            groups[key] = Decimal(groups.get(key, Decimal(0))) + value
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
        current = self._connection.execute(
            "SELECT * FROM docs WHERE app_id = ? AND id = ? AND archived_at IS NULL",
            (self.app_id, document_id),
        ).fetchone()
        if current is None:
            raise KeyError(document_id)
        now = datetime.now(UTC).isoformat()
        revision = int(current["revision"]) + 1
        with self._connection:
            self._connection.execute(
                """
                UPDATE docs
                SET archived_at = ?, archive_reason = ?, updated_at = ?, revision = ?
                WHERE app_id = ? AND id = ? AND archived_at IS NULL
                """,
                (now, reason.strip(), now, revision, self.app_id, document_id),
            )
            self._record_revision(
                document_id=document_id,
                revision=revision,
                event="archived",
                collection=current["collection"],
                value_json=current["value_json"],
                recorded_at=now,
                archived_at=now,
                archive_reason=reason.strip(),
            )
        return self.get(document_id, include_archived=True)

    def _select(
        self,
        *,
        collection: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int | None,
    ) -> list[Document]:
        if collection is not None and not COLLECTION.fullmatch(collection):
            raise ValueError("invalid collection name")
        for field in (where or {}):
            if not isinstance(field, str) or not field or any(
                part == "" for part in field.split(".")
            ):
                raise ValueError("filter fields must be non-empty dotted paths")
        sql = "SELECT * FROM docs WHERE app_id = ? AND archived_at IS NULL"
        params: list[Any] = [self.app_id]
        if collection is not None:
            sql += " AND collection = ?"
            params.append(collection)
        sql += " ORDER BY updated_at DESC, id ASC"
        documents = (self._from_row(row) for row in self._connection.execute(sql, params))
        sentinel = object()
        matched = [
            document
            for document in documents
            if all(
                self._field(document.value, field, missing=sentinel) == expected
                for field, expected in (where or {}).items()
            )
        ]
        return matched if limit is None else matched[:limit]

    def _record_revision(
        self,
        *,
        document_id: str,
        revision: int,
        event: str,
        collection: str,
        value_json: str,
        recorded_at: str,
        archived_at: str | None,
        archive_reason: str | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO document_revisions (
                app_id, id, revision, event, collection, value_json,
                recorded_at, archived_at, archive_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.app_id,
                document_id,
                revision,
                event,
                collection,
                value_json,
                recorded_at,
                archived_at,
                archive_reason,
            ),
        )

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"field {field!r} contains a non-numeric value")
        if isinstance(value, str) and not DECIMAL.fullmatch(value):
            raise ValueError(f"field {field!r} contains a non-numeric value")
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"field {field!r} contains a non-numeric value") from exc
        if not result.is_finite():
            raise ValueError(f"field {field!r} contains a non-finite value")
        return result

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
            revision=row["revision"],
        )

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> DocumentRevision:
        return DocumentRevision(
            app_id=row["app_id"],
            id=row["id"],
            revision=row["revision"],
            event=row["event"],
            collection=row["collection"],
            value=json.loads(row["value_json"]),
            recorded_at=row["recorded_at"],
            archived_at=row["archived_at"],
            archive_reason=row["archive_reason"],
        )
