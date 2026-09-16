from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_loop import StrandsSession
from .app_pack import AppPack
from .model_client import LiteLLMConfig
from .tool_service import ToolService
from .tracing import TraceWriter, utc_now


def run_case(
    root: Path,
    config: LiteLLMConfig,
    pack: AppPack,
    case: dict[str, Any],
    *,
    database: Path,
    contract_root: Path,
    trace: TraceWriter | None = None,
) -> dict[str, Any]:
    turns = case.get("turns") or [
        {
            "message": case["message"],
            "required_tools": case.get("required_tools", []),
            "forbidden_tools": case.get("forbidden_tools", []),
        }
    ]
    turn_results: list[dict[str, Any]] = []
    try:
        with StrandsSession(
            config,
            app_id=pack.id,
            functionality=pack.functionality,
            database=database,
            contract_root=contract_root,
            contract_schema=root / "contracts" / "data-contract.schema.json",
            tool_catalog=root / "contracts" / "tool-catalog.json",
            capabilities=pack.capabilities,
            trace_path=trace.path if trace else None,
        ) as session:
            for turn in turns:
                started_at = utc_now()
                try:
                    outcome = session.send(turn["message"])
                    used = {call["name"] for call in outcome["tool_calls"]}
                    successful = {
                        call["name"]
                        for call in outcome["tool_calls"]
                        if call.get("status") != "error"
                    }
                    missing = sorted(set(turn.get("required_tools", [])) - successful)
                    forbidden = sorted(set(turn.get("forbidden_tools", [])) & used)
                    tool_errors = [
                        call["name"]
                        for call in outcome["tool_calls"]
                        if call.get("status") == "error"
                    ]
                    tool_error_details = [
                        {"name": call["name"], "result": call.get("result")}
                        for call in outcome["tool_calls"]
                        if call.get("status") == "error"
                    ]
                    passed = not missing and not forbidden and bool(outcome["reply"].strip())
                    result = {
                        "message": turn["message"],
                        "passed": passed,
                        "required_tools": turn.get("required_tools", []),
                        "used_tools": sorted(used),
                        "missing_tools": missing,
                        "forbidden_tools_used": forbidden,
                        "tool_errors": tool_errors,
                        "tool_error_details": tool_error_details,
                    }
                    if trace:
                        trace.write(
                            app_id=pack.id,
                            model=config.model,
                            user_message=turn["message"],
                            started_at=started_at,
                            outcome="passed" if passed else "failed",
                            reply=outcome["reply"],
                            tool_calls=outcome["tool_calls"],
                            error=None if passed else _turn_detail(result),
                        )
                except Exception as exc:
                    result = {
                        "message": turn["message"],
                        "passed": False,
                        "required_tools": turn.get("required_tools", []),
                        "used_tools": [],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    if trace:
                        trace.write(
                            app_id=pack.id,
                            model=config.model,
                            user_message=turn["message"],
                            started_at=started_at,
                            outcome="error",
                            error=result["error"],
                        )
                turn_results.append(result)
                if not result["passed"]:
                    break
    except Exception as exc:
        if not turn_results:
            turn_results.append(
                {
                    "message": turns[0]["message"],
                    "passed": False,
                    "required_tools": turns[0].get("required_tools", []),
                    "used_tools": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    inspector = ToolService(
        app_id=pack.id,
        database=database,
        contract_root=contract_root,
        contract_schema=root / "contracts" / "data-contract.schema.json",
        timezone="UTC",
    )
    try:
        document_count = sum(inspector.store.describe().values())
        revision_count = inspector.store.revision_count()
    finally:
        inspector.close()
    candidate_count = len(
        list((contract_root / pack.id / "candidates").glob("*/contract.json"))
    )
    expected_state = _expected_state(case)
    actual_state = {
        "documents": document_count,
        "revisions": revision_count,
        "candidates": candidate_count,
    }
    state_checks = {
        name: {
            "expected": expected_state[name],
            "actual": actual_state[name],
            "passed": actual_state[name] == expected_state[name],
        }
        for name in expected_state
    }
    passed = (
        len(turn_results) == len(turns)
        and all(result["passed"] for result in turn_results)
        and all(check["passed"] for check in state_checks.values())
    )
    return {
        "id": case["id"],
        "passed": passed,
        "turns": turn_results,
        "document_count": document_count,
        "revision_count": revision_count,
        "candidate_count": candidate_count,
        "state_checks": state_checks,
    }


def case_detail(result: dict[str, Any]) -> str:
    if result["passed"]:
        return "ok"
    failed_turns = [
        _turn_detail(turn) for turn in result["turns"] if not turn["passed"]
    ]
    failed_state = {
        name: {"expected": check["expected"], "actual": check["actual"]}
        for name, check in result["state_checks"].items()
        if not check["passed"]
    }
    return "; ".join(failed_turns + ([f"failed_state={failed_state}"] if failed_state else []))


def _turn_detail(turn: dict[str, Any]) -> str:
    if turn.get("error"):
        return turn["error"]
    return (
        f"missing_tools={turn.get('missing_tools', [])}, "
        f"forbidden_tools={turn.get('forbidden_tools_used', [])}, "
        f"tool_errors={turn.get('tool_errors', [])}, "
        f"details={turn.get('tool_error_details', [])}"
    )


def _expected_state(case: dict[str, Any]) -> dict[str, int]:
    expected = case.get("expected_state")
    names = {"documents", "revisions", "candidates"}
    if (
        not isinstance(expected, dict)
        or set(expected) != names
        or any(
            not isinstance(expected[name], int)
            or isinstance(expected[name], bool)
            or expected[name] < 0
            for name in names
        )
    ):
        raise ValueError(
            "case expected_state must contain non-negative integer counts for "
            "documents, revisions and candidates"
        )
    return expected
