import asyncio
import json
from pathlib import Path

import pytest

from prompt_os.data_contract import DataContractRepository
from prompt_os.mcp_server import GeneratedDataContract, _contract_value, create_server
from prompt_os.model_client import LiteLLMConfig
from prompt_os.tool_service import ToolService
from prompt_os.cli import main


ROOT = Path(__file__).parents[1]
EVIDENCE = [
    "document:record-1@1",
    "document:record-2@1",
    "document:record-3@1",
]


def available_evidence() -> dict[str, dict]:
    return {
        evidence_id: {"kind": "document", "document": {"label": evidence_id}}
        for evidence_id in EVIDENCE
    }


def generated_contract(
    *, based_on=None, version="0.1.0", field_evidence: list[str] | None = None
) -> dict:
    return {
        "version": version,
        "based_on": based_on,
        "summary": "A structure inferred from recurring records.",
        "collections": {
            "things": {
                "description": "Things observed in the business activity.",
                "identity": ["label"],
                "fields": {
                    "label": {
                        "description": "The supplied label.",
                        "type": "string",
                        "required": True,
                        "evidence": field_evidence or EVIDENCE,
                    },
                },
            }
        },
        "open_questions": [],
    }


def test_generated_contract_is_candidate_until_replay_gated_promotion(tmp_path: Path) -> None:
    repository = DataContractRepository(
        tmp_path / "contracts", ROOT / "contracts" / "data-contract.schema.json"
    )
    candidate = repository.propose(
        "sample-app",
        generated_contract(),
        rationale="Repeated records now support stable labels.",
        evidence=EVIDENCE,
        available_evidence=available_evidence(),
    )

    assert repository.current("sample-app") is None
    with pytest.raises(ValueError, match="replay"):
        repository.promote(
            "sample-app",
            candidate.id,
            replay_id="not-a-replay",
            available_evidence=available_evidence(),
        )
    replay = repository.replay(
        "sample-app", candidate.id, available_evidence=available_evidence()
    )
    promoted = repository.promote(
        "sample-app",
        candidate.id,
        replay_id=replay["replay_id"],
        available_evidence=available_evidence(),
    )
    assert repository.current("sample-app") == promoted


def test_initial_generated_contract_keeps_explicit_null_base_version() -> None:
    value = _contract_value(GeneratedDataContract.model_validate(generated_contract()))

    assert "based_on" in value
    assert value["based_on"] is None


def test_required_generated_field_needs_three_examples(tmp_path: Path) -> None:
    repository = DataContractRepository(
        tmp_path / "contracts", ROOT / "contracts" / "data-contract.schema.json"
    )
    contract = generated_contract()
    contract["collections"]["things"]["fields"]["label"]["evidence"] = [EVIDENCE[0]]
    with pytest.raises(ValueError, match="three supporting examples"):
        repository.propose(
            "sample-app",
            contract,
            rationale="Too early",
            evidence=[EVIDENCE[0]],
            available_evidence=available_evidence(),
        )
    with pytest.raises(ValueError, match="unknown evidence"):
        unknown = ["made-up-1", "made-up-2", "made-up-3"]
        repository.propose(
            "sample-app",
            generated_contract(field_evidence=unknown),
            rationale="Ungrounded",
            evidence=unknown,
            available_evidence=available_evidence(),
        )


def test_contract_replay_is_bound_to_candidate_and_immutable_evidence(
    tmp_path: Path,
) -> None:
    repository = DataContractRepository(
        tmp_path / "contracts", ROOT / "contracts" / "data-contract.schema.json"
    )
    available = available_evidence()
    candidate = repository.propose(
        "sample-app",
        generated_contract(),
        rationale="Grounded candidate.",
        evidence=EVIDENCE,
        available_evidence=available,
    )
    replay = repository.replay(
        "sample-app", candidate.id, available_evidence=available
    )
    changed = available | {
        EVIDENCE[0]: {"kind": "document", "document": {"label": "changed"}}
    }

    with pytest.raises(ValueError, match="replay"):
        repository.promote(
            "sample-app",
            candidate.id,
            replay_id=replay["replay_id"],
            available_evidence=changed,
        )


