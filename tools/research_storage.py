"""Explicit local-only research maintenance CLI. No implicit live-store defaults."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.research_db import ResearchStore
from services.research_storage import backup_store, inspect_storage, prune_orphans, restore_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "backup", "prune"):
        command = commands.add_parser(name)
        command.add_argument(
            "--store", required=True, help="Explicit existing research data directory"
        )
        if name == "backup":
            command.add_argument(
                "--destination", required=True, help="New or empty backup directory"
            )
        if name == "prune":
            command.add_argument(
                "--apply",
                action="store_true",
                help="Delete eligible orphans; omitted means dry run",
            )
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--destination", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "restore":
            result = restore_store(arguments.backup, arguments.destination)
        else:
            root = Path(arguments.store).resolve()
            if not (root / "research.db").is_file():
                raise ValueError("Named research store does not exist")
            store = ResearchStore(root)
            try:
                if arguments.command == "inspect":
                    result = inspect_storage(store)
                elif arguments.command == "backup":
                    result = backup_store(store, arguments.destination)
                else:
                    result = prune_orphans(store, apply=arguments.apply)
            finally:
                store.close()
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, LookupError, SQLAlchemyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
