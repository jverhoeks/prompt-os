from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .agent_loop import run_turn
from .app_pack import discover_app_packs
from .model_client import LiteLLMConfig, check_model
from .tool_service import ToolService
from .tracing import TraceWriter, utc_now


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
            started_at = utc_now()
            try:
                outcome = run_turn(
                    config,
                    app_id=case["app"],
                    functionality=packs[case["app"]].functionality,
                    user_message=case["message"],
                    database=database,
                    contract_root=contract_root,
                    contract_schema=root / "contracts" / "data-contract.schema.json",
                )
                used = {call["name"] for call in outcome["tool_calls"]}
                missing = sorted(set(case["required_tools"]) - used)
                inspector = ToolService(
                    app_id=case["app"],
                    database=database,
                    contract_root=contract_root,
                    contract_schema=root / "contracts" / "data-contract.schema.json",
                    timezone="UTC",
                )
                count = sum(inspector.store.describe().values())
                inspector.close()
                candidate_count = len(list((contract_root / case["app"] / "candidates").glob("*/contract.json")))
                passed = (
                    not missing
                    and count >= case.get("minimum_documents", 0)
                    and candidate_count >= case.get("minimum_candidates", 0)
                    and bool(outcome["reply"].strip())
                )
                detail = (
                    "ok" if passed else
                    f"missing_tools={missing}, documents={count}, candidates={candidate_count}"
                )
                trace.write(
                    app_id=case["app"],
                    model=config.model,
                    user_message=case["message"],
                    started_at=started_at,
                    outcome="passed" if passed else "failed",
                    reply=outcome["reply"],
                    tool_calls=outcome["tool_calls"],
                    error=None if passed else detail,
                )
            except Exception as exc:
                passed = False
                detail = f"{type(exc).__name__}: {exc}"
                trace.write(
                    app_id=case["app"],
                    model=config.model,
                    user_message=case["message"],
                    started_at=started_at,
                    outcome="error",
                    error=detail,
                )
            failures += int(not passed)
            print(f"{'PASS' if passed else 'FAIL'} {case['id']}: {detail}")
    print(f"{len(cases) - failures}/{len(cases)} cases passed")
    return 1 if failures else 0
