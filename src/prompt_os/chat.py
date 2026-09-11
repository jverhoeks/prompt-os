from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from .agent_loop import StrandsSession
from .app_pack import AppPack
from .model_client import LiteLLMConfig, check_model
from .tracing import TraceWriter, utc_now


def run_chat(root: Path, pack: AppPack, *, timezone: str, debug: bool = False) -> int:
    config = LiteLLMConfig.from_environment()
    check_model(config)
    data_root = root / "var"
    trace = TraceWriter(
        data_root / "traces" / f"{pack.id}.jsonl", mode=pack.trace_mode
    )
    console = Console()
    console.print(f"[bold]{pack.name}[/bold] — type [cyan]/quit[/cyan] to leave")
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
        debug=debug,
        trace_path=trace.path,
    ) as session:
        while True:
            try:
                # Keep input on the interpreter's native terminal path so line
                # editing keys such as backspace behave normally.
                user_message = input("you> ").strip()
            except EOFError:
                console.print()
                return 0
            if not user_message:
                continue
            if user_message in {"/quit", "/exit"}:
                return 0
            started_at = utc_now()
            try:
                outcome = session.send(user_message)
                trace.write(
                    app_id=pack.id,
                    model=config.model,
                    user_message=user_message,
                    started_at=started_at,
                    outcome="completed",
                    reply=outcome["reply"],
                    tool_calls=outcome["tool_calls"],
                )
                console.print("[bold cyan]app>[/bold cyan]")
                console.print(Markdown(outcome["reply"]))
            except Exception as exc:
                trace.write(
                    app_id=pack.id,
                    model=config.model,
                    user_message=user_message,
                    started_at=started_at,
                    outcome="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
                console.print(f"error: {exc}", style="bold red", markup=False)
