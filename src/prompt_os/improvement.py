from __future__ import annotations

from datetime import UTC, datetime
from difflib import unified_diff
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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

SEMANTIC_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


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
        "source_version": pack.version,
        "source_functionality_sha256": hashlib.sha256(
            (pack.functionality.rstrip() + "\n").encode("utf-8")
        ).hexdigest(),
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


def format_improvement_proposal(pack: AppPack, result: dict[str, Any]) -> str:
    if result["status"] == "no-change":
        return "\n".join(
            (
                "No change proposed.",
                f"Summary: {result['summary']}",
                f"Reason: {result['rationale']}",
            )
        )

    candidate_root = Path(result["path"])
    revised = (candidate_root / "FUNCTIONALITY.md").read_text(encoding="utf-8")
    baseline = result["baseline"]
    candidate = result["candidate"]
    replay = (
        f"Replay: current {baseline['passed']}/{baseline['total']}; "
        f"candidate {candidate['passed']}/{candidate['total']}"
    )
    if result["recommended"]:
        replay += " (measured improvement)"
    else:
        replay += " (no measured improvement)"
    diff = "".join(
        unified_diff(
            pack.functionality.splitlines(keepends=True),
            revised.splitlines(keepends=True),
            fromfile=f"{pack.id} {pack.version} (current)",
            tofile=f"{result['candidate_id']} (candidate)",
        )
    ).rstrip()
    return "\n\n".join(
        (
            "Improvement proposal",
            f"Summary: {result['summary']}\nReason: {result['rationale']}\n{replay}",
            diff or "The candidate has no textual changes.",
            f"Candidate saved at {candidate_root}",
        )
    )


def promote_improvement(root: Path, pack: AppPack, result: dict[str, Any]) -> dict[str, Any]:
    """Promote a replayed candidate and retain an immutable snapshot of production."""
    candidate_id = str(result.get("candidate_id", ""))
    candidate_root = root / "var" / "improvements" / pack.id / candidate_id
    if not candidate_id or Path(result.get("path", "")).resolve() != candidate_root.resolve():
        raise ValueError("invalid improvement candidate path")

    report_path = candidate_root / "report.json"
    candidate_path = candidate_root / "FUNCTIONALITY.md"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report.get("candidate_id") != candidate_id
        or report.get("app_id") != pack.id
        or report.get("status") != "candidate"
    ):
        raise ValueError("improvement candidate is not available for promotion")
    if not _has_completed_replay(report):
        raise ValueError("improvement candidate has not completed replay")

    current = AppPack.load(pack.path)
    if current.version != pack.version or current.functionality != pack.functionality:
        raise ValueError("production application changed after the candidate was created")
    current_hash = hashlib.sha256(
        (current.functionality.rstrip() + "\n").encode("utf-8")
    ).hexdigest()
    if (
        report.get("source_version") != current.version
        or report.get("source_functionality_sha256") != current_hash
    ):
        raise ValueError("production application changed after the candidate was created")
    next_version = _bump_patch(current.version)
    revised = candidate_path.read_text(encoding="utf-8").rstrip() + "\n"
    manifest_path = current.path / "app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    functionality_path = current.path / manifest["functionality"]
    new_manifest = manifest | {"version": next_version}

    # Validate the exact candidate and manifest together before touching production.
    with TemporaryDirectory(prefix="prompt-os-promote-") as directory:
        validation_root = Path(directory) / current.id
        validation_root.mkdir()
        (validation_root / "app.json").write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (validation_root / manifest["functionality"]).write_text(revised, encoding="utf-8")
        AppPack.load(validation_root)

    archive_root = current.path / "versions" / current.version
    if archive_root.exists():
        raise ValueError(f"archive already exists for version {current.version}")
    versions_root = archive_root.parent
    versions_root.mkdir(parents=True, exist_ok=True)
    archive_temp = versions_root / f".{current.version}.{uuid.uuid4().hex}.tmp"
    archive_temp.mkdir()
    try:
        shutil.copy2(manifest_path, archive_temp / "app.json")
        shutil.copy2(functionality_path, archive_temp / manifest["functionality"])
        os.replace(archive_temp, archive_root)
    except BaseException:
        shutil.rmtree(archive_temp, ignore_errors=True)
        raise

    functionality_temp = current.path / f".{manifest['functionality']}.{uuid.uuid4().hex}.tmp"
    manifest_temp = current.path / f".app.json.{uuid.uuid4().hex}.tmp"
    try:
        functionality_temp.write_text(revised, encoding="utf-8")
        manifest_temp.write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(functionality_temp, functionality_path)
        try:
            os.replace(manifest_temp, manifest_path)
        except BaseException:
            shutil.copy2(archive_root / manifest["functionality"], functionality_path)
            raise
    finally:
        functionality_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)

    promoted_at = datetime.now(UTC).isoformat()
    promotion = {
        "status": "promoted",
        "promoted_at": promoted_at,
        "from_version": current.version,
        "to_version": next_version,
        "archive_path": str(archive_root),
    }
    promoted_report = report | promotion
    report_path.write_text(
        json.dumps(promoted_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (archive_root / "promotion.json").write_text(
        json.dumps(
            {
                "candidate_id": candidate_id,
                "promoted_at": promoted_at,
                "from_version": current.version,
                "to_version": next_version,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return promoted_report


def _bump_patch(version: str) -> str:
    match = SEMANTIC_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid semantic version {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _has_completed_replay(report: dict[str, Any]) -> bool:
    for name in ("baseline", "candidate"):
        score = report.get(name)
        if not isinstance(score, dict):
            return False
        if not isinstance(score.get("passed"), int) or not isinstance(score.get("total"), int):
            return False
        if score["passed"] < 0 or score["total"] < 1 or score["passed"] > score["total"]:
            return False
    return report["baseline"]["total"] == report["candidate"]["total"]


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
                tool_catalog=root / "contracts" / "tool-catalog.json",
                capabilities=pack.capabilities,
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
