from __future__ import annotations

from pathlib import Path
from typing import Any

from prompt_os.agent_loop import (
    StrandsSession,
    _allowed_tool_names,
    _mcp_server_arguments,
    _model_tool_name,
    _tool_calls,
)


ROOT = Path(__file__).parents[1]


class _Client:
    stopped_with: tuple[Any, Any, Any] | None = None

    def stop(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stopped_with = (exc_type, exc, traceback)


def test_session_passes_context_to_mcp_client_on_exit() -> None:
    session = object.__new__(StrandsSession)
    client = _Client()
    session._client = client  # type: ignore[assignment]

    session.__exit__(None, None, None)

    assert client.stopped_with == (None, None, None)


def test_mcp_tool_names_are_safe_for_model_apis() -> None:
    assert _model_tool_name("math.evaluate") == "math_evaluate"
    assert _model_tool_name("already-safe") == "already-safe"
    assert len(_model_tool_name("namespace." + "x" * 80)) == 64


def test_tool_call_reporting_restores_mcp_wire_name() -> None:
    messages = [
        {
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "call-1",
                        "name": "math_evaluate",
                        "input": {"expression": "2 + 2"},
                    }
                }
            ]
        }
    ]

    calls = _tool_calls(messages, tool_names={"math_evaluate": "math.evaluate"})

    assert calls[0]["name"] == "math.evaluate"


def test_session_sets_error_only_mcp_logging_by_default() -> None:
    arguments = _mcp_server_arguments(
        app_id="sample",
        database=Path("db.sqlite"),
        contract_root=Path("contracts"),
        contract_schema=Path("schema.json"),
        timezone="UTC",
        debug=False,
    )

    assert arguments[-2:] == ["--log-level", "ERROR"]


def test_session_enables_mcp_debug_logging_explicitly() -> None:
    arguments = _mcp_server_arguments(
        app_id="sample",
        database=Path("db.sqlite"),
        contract_root=Path("contracts"),
        contract_schema=Path("schema.json"),
        timezone="UTC",
        debug=True,
    )

    assert arguments[-2:] == ["--log-level", "DEBUG"]


def test_application_capabilities_limit_exposed_tools() -> None:
    catalog = ROOT / "contracts" / "tool-catalog.json"

    expense_tools = _allowed_tool_names(
        catalog, ("documents", "clock", "aggregation")
    )
    calculator_tools = _allowed_tool_names(catalog, ("calculation", "conversion"))

    assert "store.put" in expense_tools
    assert "store.aggregate" in expense_tools
    assert "system.now" in expense_tools
    assert "math.evaluate" not in expense_tools
    assert "math.evaluate" in calculator_tools
    assert "store.put" not in calculator_tools
    assert "view.present" in expense_tools
    assert "view.present" in calculator_tools
