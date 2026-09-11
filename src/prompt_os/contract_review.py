from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .app_pack import AppPack
from .tool_service import ToolService


def review_contract_candidate(
    root: Path, pack: AppPack, candidate_id: str | None = None
) -> dict[str, Any]:
    service = _service(root, pack)
    try:
        candidates = [
            item for item in service.contracts.candidates(pack.id) if item["status"] == "candidate"
        ]
        if candidate_id is None:
            if not candidates:
                raise ValueError(f"no contract candidates found for {pack.id}")
            candidate_id = candidates[0]["id"]
        contract = service.contracts.candidate_contract(pack.id, candidate_id)
        replay = service.replay_contract(candidate_id)
        metadata = next(
            (item for item in candidates if item["id"] == candidate_id), None
        )
        if metadata is None:
            raise ValueError(f"contract candidate {candidate_id!r} is not available")
        return {
            "candidate_id": candidate_id,
            "metadata": metadata,
            "contract": contract,
            "replay": replay,
        }
    finally:
        service.close()


def promote_contract_candidate(
    root: Path, pack: AppPack, review: dict[str, Any]
) -> dict[str, Any]:
    service = _service(root, pack)
    try:
        return service.promote_contract(
            review["candidate_id"], review["replay"]["replay_id"]
        )
    finally:
        service.close()


def format_contract_review(review: dict[str, Any]) -> str:
    replay = review["replay"]
    checks = "\n".join(
        f"  {'PASS' if check['passed'] else 'FAIL'} {check['name']}"
        + (f": {check['detail']}" if check.get("detail") else "")
        for check in replay["checks"]
    )
    contract = json.dumps(review["contract"], ensure_ascii=False, indent=2)
    return (
        f"Data-contract candidate {review['candidate_id']}\n\n"
        f"Rationale: {review['metadata']['rationale']}\n"
        f"Evidence records: {len(review['metadata']['evidence'])}\n\n"
        f"Replay checks:\n{checks}\n\n"
        f"Proposed contract:\n{contract}"
    )


def _service(root: Path, pack: AppPack) -> ToolService:
    (root / "var").mkdir(parents=True, exist_ok=True)
    return ToolService(
        app_id=pack.id,
        database=root / "var" / "prompt-os.sqlite",
        contract_root=root / "var" / "data-contracts",
        contract_schema=root / "contracts" / "data-contract.schema.json",
        timezone="UTC",
        functionality_sha256=hashlib.sha256(
            (pack.functionality.rstrip() + "\n").encode("utf-8")
        ).hexdigest(),
        trace_path=root / "var" / "traces" / f"{pack.id}.jsonl",
    )
