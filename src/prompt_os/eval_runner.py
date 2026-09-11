from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .app_pack import discover_app_packs
from .model_client import LiteLLMConfig, check_model
from .replay import case_detail, run_case
from .tracing import TraceWriter


def run_evaluation(
    root: Path, *, selected_app: str | None = None, suite: str = "smoke"
) -> int:
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
    failures = 0
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
            passed = result["passed"]
            detail = case_detail(result)
            failures += int(not passed)
            print(f"{'PASS' if passed else 'FAIL'} {case['id']}: {detail}")
    print(f"{len(cases) - failures}/{len(cases)} cases passed")
    return 1 if failures else 0
