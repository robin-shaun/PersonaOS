from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.bootstrap import build_container
from core.config import Settings


def _password_from_input(*, password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline()
        if password == "":
            raise ValueError("password stdin is empty")
        return password.rstrip("\r\n")
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("password confirmation does not match")
    return password


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.admin",
        description="Trusted local administration for PersonaOS.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    create = subcommands.add_parser(
        "create-account",
        help="Create a login account from the trusted host.",
    )
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument(
        "--role",
        choices=("admin", "member"),
        default="member",
    )
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read one password line from stdin instead of prompting.",
    )

    list_accounts = subcommands.add_parser(
        "list-accounts",
        help="List registered accounts without credential material.",
    )
    list_accounts.set_defaults(command="list-accounts")

    migrate = subcommands.add_parser(
        "migrate-legacy-owner",
        help="Preview or explicitly apply 0.11 owner reassignment.",
    )
    migrate.add_argument(
        "--source",
        help="Legacy owner ID; defaults to PERSONA_LOCAL_OWNER_ID.",
    )
    migrate.add_argument("--to", required=True, dest="target_username")
    migrate.add_argument(
        "--apply",
        action="store_true",
        help="Apply the transaction; without this flag the command is read-only.",
    )

    rollback = subcommands.add_parser(
        "rollback-legacy-owner",
        help="Rollback exactly the rows named by an applied migration receipt.",
    )
    rollback.add_argument("--receipt", required=True)
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)
    settings = Settings.from_env(project_root)
    container = build_container(settings=settings)

    try:
        if args.command == "create-account":
            password = _password_from_input(
                password_stdin=bool(args.password_stdin)
            )
            account = container.authentication.create_account(
                username=args.username,
                display_name=args.display_name,
                password=password,
                role=args.role,
            )
            _print({"account": account, "created": True})
            return 0
        if args.command == "list-accounts":
            _print(
                {
                    "accounts": (
                        container.authentication.list_accounts_from_trusted_host()
                    )
                }
            )
            return 0
        if args.command == "migrate-legacy-owner":
            source = args.source or settings.persona_local_owner_id
            result = (
                container.legacy_owner_migration.apply(
                    source_owner_id=source,
                    target_username=args.target_username,
                )
                if args.apply
                else container.legacy_owner_migration.preview(
                    source_owner_id=source,
                    target_username=args.target_username,
                )
            )
            _print(result)
            return 0
        if args.command == "rollback-legacy-owner":
            _print(
                container.legacy_owner_migration.rollback(args.receipt)
            )
            return 0
    except (KeyError, PermissionError, ValueError) as exc:
        print(f"PersonaOS admin error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
