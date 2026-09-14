from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .app_pack import discover_app_packs
from .model_client import LiteLLMConfig, check_model
from .replay import case_detail, run_case
from .tracing import TraceWriter


def collect_evaluation(
    root: Path, *, selected_app: str | None = None, suite: str = "smoke"
) -> dict[str, object]:
    if suite not in {"smoke", "contracts", "all"}:
        raise ValueError("suite must be smoke, contracts or all")
    config = LiteLLMConfig.from_environment()
    check_model(config)
    packs = {pack.id: pack for pack in discover_app_packs(root / "apps")}
    paths = {
        "smoke": [root / "eval" / "cases.json"],
        "contracts": [root / "eval" / "contract_cases.json"],
        "all": [root / "eval" / "cases.json", root / "eval" / "contract_cases.json"],
    }[suite]
    cases = [
        case
        for path in paths
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]
    ]
    if selected_app:
        cases = [case for case in cases if case["app"] == selected_app]
        if not cases:
            raise ValueError(f"no evaluation cases for {selected_app!r}")
    results: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="prompt-os-eval-") as directory:
        temp = Path(directory)
        for case in cases:
            database = temp / f"{case['id']}.sqlite"
            contract_root = temp / "contracts" / case["id"]
            trace = TraceWriter(temp / "traces" / f"{case['app']}.jsonl")
            result = run_case(
                root,
                config,
                packs[case["app"]],
                case,
                database=database,
                contract_root=contract_root,
                trace=trace,
            )
            results.append(
                {
                    "id": case["id"],
                    "passed": result["passed"],
                    "detail": case_detail(result),
                    "result": result,
                }
            )
    passed = sum(1 for item in results if item["passed"])
    return {
        "suite": suite,
        "app": selected_app,
        "passed": passed,
        "total": len(results),
        "cases": results,
    }


def run_evaluation(
    root: Path, *, selected_app: str | None = None, suite: str = "smoke"
) -> int:
    report = collect_evaluation(root, selected_app=selected_app, suite=suite)
    for case in report["cases"]:
        print(
            f"{'PASS' if case['passed'] else 'FAIL'} {case['id']}: {case['detail']}"
        )
    print(f"{report['passed']}/{report['total']} cases passed")
    return 1 if report["passed"] != report["total"] else 0
