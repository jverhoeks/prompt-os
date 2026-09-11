import asyncio
import json
from pathlib import Path

import pytest

from prompt_os.data_contract import DataContractRepository
from prompt_os.mcp_server import create_server
from prompt_os.model_client import LiteLLMConfig
from prompt_os.tool_service import ToolService
from prompt_os.cli import main


ROOT = Path(__file__).parents[1]


def generated_contract(*, based_on=None, version="0.1.0") -> dict:
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
                        "evidence": ["first example", "second example", "third example"],
                    },
                    "note": {
                        "description": "Optional supporting detail.",
                        "type": "string",
                        "required": False,
                        "evidence": ["one record included a note"],
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
        evidence=["record-1", "record-2", "record-3"],
    )

    assert repository.current("sample-app") is None
    with pytest.raises(ValueError, match="replay"):
        repository.promote("sample-app", candidate.id, replay_passed=False)
    promoted = repository.promote("sample-app", candidate.id, replay_passed=True)
    assert repository.current("sample-app") == promoted


def test_required_generated_field_needs_three_examples(tmp_path: Path) -> None:
    repository = DataContractRepository(
        tmp_path / "contracts", ROOT / "contracts" / "data-contract.schema.json"
    )
    contract = generated_contract()
    contract["collections"]["things"]["fields"]["label"]["evidence"] = ["only one"]
    with pytest.raises(ValueError, match="three supporting examples"):
        repository.propose("sample-app", contract, rationale="Too early", evidence=["record-1"])


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

    candidate = service.contracts.propose(
        "sample-app",
        generated_contract(),
        rationale="Enough examples exist.",
        evidence=["record-1", "record-2", "record-3"],
    )
    service.contracts.promote("sample-app", candidate.id, replay_passed=True)
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