def test_tool_service_uses_inbox_then_promoted_contract(tmp_path: Path) -> None:
    service = ToolService(
        app_id="sample-app",
        database=tmp_path / "db.sqlite",
        contract_root=tmp_path / "contracts",
        contract_schema=ROOT / "contracts" / "data-contract.schema.json",
        timezone="UTC",
    )
    loose = service.put({"raw": "unstructured first record"})
    assert loose["collection"] == "inbox"
    with pytest.raises(ValueError, match="must enter inbox"):
        service.put({"label": "first"}, collection="things")

    for index in range(1, 4):
        service.put({"label": f"example-{index}"}, document_id=f"record-{index}")
    evidence = [
        evidence_id
        for evidence_id, item in service.contract_evidence().items()
        if item.get("document", {}).get("label")
    ]
    candidate = service.propose_contract(
        generated_contract(field_evidence=evidence),
        rationale="Enough examples exist.",
        evidence=evidence,
    )
    replay = service.replay_contract(candidate["candidate_id"])
    service.promote_contract(candidate["candidate_id"], replay["replay_id"])
    saved = service.put({"label": "first"}, collection="things")
    assert saved["collection"] == "things"
    with pytest.raises(ValueError, match="unknown fields"):
        service.put({"label": "second", "invented": 3}, collection="things")
    service.close()


def test_document_store_is_isolated_by_application(tmp_path: Path) -> None:
    common = dict(
        database=tmp_path / "db.sqlite",
        contract_root=tmp_path / "contracts",
        contract_schema=ROOT / "contracts" / "data-contract.schema.json",
        timezone="UTC",
    )
    first = ToolService(app_id="first-app", **common)
    second = ToolService(app_id="second-app", **common)
    first.put({"private": "first"})
    second.put({"private": "second"})
    assert [row["document"] for row in first.scan()] == [{"private": "first"}]
    assert [row["document"] for row in second.scan()] == [{"private": "second"}]
    first.close()
    second.close()


def test_mcp_server_matches_the_fundamental_tool_catalog(tmp_path: Path) -> None:
    service = ToolService(
        app_id="sample-app",
        database=tmp_path / "db.sqlite",
        contract_root=tmp_path / "contracts",
        contract_schema=ROOT / "contracts" / "data-contract.schema.json",
        timezone="UTC",
    )

    async def names() -> set[str]:
        return {tool.name for tool in await create_server(service).list_tools()}

    catalog = json.loads((ROOT / "contracts" / "tool-catalog.json").read_text())["tools"]
    assert asyncio.run(names()) == {tool["name"] for tool in catalog}
    service.close()


def test_math_service_is_deterministic_and_bounded(tmp_path: Path) -> None:
    service = ToolService(
        app_id="sample-app",
        database=tmp_path / "db.sqlite",
        contract_root=tmp_path / "contracts",
        contract_schema=ROOT / "contracts" / "data-contract.schema.json",
        timezone="UTC",
    )
    assert service.calculate("84 * 0.17")["value"] == "14.28"
    with pytest.raises(ValueError, match="unsupported"):
        service.calculate("__import__('os').getcwd()")
    service.close()


def test_unit_conversion_is_deterministic_and_rejects_incompatible_units(
    tmp_path: Path,
) -> None:
    service = ToolService(
        app_id="sample-app",
        database=tmp_path / "db.sqlite",
        contract_root=tmp_path / "contracts",
        contract_schema=ROOT / "contracts" / "data-contract.schema.json",
        timezone="UTC",
    )
    conversion = service.convert("3", "miles", "km")
    assert conversion["output"] == {
        "value": "4.828032",
        "unit": "kilometre",
    }
    assert "1609.344" in conversion["basis"]
    assert service.convert("32", "fahrenheit", "celsius")["output"]["value"] == "0"
    with pytest.raises(ValueError, match="different kinds"):
        service.convert("10", "metres", "kilograms")
    service.close()


def test_contract_trace_evidence_is_hash_bound_without_exposing_content(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "trace_id": "trace-1",
                "app_id": "sample-app",
                "outcome": "completed",
                "user_message": "private message",
                "tool_calls": [{"name": "store.put", "arguments": {"secret": "value"}}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    service = ToolService(
        app_id="sample-app",
        database=tmp_path / "db.sqlite",
        contract_root=tmp_path / "contracts",
        contract_schema=ROOT / "contracts" / "data-contract.schema.json",
        timezone="UTC",
        trace_path=trace_path,
    )

    evidence = service.contract_evidence()["trace:trace-1"]

    assert evidence["tool_names"] == ["store.put"]
    assert "private message" not in json.dumps(evidence)
    assert "secret" not in json.dumps(evidence)
    service.close()


def test_litellm_configuration_keeps_proxy_details_separate() -> None:
    config = LiteLLMConfig("http://localhost:4000", "secret", "model")
    assert config.base_url == "http://localhost:4000"
    assert config.model == "model"


def test_cli_loads_dotenv_without_overriding_shell_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "LITELLM_BASE_URL=http://from-file:4000\n"
        "LITELLM_API_KEY=file-key\n"
        "LITELLM_MODEL=file-model\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.setenv("LITELLM_MODEL", "shell-model")
    assert main(["--apps-root", str(ROOT / "apps"), "validate"]) == 0
    assert LiteLLMConfig.from_environment() == LiteLLMConfig(
        "http://from-file:4000", "file-key", "shell-model"
    )
