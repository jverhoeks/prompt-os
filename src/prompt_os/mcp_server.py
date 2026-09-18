from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .calculate import evaluate, sample
from .data_contract import GeneratedDataContract
from .tool_service import ToolService
from .units import convert
from .view_description import ViewDescription


def _contract_value(contract: GeneratedDataContract) -> dict[str, Any]:
    value = contract.model_dump(exclude_none=True)
    value["based_on"] = contract.based_on
    return value


def create_server(service: ToolService, *, debug: bool = False) -> FastMCP:
    server = FastMCP(
        "Prompt OS fundamentals",
        instructions="Schema-neutral storage, clock, aggregation and generated-contract lifecycle.",
        log_level="DEBUG" if debug else "ERROR",
    )

    @server.tool(name="system.now")
    def system_now() -> dict[str, Any]:
        """Return the current instant, local date and configured timezone."""
        return service.now()

    @server.tool(name="store.put")
    def store_put(
        document: dict[str, Any], collection: str | None = None, document_id: str | None = None
    ) -> dict[str, Any]:
        """Create or update a JSON document.

        Omit collection before a data contract is promoted. Supply the existing
        document_id for corrections so immutable revision history stays connected.
        Store precision-sensitive decimal input as a JSON string.
        """
        return service.put(document, collection=collection, document_id=document_id)

    @server.tool(name="store.get")
    def store_get(document_id: str) -> dict[str, Any]:
        """Read one current, non-archived document by identifier."""
        return service.get(document_id)

    @server.tool(name="store.query")
    def store_query(
        collection: str | None = None,
        where: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Find current documents using exact matches on dotted JSON field paths.

        Call without arguments to read recently changed documents for review or
        structure discovery.
        """
        return service.query(collection=collection, where=where, limit=limit)

    @server.tool(name="store.search")
    def store_search(
        query: str, collection: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Find relevant documents using deterministic case-insensitive term matching."""
        return service.search(query, collection=collection, limit=limit)

    @server.tool(name="store.archive")
    def store_archive(document_id: str, reason: str) -> dict[str, Any]:
        """Archive one document with a required reason while preserving its history."""
        return service.archive(document_id, reason)

    @server.tool(name="store.history")
    def store_history(document_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return the append-only revision history for one document."""
        return service.history(document_id, limit=limit)

    @server.tool(name="store.describe")
    def store_describe() -> dict[str, Any]:
        """Return current collection counts and the promoted data contract."""
        return service.describe()

    @server.tool(name="store.aggregate")
    def store_aggregate(
        operation: str,
        collection: str | None = None,
        field: str | None = None,
        group_by: str | None = None,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Calculate an exact count or decimal sum over every matching current document."""
        return service.aggregate(
            operation, collection=collection, field=field, group_by=group_by, where=where
        )

    @server.tool(name="contract.current")
    def contract_current() -> dict[str, Any]:
        """Read the current explicitly promoted data contract, if one exists."""
        return {"contract": service.contracts.current(service.app_id)}

    @server.tool(name="contract.evidence")
    def contract_evidence(limit: int = 100) -> dict[str, Any]:
        """List grounded functionality, immutable document and trace evidence identifiers."""
        return {"evidence": service.listed_contract_evidence(limit=limit)}

    @server.tool(name="contract.propose")
    def contract_propose(
        contract: GeneratedDataContract, rationale: str, evidence: list[str]
    ) -> dict[str, Any]:
        """Propose, but do not promote, a generated application data contract.

        Base the proposal on the business functionality, observed inbox records,
        corrections or failed queries. Use version 0.1.0 and based_on null for the
        first proposal. A required field needs at least three supporting examples;
        keep less-supported fields optional. Obtain valid immutable evidence identifiers
        from contract.evidence and use only those identifiers. Prefer the smallest useful
        structure.
        """
        return service.propose_contract(
            _contract_value(contract), rationale=rationale, evidence=evidence
        )

    @server.tool(name="math.evaluate")
    def math_evaluate(expression: str) -> dict[str, str]:
        """Evaluate bounded arithmetic using deterministic decimal operations."""
        return evaluate(expression)

    @server.tool(name="math.sample")
    def math_sample(
        expression: str,
        start: str,
        end: str,
        points: int = 240,
        variable: str = "x",
    ) -> dict[str, Any]:
        """Sample a univariate expression over a closed interval for a continuous plot."""
        return sample(expression, start=start, end=end, points=points, variable=variable)

    @server.tool(name="unit.convert")
    def unit_convert(
        value: str | int | float, from_unit: str, to_unit: str
    ) -> dict[str, Any]:
        """Convert compatible length, mass, volume, area or temperature units."""
        return convert(value, from_unit, to_unit)

    @server.tool(name="view.present")
    def view_present(view: ViewDescription) -> dict[str, Any]:
        """Present results using generic UI blocks when structure aids understanding."""
        return {"accepted": True, "blocks": len(view.blocks)}

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-os-mcp")
    parser.add_argument("--app", required=True)
    parser.add_argument("--database", type=Path, default=Path("var/prompt-os.sqlite"))
    parser.add_argument("--contracts", type=Path, default=Path("var/data-contracts"))
    parser.add_argument("--functionality-sha256")
    parser.add_argument("--traces", type=Path)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    service = ToolService(
        app_id=args.app,
        database=args.database,
        contract_root=args.contracts,
        timezone=args.timezone,
        functionality_sha256=args.functionality_sha256,
        trace_path=args.traces,
    )
    try:
        create_server(service, debug=args.debug).run(transport="stdio")
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
