from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .tool_service import ToolService


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class GeneratedField(BaseModel):
    description: str
    type: Literal["string", "integer", "number", "boolean", "datetime", "reference", "list"]
    required: bool
    references: str | None = None
    evidence: list[str] = Field(min_length=1)


class GeneratedCollection(BaseModel):
    description: str
    identity: list[str]
    fields: dict[str, GeneratedField]


class GeneratedDataContract(BaseModel):
    version: str
    based_on: str | None
    summary: str
    collections: dict[str, GeneratedCollection]
    open_questions: list[str]


def create_server(service: ToolService, *, log_level: LogLevel = "ERROR") -> FastMCP:
    server = FastMCP(
        "Prompt OS fundamentals",
        instructions="Schema-neutral storage, clock, aggregation and generated-contract lifecycle.",
        log_level=log_level,
    )

    @server.tool(name="system.now")
    def system_now() -> dict[str, Any]:
        return service.now()

    @server.tool(name="store.put")
    def store_put(
        document: dict[str, Any], collection: str | None = None, document_id: str | None = None
    ) -> dict[str, Any]:
        return service.put(document, collection=collection, document_id=document_id)

    @server.tool(name="store.get")
    def store_get(document_id: str) -> dict[str, Any]:
        return service.get(document_id)

    @server.tool(name="store.query")
    def store_query(
        collection: str | None = None,
        where: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return service.query(collection=collection, where=where, limit=limit)

    @server.tool(name="store.scan")
    def store_scan(limit: int = 100) -> list[dict[str, Any]]:
        return service.scan(limit=limit)

    @server.tool(name="store.archive")
    def store_archive(document_id: str, reason: str) -> dict[str, Any]:
        return service.archive(document_id, reason)

    @server.tool(name="store.describe")
    def store_describe() -> dict[str, Any]:
        return service.describe()

    @server.tool(name="store.aggregate")
    def store_aggregate(
        operation: str,
        collection: str | None = None,
        field: str | None = None,
        group_by: str | None = None,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.aggregate(
            operation, collection=collection, field=field, group_by=group_by, where=where
        )

    @server.tool(name="contract.current")
    def contract_current() -> dict[str, Any]:
        return {"contract": service.contracts.current(service.app_id)}

    @server.tool(name="contract.propose")
    def contract_propose(
        contract: GeneratedDataContract, rationale: str, evidence: list[str]
    ) -> dict[str, Any]:
        """Propose, but do not promote, a generated application data contract.

        Base the proposal on the business functionality, observed inbox records,
        corrections or failed queries. Use version 0.1.0 and based_on null for the
        first proposal. A required field needs at least three supporting examples;
        keep less-supported fields optional. Prefer the smallest useful structure.
        """
        return service.propose_contract(
            contract.model_dump(exclude_none=True), rationale=rationale, evidence=evidence
        )

    @server.tool(name="math.evaluate")
    def math_evaluate(expression: str) -> dict[str, str]:
        return service.calculate(expression)

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-os-mcp")
    parser.add_argument("--app", required=True)
    parser.add_argument("--database", type=Path, default=Path("var/prompt-os.sqlite"))
    parser.add_argument("--contracts", type=Path, default=Path("var/data-contracts"))
    parser.add_argument("--contract-schema", type=Path, default=Path("contracts/data-contract.schema.json"))
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="ERROR",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    service = ToolService(
        app_id=args.app,
        database=args.database,
        contract_root=args.contracts,
        contract_schema=args.contract_schema,
        timezone=args.timezone,
    )
    try:
        create_server(service, log_level=args.log_level).run(transport="stdio")
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
