from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import DataTable, Input, Markdown

from prompt_os.app_pack import AppPack
from prompt_os.tui import PromptTUI


class _Session:
    def __init__(self, outcome: dict[str, Any] | Exception) -> None:
        self.outcome = outcome
        self.prompts: list[str] = []

    def send(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _Trace:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, **record: Any) -> None:
        self.records.append(record)


def _pack() -> AppPack:
    return AppPack(
        id="sample-app",
        name="Sample application",
        version="0.1.0",
        capabilities=(),
        data_policy="persistent",
        functionality="Sample functionality",
        path=Path("sample-app"),
    )


async def _wait_for_turn(app: PromptTUI) -> None:
    for _ in range(100):
        if not app._busy:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("TUI turn did not finish")


def test_tui_edits_input_and_renders_generic_markdown() -> None:
    async def scenario() -> None:
        session = _Session(
            {
                "reply": "- **Result:** 4",
                "tool_calls": [
                    {
                        "name": "view.present",
                        "status": "success",
                        "arguments": {
                            "view": {
                                "title": "Result",
                                "blocks": [
                                    {
                                        "type": "table",
                                        "columns": ["Item", "Value"],
                                        "rows": [["Answer", "4"]],
                                    }
                                ],
                            }
                        },
                    }
                ],
            }
        )
        trace = _Trace()
        app = PromptTUI(
            pack=_pack(), session=session, trace=trace, model="model"  # type: ignore[arg-type]
        )
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.press("a", "b", "backspace", "c")
            assert app.query_one("#prompt", Input).value == "ac"

            await pilot.press("enter")
            await _wait_for_turn(app)
            await pilot.pause()

            assert session.prompts == ["ac"]
            assert len(app.query(".user-message")) == 1
            assert len(app.query(".assistant-message")) == 1
            assert len(app.query(Markdown)) == 1
            assert len(app.query(".structured-view")) == 1
            assert len(app.query(DataTable)) == 1
            assert trace.records[0]["outcome"] == "completed"

    asyncio.run(scenario())


def test_tui_only_shows_tool_activity_in_debug_mode() -> None:
    async def count_debug_details(debug: bool) -> int:
        app = PromptTUI(
            pack=_pack(),
            session=_Session(
                {
                    "reply": "Done.",
                    "tool_calls": [{"name": "store.scan", "arguments": {}}],
                }
            ),  # type: ignore[arg-type]
            trace=_Trace(),  # type: ignore[arg-type]
            model="model",
            debug=debug,
        )
        async with app.run_test(size=(90, 28)) as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.value = "show data"
            await pilot.press("enter")
            await _wait_for_turn(app)
            await pilot.pause()
            return len(app.query(".debug-details"))

    assert asyncio.run(count_debug_details(False)) == 0
    assert asyncio.run(count_debug_details(True)) == 1


def test_tui_surfaces_turn_errors_and_recovers_input() -> None:
    async def scenario() -> None:
        trace = _Trace()
        app = PromptTUI(
            pack=_pack(),
            session=_Session(RuntimeError("model unavailable")),  # type: ignore[arg-type]
            trace=trace,  # type: ignore[arg-type]
            model="model",
        )
        async with app.run_test(size=(90, 28)) as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.value = "hello"
            await pilot.press("enter")
            await _wait_for_turn(app)
            await pilot.pause()

            assert len(app.query(".error-message")) == 1
            assert prompt.disabled is False
            assert trace.records[0]["outcome"] == "error"

    asyncio.run(scenario())
