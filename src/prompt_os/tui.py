from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, Input, Label, Markdown, Static

from .agent_loop import StrandsSession
from .app_pack import AppPack
from .model_client import LiteLLMConfig, check_model
from .tracing import TraceWriter, utc_now
from .view_description import (
    BarChartBlock,
    ListBlock,
    MetricBlock,
    TableBlock,
    TextBlock,
    ViewDescription,
    view_from_tool_calls,
)


class TurnCompleted(Message):
    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__()
        self.outcome = outcome


class TurnFailed(Message):
    def __init__(self, detail: str) -> None:
        super().__init__()
        self.detail = detail


class PromptTUI(App[None]):
    """Generic terminal conversation renderer for any application pack."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #conversation {
        height: 1fr;
        padding: 1 2;
        scrollbar-gutter: stable;
    }

    .message {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        padding: 1 2;
    }

    .user-message {
        background: $boost;
        border-left: thick $accent;
    }

    .assistant-message {
        background: $surface;
        border-left: thick $success;
    }

    .error-message {
        background: $error 20%;
        border-left: thick $error;
    }

    .message-label {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    .debug-details {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    .structured-view {
        height: auto;
        margin-bottom: 1;
        padding: 1;
        border: round $primary;
    }

    .view-title {
        height: 1;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    .metric-block {
        height: auto;
        margin-bottom: 1;
        padding: 1;
        background: $boost;
    }

    .metric-value {
        height: 1;
        text-style: bold;
        color: $success;
    }

    .view-table {
        height: auto;
        max-height: 14;
        margin-bottom: 1;
    }

    .view-block {
        height: auto;
        margin-bottom: 1;
    }

    #status {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    #prompt {
        dock: bottom;
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "clear_conversation", "Clear"),
    ]

    def __init__(
        self,
        *,
        pack: AppPack,
        session: StrandsSession,
        trace: TraceWriter,
        model: str,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self.title = pack.name
        self.sub_title = pack.id
        self._session = session
        self._trace = trace
        self._model = model
        self._app_id = pack.id
        self._debug = debug
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="conversation")
        yield Static("Ready", id="status")
        yield Input(placeholder="Type a message and press Enter", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._busy:
            return
        event.input.clear()
        if prompt in {"/quit", "/exit"}:
            self.exit()
            return

        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.mount(_user_message(prompt))
        conversation.scroll_end(animate=False)
        self._set_busy(True)
        self._send(prompt, utc_now())

    @work(thread=True, exclusive=True, group="model-turn", exit_on_error=False)
    def _send(self, prompt: str, started_at: str) -> None:
        try:
            outcome = self._session.send(prompt)
            self._trace.write(
                app_id=self._app_id,
                model=self._model,
                user_message=prompt,
                started_at=started_at,
                outcome="completed",
                reply=outcome["reply"],
                tool_calls=outcome["tool_calls"],
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self._trace.write(
                app_id=self._app_id,
                model=self._model,
                user_message=prompt,
                started_at=started_at,
                outcome="error",
                error=detail,
            )
            self.post_message(TurnFailed(detail))
        else:
            self.post_message(TurnCompleted(outcome))

    async def on_turn_completed(self, event: TurnCompleted) -> None:
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.mount(
            _assistant_message(event.outcome, show_tools=self._debug)
        )
        conversation.scroll_end(animate=False)
        self._set_busy(False)

    async def on_turn_failed(self, event: TurnFailed) -> None:
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.mount(_error_message(event.detail))
        conversation.scroll_end(animate=False)
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = busy
        self.query_one("#status", Static).update("Working…" if busy else "Ready")
        if not busy:
            prompt.focus()

    def action_clear_conversation(self) -> None:
        self.query_one("#conversation", VerticalScroll).remove_children()


def _user_message(content: str) -> Vertical:
    return Vertical(
        Label("You", classes="message-label"),
        Static(Text(content)),
        classes="message user-message",
    )


def _assistant_message(outcome: dict[str, Any], *, show_tools: bool) -> Vertical:
    children: list[Any] = [Label("App", classes="message-label")]
    view = view_from_tool_calls(outcome.get("tool_calls"))
    if view is not None:
        children.append(_structured_view(view))
    children.append(Markdown(str(outcome["reply"])))
    if show_tools and outcome.get("tool_calls"):
        children.append(
            Static(
                Text(json.dumps(outcome["tool_calls"], ensure_ascii=False, indent=2)),
                classes="debug-details",
            )
        )
    return Vertical(*children, classes="message assistant-message")


class ViewTable(DataTable[str]):
    def __init__(self, block: TableBlock) -> None:
        super().__init__(classes="view-table", zebra_stripes=True)
        self._block = block

    def on_mount(self) -> None:
        self.add_columns(*self._block.columns)
        self.add_rows(self._block.rows)


def _structured_view(view: ViewDescription) -> Vertical:
    children: list[Any] = []
    if view.title:
        children.append(Label(view.title, classes="view-title"))
    for block in view.blocks:
        if isinstance(block, TextBlock):
            children.append(Markdown(block.markdown, classes="view-block"))
        elif isinstance(block, MetricBlock):
            metric_children: list[Any] = [
                Label(block.label),
                Static(Text(block.value), classes="metric-value"),
            ]
            if block.detail:
                metric_children.append(Static(Text(block.detail)))
            children.append(Vertical(*metric_children, classes="metric-block"))
        elif isinstance(block, ListBlock):
            markdown = ""
            if block.title:
                markdown += f"**{block.title}**\n\n"
            markdown += "\n".join(f"- {item}" for item in block.items)
            children.append(Markdown(markdown, classes="view-block"))
        elif isinstance(block, TableBlock):
            if block.title:
                children.append(Label(block.title, classes="view-title"))
            children.append(ViewTable(block))
        elif isinstance(block, BarChartBlock):
            children.append(Static(_bar_chart(block), classes="view-block"))
    return Vertical(*children, classes="structured-view")


def _bar_chart(block: BarChartBlock) -> Table:
    table = Table(title=block.title, box=None, show_header=False, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_column(justify="right")
    maximum = max(abs(item.value) for item in block.series) or 1
    for item in block.series:
        width = max(1, round(abs(item.value) / maximum * 24)) if item.value else 0
        table.add_row(item.label, "█" * width, item.display or f"{item.value:g}")
    return table


def _error_message(detail: str) -> Vertical:
    return Vertical(
        Label("Error", classes="message-label"),
        Static(Text(detail)),
        classes="message error-message",
    )


def run_tui(root: Path, pack: AppPack, *, timezone: str, debug: bool = False) -> int:
    config = LiteLLMConfig.from_environment()
    check_model(config)
    data_root = root / "var"
    trace = TraceWriter(
        data_root / "traces" / f"{pack.id}.jsonl", mode=pack.trace_mode
    )
    with StrandsSession(
        config,
        app_id=pack.id,
        functionality=pack.functionality,
        database=data_root / "prompt-os.sqlite",
        contract_root=data_root / "data-contracts",
        contract_schema=root / "contracts" / "data-contract.schema.json",
        tool_catalog=root / "contracts" / "tool-catalog.json",
        capabilities=pack.capabilities,
        timezone=timezone,
        debug=False,
        trace_path=trace.path,
    ) as session:
        PromptTUI(
            pack=pack,
            session=session,
            trace=trace,
            model=config.model,
            debug=debug,
        ).run()
    return 0
