"""Repair known NSE calendar seeds without replacing customized entries."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Report changes without writing")
    args = parser.parse_args()
    from sqlalchemy import inspect

    from database.market_calendar_db import engine
    from database.research_calendar import repair_nse_calendar

    try:
        if (
            engine.url.get_backend_name() == "sqlite"
            and engine.url.database not in (None, ":memory:")
            and not Path(engine.url.database).exists()
        ):
            print("Fresh installation: native startup will initialize the reviewed calendar.")
            return 0
        if not inspect(engine).has_table("market_holidays"):
            print("Fresh installation: native startup will initialize the reviewed calendar.")
            return 0
        changes = repair_nse_calendar(status=args.status)
        print(json.dumps({"status_only": args.status, "changes": changes}))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
