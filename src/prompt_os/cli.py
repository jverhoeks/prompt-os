from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .app_pack import AppPack, discover_app_packs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-os")
    parser.add_argument("--apps-root", type=Path, default=Path.cwd() / "apps")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("apps", help="list available applications")
    show = commands.add_parser("show", help="show an application's business functionality")
    show.add_argument("app_id")
    commands.add_parser("validate", help="validate all application packs")
    evaluate = commands.add_parser("eval", help="run opt-in model evaluations through LiteLLM")
    evaluate.add_argument("--app", dest="selected_app")
    evaluate.add_argument("--suite", choices=("smoke", "contracts", "all"), default="smoke")
    for name, help_text in (
        ("chat", "run an application and persist its traces"),
        ("tui", "run an application in a terminal interface"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("app_id")
        command.add_argument("--timezone", default="UTC")
        command.add_argument("--debug", action="store_true", help="show tool activity")
    web = commands.add_parser("web", help="run a local browser interface")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--open", action="store_true", help="open the interface in a browser")
    improve = commands.add_parser("improve", help="propose and replay one trace-based improvement")
    improve.add_argument("app_id")
    improve.add_argument("--trace-limit", type=int, default=20)
    contract = commands.add_parser(
        "contract", help="review and promote a generated data-contract candidate"
    )
    contract.add_argument("app_id")
    contract.add_argument("candidate_id", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except EOFError:
        print("Not promoted. The candidate remains available for review.")
        return 0
    except (KeyError, OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    root = Path.cwd()
    packs = discover_app_packs(args.apps_root)
    if args.command == "apps":
        for pack in packs:
            print(f"{pack.id:<18} {pack.version:<8} {pack.name}")
        return 0
    if args.command == "validate":
        print(f"validated {len(packs)} application packs")
        return 0
    if args.command == "eval":
        from .eval_runner import run_evaluation

        return run_evaluation(root, selected_app=args.selected_app, suite=args.suite)
    if args.command == "web":
        from .web import run_web

        return run_web(root, host=args.host, port=args.port, open_browser=args.open)

    pack = _pack(packs, args.app_id)
    if args.command == "show":
        print(pack.functionality)
        return 0
    if args.command == "chat":
        from .chat import run_chat

        return run_chat(root, pack, timezone=args.timezone, debug=args.debug)
    if args.command == "tui":
        from .tui import run_tui

        return run_tui(root, pack, timezone=args.timezone, debug=args.debug)
    if args.command == "improve":
        from .improvement import format_improvement_proposal, improve, promote_improvement

        result = improve(root, pack, trace_limit=args.trace_limit)
        print(format_improvement_proposal(pack, result))
        if result["status"] == "no-change":
            return 0
        if not _approved("Promote this improvement?"):
            print("Not promoted. The candidate remains available for review.")
            return 0
        promotion = promote_improvement(root, pack, result)
        print(
            f"Promoted {pack.id} {promotion['from_version']} -> {promotion['to_version']}.\n"
            f"Previous version archived at {promotion['archive_path']}"
        )
        return 0
    from .contract_review import (
        format_contract_review,
        promote_contract_candidate,
        review_contract_candidate,
    )

    review = review_contract_candidate(root, pack, args.candidate_id)
    print(format_contract_review(review))
    if not review["replay"]["passed"]:
        print("Not promotable: replay checks failed.")
        return 1
    if not _approved("Promote this data contract?"):
        print("Not promoted. The candidate remains available for review.")
        return 0
    promoted = promote_contract_candidate(root, pack, review)
    print(f"Promoted data contract {promoted['version']} for {pack.id}.")
    return 0


def _pack(packs: list[AppPack], app_id: str) -> AppPack:
    pack = next((item for item in packs if item.id == app_id), None)
    if pack is None:
        raise ValueError(f"unknown application {app_id!r}")
    return pack


def _approved(question: str) -> bool:
    return input(f"\n{question} [y/N] ").strip().lower() in {"y", "yes"}


def _load_env(path: Path) -> None:
    """Read KEY=VALUE lines into the environment without overriding shell values."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
