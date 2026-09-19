from __future__ import annotations

from dataclasses import replace
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

from .app_pack import AppPack
from .model_client import LiteLLMConfig, check_model, create_model
from .replay import run_case


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

CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def improve(root: Path, pack: AppPack, *, trace_limit: int = 20) -> dict[str, Any]:
    if trace_limit < 1:
        raise ValueError("trace limit must be positive")
    if pack.trace_mode == "off":
        raise ValueError(f"trace policy disables improvement evidence for {pack.id}")
    trace_path = root / "var" / "traces" / f"{pack.id}.jsonl"
    if not trace_path.is_file():
        raise ValueError(f"no traces found for {pack.id}; use the app first")
    config = LiteLLMConfig.from_environment()
    check_model(config)
    traces = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    traces = traces[-trace_limit:]
    if pack.trace_mode == "metadata":
        traces = [_metadata_trace(record) for record in traces]
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
    available_trace_ids = {
        record.get("trace_id") for record in traces if record.get("trace_id")
    }
    if not proposal.evidence_trace_ids or not set(
        proposal.evidence_trace_ids
    ) <= available_trace_ids:
        raise ValueError("improvement proposal must cite available trace identifiers")

    baseline = _score(root, config, pack, pack.functionality)
    candidate_score = _score(root, config, pack, proposal.revised_functionality)
    candidate_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    candidate_root = root / "var" / "improvements" / pack.id / candidate_id
    candidate_root.mkdir(parents=True, exist_ok=False)
    candidate_path = candidate_root / "FUNCTIONALITY.md"
    candidate_path.write_text(
        proposal.revised_functionality.rstrip() + "\n", encoding="utf-8"
    )
    report = {
        "candidate_id": candidate_id,
        "app_id": pack.id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_version": pack.version,
        "source_functionality_sha256": pack.functionality_sha256,
        "candidate_functionality_sha256": hashlib.sha256(
            candidate_path.read_bytes()
        ).hexdigest(),
        "summary": proposal.summary,
        "rationale": proposal.rationale,
        "evidence_trace_ids": proposal.evidence_trace_ids,
        "baseline": baseline,
        "candidate": candidate_score,
        "recommended": (
            candidate_score["passed"] > baseline["passed"]
            and _has_no_regressions(baseline, candidate_score)
        ),
        "status": "candidate",
    }
    (candidate_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report | {"path": str(candidate_root)}


def _metadata_trace(record: dict[str, Any]) -> dict[str, Any]:
    safe = {
        key: record[key]
        for key in (
            "trace_id",
            "session_id",
            "turn_index",
            "app_id",
            "model",
            "started_at",
            "completed_at",
            "outcome",
        )
        if key in record
    }
    safe["tool_calls"] = [
        {
            key: call[key]
            for key in ("id", "name", "status")
            if key in call
        }
        for call in record.get("tool_calls", [])
        if isinstance(call, dict)
    ]
    safe["content_redacted"] = True
    return safe


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
    if not _has_no_regressions(baseline, candidate):
        replay += " (regression detected)"
    elif result["recommended"]:
        replay += " (measured improvement)"
    else:
        replay += " (no measured improvement)"
    baseline_cases = {case["id"]: case["passed"] for case in baseline["cases"]}
    candidate_cases = {case["id"]: case["passed"] for case in candidate["cases"]}
    case_results = "\n".join(
        f"  {case_id}: current={'PASS' if passed else 'FAIL'}, "
        f"candidate={'PASS' if candidate_cases[case_id] else 'FAIL'}"
        for case_id, passed in baseline_cases.items()
    )
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
            f"Summary: {result['summary']}\nReason: {result['rationale']}\n"
            f"{replay}\n{case_results}",
            diff or "The candidate has no textual changes.",
            f"Candidate saved at {candidate_root}",
        )
    )


