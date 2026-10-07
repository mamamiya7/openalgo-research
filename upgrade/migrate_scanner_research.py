"""Idempotently initialize the isolated Scanner Research metadata schema."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect

from database.research_db import Base, ResearchStore
from utils.logging import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    store = ResearchStore()
    try:
        if args.status:
            if not (store.root / "research.db").exists():
                logger.info("Scanner Research schema needs initialization")
            else:
                missing = set(Base.metadata.tables) - set(inspect(store.engine).get_table_names())
                logger.info("Scanner Research missing tables: %s", sorted(missing))
        else:
            store.initialize()
            logger.info("Scanner Research schema is ready")
    finally:
        store.close()


if __name__ == "__main__":
    main()
