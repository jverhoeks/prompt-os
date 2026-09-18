from __future__ import annotations

import json
from pathlib import Path

from prompt_os.app_pack import AppPack
from prompt_os.contract_review import (
    format_contract_review,
    promote_contract_candidate,
    review_contract_candidate,
)
from prompt_os.tool_service import ToolService



def test_operator_reviews_replay_before_contract_promotion(tmp_path: Path) -> None:
    app_root = tmp_path / "apps" / "sample-app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "id": "sample-app",
                "name": "Sample",
                "version": "0.1.0",
                "functionality": "FUNCTIONALITY.md",
                "capabilities": ["documents"],
                "data_policy": "persistent",
            }
        ),
        encoding="utf-8",
    )
    (app_root / "FUNCTIONALITY.md").write_text(
        "# Purpose\n\nRecord labels.\n", encoding="utf-8"
    )
    pack = AppPack.load(app_root)
    (tmp_path / "var").mkdir()
    service = ToolService(
        app_id=pack.id,
        database=tmp_path / "var" / "prompt-os.sqlite",
        contract_root=tmp_path / "var" / "data-contracts",
        timezone="UTC",
    )
    for index in range(3):
        service.put({"label": f"label-{index}"}, document_id=f"record-{index}")
    evidence = list(service.contract_evidence())
    proposal = service.propose_contract(
        {
            "version": "0.1.0",
            "based_on": None,
            "summary": "Labels supported by three records.",
            "collections": {
                "things": {
                    "description": "Recorded things.",
                    "identity": ["label"],
                    "fields": {
                        "label": {
                            "description": "Recorded label.",
                            "type": "string",
                            "required": True,
                            "evidence": evidence,
                        }
                    },
                }
            },
            "open_questions": [],
        },
        rationale="Three immutable records agree.",
        evidence=evidence,
    )
    service.close()

    review = review_contract_candidate(tmp_path, pack, proposal["candidate_id"])

    assert review["replay"]["passed"] is True
    assert "PASS evidence" in format_contract_review(review)
    promoted = promote_contract_candidate(tmp_path, pack, review)
    assert promoted["version"] == "0.1.0"