def list_improvement_candidates(root: Path, pack: AppPack) -> list[dict[str, Any]]:
    directory = root / "var" / "improvements" / pack.id
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for candidate_root in sorted(directory.iterdir(), reverse=True):
        report_path = candidate_root / "report.json"
        if not candidate_root.is_dir() or not report_path.is_file():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(report, dict):
            continue
        records.append(
            {
                "candidate_id": str(report.get("candidate_id") or candidate_root.name),
                "status": report.get("status"),
                "summary": report.get("summary"),
                "rationale": report.get("rationale"),
                "created_at": report.get("created_at"),
                "recommended": report.get("recommended"),
                "source_version": report.get("source_version"),
                "to_version": report.get("to_version"),
            }
        )
    return records


def load_improvement_candidate(
    root: Path, pack: AppPack, candidate_id: str
) -> dict[str, Any]:
    if not CANDIDATE_ID.fullmatch(candidate_id):
        raise ValueError("invalid improvement candidate")
    candidate_root = root / "var" / "improvements" / pack.id / candidate_id
    report_path = candidate_root / "report.json"
    if not report_path.is_file():
        raise KeyError(f"improvement candidate {candidate_id!r} was not found")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("improvement candidate report is invalid")
    return report | {"path": str(candidate_root)}


def improvement_is_promotable(result: dict[str, Any]) -> bool:
    return (
        result.get("status") == "candidate"
        and _has_completed_replay(result)
        and _has_no_regressions(result["baseline"], result["candidate"])
    )


def promote_improvement(root: Path, pack: AppPack, result: dict[str, Any]) -> dict[str, Any]:
    """Promote a replayed candidate and retain an immutable snapshot of production."""
    candidate_id = str(result.get("candidate_id", ""))
    candidate_root = root / "var" / "improvements" / pack.id / candidate_id
    if (
        not CANDIDATE_ID.fullmatch(candidate_id)
        or Path(result.get("path", "")).resolve() != candidate_root.resolve()
    ):
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
    if not _has_no_regressions(report["baseline"], report["candidate"]):
        raise ValueError("improvement candidate regresses a passing replay case")

    current = AppPack.load(pack.path)
    if current.version != pack.version or current.functionality != pack.functionality:
        raise ValueError("production application changed after the candidate was created")
    if (
        report.get("source_version") != current.version
        or report.get("source_functionality_sha256") != current.functionality_sha256
    ):
        raise ValueError("production application changed after the candidate was created")
    next_version = _bump_patch(current.version)
    revised = candidate_path.read_text(encoding="utf-8").rstrip() + "\n"
    if report.get("candidate_functionality_sha256") != hashlib.sha256(
        revised.encode("utf-8")
    ).hexdigest():
        raise ValueError("improvement candidate changed after replay")
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
    major, minor, patch = map(int, version.split("."))
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
        cases = score.get("cases")
        if not isinstance(cases, list) or len(cases) != score["total"]:
            return False
        if any(
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not isinstance(case.get("passed"), bool)
            for case in cases
        ):
            return False
    return report["baseline"]["total"] == report["candidate"]["total"]


def _has_no_regressions(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    baseline_cases = {case["id"]: case["passed"] for case in baseline.get("cases", [])}
    candidate_cases = {case["id"]: case["passed"] for case in candidate.get("cases", [])}
    return baseline_cases.keys() == candidate_cases.keys() and all(
        not passed or candidate_cases[case_id]
        for case_id, passed in baseline_cases.items()
    )


def _score(
    root: Path, config: LiteLLMConfig, pack: AppPack, functionality: str
) -> dict[str, Any]:
    cases = json.loads((root / "eval" / "cases.json").read_text(encoding="utf-8"))["cases"]
    cases = [case for case in cases if case["app"] == pack.id]
    results: list[dict[str, Any]] = []
    candidate_pack = replace(pack, functionality=functionality)
    with TemporaryDirectory(prefix="prompt-os-improve-") as directory:
        temp = Path(directory)
        for case in cases:
            database = temp / f"{case['id']}.sqlite"
            contracts = temp / "contracts" / case["id"]
            result = run_case(
                root,
                config,
                candidate_pack,
                case,
                database=database,
                contract_root=contracts,
            )
            results.append(result)
    return {
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "cases": results,
    }
