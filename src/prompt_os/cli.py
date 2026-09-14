from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

from .app_pack import discover_app_packs


def _default_apps_root() -> Path:
    return Path.cwd() / "apps"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-os")
    parser.add_argument("--apps-root", type=Path, default=_default_apps_root())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("apps", help="list available applications")
    show = commands.add_parser("show", help="show an application's business functionality")
    show.add_argument("app_id")
    commands.add_parser("validate", help="validate all application packs")
    evaluate = commands.add_parser("eval", help="run opt-in model evaluations through LiteLLM")
    evaluate.add_argument("--app", dest="selected_app")
    evaluate.add_argument("--suite", choices=("smoke", "contracts", "all"), default="smoke")
    chat = commands.add_parser("chat", help="run an application and persist its traces")
    chat.add_argument("app_id")
    chat.add_argument("--timezone", default="UTC")
    chat.add_argument("--debug", action="store_true", help="show detailed runtime logs")
    tui = commands.add_parser("tui", help="run an application in a terminal interface")
    tui.add_argument("app_id")
    tui.add_argument("--timezone", default="UTC")
    tui.add_argument("--debug", action="store_true", help="show tool activity")
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
    load_dotenv(Path.cwd() / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        packs = discover_app_packs(args.apps_root)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "apps":
        for pack in packs:
            print(f"{pack.id:<18} {pack.version:<8} {pack.name}")
        return 0
    if args.command == "show":
        pack = next((item for item in packs if item.id == args.app_id), None)
        if pack is None:
            print(f"error: unknown application {args.app_id!r}", file=sys.stderr)
            return 1
        print(pack.functionality)
        return 0
    if args.command == "eval":
        from .eval_runner import run_evaluation

        try:
            return run_evaluation(
                Path.cwd(), selected_app=args.selected_app, suite=args.suite
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "chat":
        from .chat import run_chat

        pack = next((item for item in packs if item.id == args.app_id), None)
        if pack is None:
            print(f"error: unknown application {args.app_id!r}", file=sys.stderr)
            return 1
        try:
            return run_chat(
                Path.cwd(), pack, timezone=args.timezone, debug=args.debug
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "tui":
        from .tui import run_tui

        pack = next((item for item in packs if item.id == args.app_id), None)
        if pack is None:
            print(f"error: unknown application {args.app_id!r}", file=sys.stderr)
            return 1
        try:
            return run_tui(
                Path.cwd(), pack, timezone=args.timezone, debug=args.debug
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "web":
        from .web import run_web

        try:
            return run_web(
                Path.cwd(), host=args.host, port=args.port, open_browser=args.open
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "improve":
        from .improvement import format_improvement_proposal, improve, promote_improvement

        pack = next((item for item in packs if item.id == args.app_id), None)
        if pack is None:
            print(f"error: unknown application {args.app_id!r}", file=sys.stderr)
            return 1
        try:
            result = improve(Path.cwd(), pack, trace_limit=args.trace_limit)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(format_improvement_proposal(pack, result))
        if result["status"] == "no-change":
            return 0
        try:
            approved = input("\nPromote this improvement? [y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            approved = False
        if not approved:
            print("Not promoted. The candidate remains available for review.")
            return 0
        try:
            promotion = promote_improvement(Path.cwd(), pack, result)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"Promoted {pack.id} {promotion['from_version']} -> {promotion['to_version']}.\n"
            f"Previous version archived at {promotion['archive_path']}"
        )
        return 0
    if args.command == "contract":
        from .contract_review import (
            format_contract_review,
            promote_contract_candidate,
            review_contract_candidate,
        )

        pack = next((item for item in packs if item.id == args.app_id), None)
        if pack is None:
            print(f"error: unknown application {args.app_id!r}", file=sys.stderr)
            return 1
        try:
            review = review_contract_candidate(Path.cwd(), pack, args.candidate_id)
            print(format_contract_review(review))
            if not review["replay"]["passed"]:
                print("Not promotable: replay checks failed.")
                return 1
            approved = input("\nPromote this data contract? [y/N] ").strip().lower() in {
                "y",
                "yes",
            }
            if not approved:
                print("Not promoted. The candidate remains available for review.")
                return 0
            promoted = promote_contract_candidate(Path.cwd(), pack, review)
        except EOFError:
            print("Not promoted. The candidate remains available for review.")
            return 0
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Promoted data contract {promoted['version']} for {pack.id}.")
        return 0
    print(f"validated {len(packs)} application packs")
    return 0
