from __future__ import annotations

import json
from pathlib import Path

from prompt_os.app_pack import AppPack
from prompt_os.model_client import LiteLLMConfig
from prompt_os.replay import case_detail, run_case
from prompt_os.store import DocumentStore
from prompt_os.tracing import TraceWriter


ROOT = Path(__file__).parents[1]


class FakeSession:
    def __init__(self, config, *, app_id, database, **kwargs) -> None:
        self.store = DocumentStore(database, app_id=app_id)
        self.turn = 0

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.store.close()

    def send(self, message: str) -> dict:
        self.turn += 1
        value = {"description": "Call Alex", "completed": self.turn == 2}
        self.store.put("inbox", value, document_id="task-1")
        return {
            "reply": "Saved.",
            "tool_calls": [{"name": "store.put", "status": "success"}],
        }


def test_multi_turn_replay_asserts_business_state_and_keeps_session_identity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("prompt_os.replay.StrandsSession", FakeSession)
    pack = AppPack.load(ROOT / "apps" / "task-list")
    trace = TraceWriter(tmp_path / "traces.jsonl")
    case = {
        "id": "task-lifecycle",
        "turns": [
            {"message": "Remember I need to call Alex.", "required_tools": ["store.put"]},
            {"message": "Mark that task complete.", "required_tools": ["store.put"]},
        ],
        "expected_state": {"documents": 1, "revisions": 2, "candidates": 0},
    }

    result = run_case(
        ROOT,
        LiteLLMConfig("http://example", "key", "model"),
        pack,
        case,
        database=tmp_path / "data.sqlite",
        contract_root=tmp_path / "contracts",
        trace=trace,
    )

    assert result["passed"] is True
    assert result["document_count"] == 1
    assert result["revision_count"] == 2
    assert all(check["passed"] for check in result["state_checks"].values())
    records = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert [record["turn_index"] for record in records] == [1, 2]
    assert len({record["session_id"] for record in records}) == 1


def test_replay_fails_when_observed_state_differs_from_exact_expectation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("prompt_os.replay.StrandsSession", FakeSession)
    pack = AppPack.load(ROOT / "apps" / "task-list")
    case = {
        "id": "task-count",
        "message": "Remember I need to call Alex.",
        "required_tools": ["store.put"],
        "expected_state": {"documents": 2, "revisions": 1, "candidates": 0},
    }

    result = run_case(
        ROOT,
        LiteLLMConfig("http://example", "key", "model"),
        pack,
        case,
        database=tmp_path / "data.sqlite",
        contract_root=tmp_path / "contracts",
    )

    assert result["passed"] is False
    assert result["state_checks"]["documents"] == {
        "expected": 2,
        "actual": 1,
        "passed": False,
    }
    assert "'expected': 2" in case_detail(result)
