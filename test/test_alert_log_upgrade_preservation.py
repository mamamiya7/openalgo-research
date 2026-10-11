"""The additive alert-log migration preserves populated account and log rows."""

import importlib.util
import sys
from pathlib import Path

from sqlalchemy import inspect, text

from database.engine_factory import create_db_engine


def test_populated_upgrade_is_idempotent_and_status_is_read_only(tmp_path):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "upgrade"))
    spec = importlib.util.spec_from_file_location(
        "alert_log_upgrade_fixture", root / "upgrade/migrate_alert_log.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_db_engine("sqlite:///" + str(tmp_path / "existing.db"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE account_fixture (name TEXT PRIMARY KEY, value TEXT)")
            )
            connection.execute(text("INSERT INTO account_fixture VALUES ('retained', 'unchanged')"))
        assert not migration.status(engine)
        assert "alert_log" not in inspect(engine).get_table_names()
        assert migration.create_log_table(engine)
        assert migration.create_indexes(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO alert_log (user_id, alert_id, message) VALUES ('fixture', 'one', 'retained')"
                )
            )
        assert migration.create_log_table(engine)
        assert migration.create_indexes(engine)
        assert migration.status(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM account_fixture")).all() == [
                ("retained", "unchanged")
            ]
            assert connection.execute(
                text("SELECT user_id, alert_id, message FROM alert_log")
            ).all() == [("fixture", "one", "retained")]
    finally:
        engine.dispose()
