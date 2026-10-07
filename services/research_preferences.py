"""Account-scoped report presentation settings, separate from saved evidence."""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy

from sqlalchemy import update

from database.research_db import ResearchReportPreferences
from services.scanner_research_service import encoded, write_guard

VERSION = "research-report-preferences-v1"
MAX_BODY_BYTES = 8192
MAX_REVISION = 2_147_483_647
METRIC_ID = re.compile(r"[a-z][a-z0-9_]{0,127}")
SECTIONS = frozenset(
    {"annual", "sortino", "prices", "all_statistics", "engine_records", "drawdowns"}
)


class PreferencesConflict(ValueError):
    def __init__(self, current):
        super().__init__(
            "Report preferences changed in another session. Review the latest settings and save again."
        )
        self.current = current


def default_preferences():
    return {
        "headline_metrics": [
            "account_net_return_pct",
            "account_net_pnl",
            "account_max_drawdown_pct",
            "account_sharpe_ratio",
            "account_win_rate_pct",
            "account_closed_trades",
        ],
        "statistic_metrics": [
            "account_initial_capital",
            "account_final_equity",
            "account_annualized_return_pct",
            "account_sharpe_ratio",
            "account_sortino_ratio",
            "account_annualized_volatility_pct",
            "account_profit_factor",
            "account_trade_expectancy",
        ],
        "performance_view": "return",
        "log_equity": False,
        "rolling_window": 21,
        "expanded_sections": [],
    }


def _owner(owner):
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 255:
        raise ValueError("Supply a valid app account")
    return owner


def _ids(value, minimum, maximum, *, sections=False):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(
            f"Choose {minimum}–{maximum} report {'sections' if sections else 'metrics'}"
        )
    if any(
        not isinstance(item, str)
        or (item not in SECTIONS if sections else METRIC_ID.fullmatch(item) is None)
        for item in value
    ):
        raise ValueError(f"Invalid report {'section' if sections else 'metric'}")
    if len(set(value)) != len(value):
        raise ValueError("Report choices must not contain duplicates")


def _changes(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - set(default_preferences()):
        raise ValueError("Supply supported report preference changes")
    for field, value in changes.items():
        if field == "headline_metrics":
            _ids(value, 1, 6)
        elif field == "statistic_metrics":
            _ids(value, 0, 24)
        elif field == "expanded_sections":
            _ids(value, 0, len(SECTIONS), sections=True)
        elif field == "performance_view":
            if not isinstance(value, str) or value not in ("return", "equity"):
                raise ValueError("Choose return or equity for the performance chart")
        elif field == "log_equity":
            if type(value) is not bool:
                raise ValueError("Log equity must be true or false")
        elif field == "rolling_window":
            if type(value) is not int or value not in (21, 63, 126):
                raise ValueError("Choose a 21, 63 or 126-session rolling window")
    return deepcopy(changes)


def _receipt(row):
    if row is None:
        return {
            "version": VERSION,
            "revision": 0,
            "preferences": default_preferences(),
            "updated_at": None,
        }
    document = json.loads(row.preferences)
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise ValueError("This report preference version is not supported")
    settings = document.get("preferences")
    if not isinstance(settings, dict) or set(settings) != set(default_preferences()):
        raise ValueError("Saved report preferences are incomplete")
    return {
        "version": VERSION,
        "revision": row.revision,
        "preferences": _changes(settings),
        "updated_at": row.updated_at,
    }


def get_preferences(store, owner):
    with store.sessions() as db:
        return _receipt(db.get(ResearchReportPreferences, _owner(owner)))


def update_preferences(store, owner, data):
    owner = _owner(owner)
    if not isinstance(data, dict) or set(data) != {"revision", "changes"}:
        raise ValueError("Supply the saved revision and report preference changes")
    revision = data["revision"]
    if type(revision) is not int or not 0 <= revision < MAX_REVISION:
        raise ValueError("Supply a valid saved report preference revision")
    changes = _changes(data["changes"])
    with store.sessions.begin() as db:
        # The existing metadata write fence makes the first-row read/insert atomic
        # as well as excluding backup/restore. A second initial writer sees revision 1.
        write_guard(db)
        row = db.get(ResearchReportPreferences, owner)
        current = _receipt(row)
        if current["revision"] != revision:
            raise PreferencesConflict(current)
        settings = {**current["preferences"], **changes}
        document = encoded({"version": VERSION, "preferences": settings}).decode()
        now = time.time()
        if row is None:
            db.add(
                ResearchReportPreferences(
                    owner=owner, revision=1, preferences=document, updated_at=now
                )
            )
        else:
            saved = db.execute(
                update(ResearchReportPreferences)
                .where(
                    ResearchReportPreferences.owner == owner,
                    ResearchReportPreferences.revision == revision,
                )
                .values(revision=revision + 1, preferences=document, updated_at=now)
            )
            if saved.rowcount != 1:
                db.expire_all()
                raise PreferencesConflict(_receipt(db.get(ResearchReportPreferences, owner)))
        result = {
            "version": VERSION,
            "revision": revision + 1,
            "preferences": settings,
            "updated_at": now,
        }
    return result
