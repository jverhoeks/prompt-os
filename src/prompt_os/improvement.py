from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import uuid

from pydantic import BaseModel
from strands import Agent

from .agent_loop import run_turn
from .app_pack import AppPack
from .model_client import LiteLLMConfig, check_model, create_model
from .tool_service import ToolService


class ImprovementProposal(BaseModel):
    change_needed: bool
    summary: str
    rationale: str
    evidence_trace_ids: list[str]
    revised_functionality: str | None = None


IMPROVEMENT_POLICY = """Review application traces and propose at most one narrow improvement.
Treat corrections, errors and failed outcomes as evidence; do not manufacture a problem.
Write functionality as a business specification, not as AI or prompt instructions.
Preserve the existing Purpose, Services, Operating rules and Acceptance examples structure.
Return the complete revised functionality when a change is needed.
"""


def improve(root: Path, pack: AppPack, *, trace_limit: int = 20) -> dict[str, Any]:
    if trace_limit < 1:
        raise ValueError("trace limit must be positive")
    trace_path = root / "var" / "traces" / f"{pack.id}.jsonl"
    if not trace_path.is_file():
        raise ValueError(f"no traces found for {pack.id}; use the app first")
    config = LiteLLMConfig.from_environment()
    check_model(config)
    traces = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
    traces = traces[-trace_limit:]
    agent = Agent(
        model=create_model(config),
        system_prompt=IMPROVEMENT_POLICY,
        callback_handler=None,
    )
    proposal = agent.structured_output(
        ImprovementProposal,
        "Current business functionality:\n\n"
        + pack.functionality
        + "\n\nRecent traces:\n"
        + json.dumps(traces, ensure_ascii=False, indent=2),
    )
    if not proposal.change_needed:
        return {"status": "no-change", "summary": proposal.summary, "rationale": proposal.rationale}
    if not proposal.revised_functionality:
        raise ValueError("improvement proposed without revised functionality")

    baseline = _score(root, config, pack, pack.functionality)
    candidate_score = _score(root, config, pack, proposal.revised_functionality)
    candidate_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    candidate_root = root / "var" / "improvements" / pack.id / candidate_id
    candidate_root.mkdir(parents=True, exist_ok=False)
    (candidate_root / "FUNCTIONALITY.md").write_text(
        proposal.revised_functionality.rstrip() + "\n", encoding="utf-8"
    )
    report = {
        "candidate_id": candidate_id,
        "app_id": pack.id,
        "created_at": datetime.now(UTC).isoformat(),
        "summary": proposal.summary,
        "rationale": proposal.rationale,
        "evidence_trace_ids": proposal.evidence_trace_ids,
        "baseline": baseline,
        "candidate": candidate_score,
        "recommended": candidate_score["passed"] > baseline["passed"],
        "status": "candidate",
    }
    (candidate_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report | {"path": str(candidate_root)}


def _score(
    root: Path, config: LiteLLMConfig, pack: AppPack, functionality: str
) -> dict[str, int]:
    cases = json.loads((root / "eval" / "cases.json").read_text(encoding="utf-8"))["cases"]
    cases = [case for case in cases if case["app"] == pack.id]
    passed = 0
    with TemporaryDirectory(prefix="prompt-os-improve-") as directory:
        temp = Path(directory)
        for case in cases:
            database = temp / f"{case['id']}.sqlite"
            contracts = temp / "contracts"
            outcome = run_turn(
                config,
                app_id=pack.id,
                functionality=functionality,
                user_message=case["message"],
                database=database,
                contract_root=contracts,
                contract_schema=root / "contracts" / "data-contract.schema.json",
            )
            used = {call["name"] for call in outcome["tool_calls"]}
            inspector = ToolService(
                app_id=pack.id,
                database=database,
                contract_root=contracts,
                contract_schema=root / "contracts" / "data-contract.schema.json",
                timezone="UTC",
            )
            document_count = sum(inspector.store.describe().values())
            inspector.close()
            if (
                set(case["required_tools"]) <= used
                and document_count >= case.get("minimum_documents", 0)
                and bool(outcome["reply"].strip())
            ):
                passed += 1
    return {"passed": passed, "total": len(cases)}
