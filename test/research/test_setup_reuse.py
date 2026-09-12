"""Chosen rules can use explicit new CSVs without changing saved evidence."""

import copy
import json

import pytest
from sqlalchemy import event, func, select

from database.research_db import ResearchJob, ResearchSource, ResearchSourceReceipt, ResearchStore
from research.portfolio import normalize
from services import research_library as library
from services import research_portfolio as portfolio
from services import scanner_research_service as service
from services.research_setup_reuse import reuse_draft

OWNER = "reuse-owner"


@pytest.fixture
def store(tmp_path):
    value = ResearchStore(tmp_path / "research")
    value.initialize()
    try:
        yield value
    finally:
        value.close()


def source(store, *, month=1, timed=False, owner=OWNER, filename="Signals.csv", kind="broker"):
    header = "Timestamp,Symbol" if timed else "Date,Symbol"
    clock = "T10:15:00+05:30" if timed else ""
    raw = (
        header + "\n" + "".join(f"2026-{month:02d}-{day:02d}{clock},AAA\n" for day in range(5, 15))
    )
    return service.create_source(
        store, owner, raw.encode(), kind, defer_preparation=True, filename=filename
    )


def chosen(store, *, count=2):
    original = source(store, filename="Original.csv")
    value = library.fresh_draft()
    value["equalWeights"] = False
    value["portfolio"] = normalize(
        {
            "name": "Chosen breakout",
            "capital": 250000,
            "engine": "vectorbt",
            "date_from": "2026-01-06",
            "date_to": "2026-01-12",
            "validation": {"mode": "reserve", "train_pct": 70},
            "strategies": [
                {
                    "id": f"strategy-{index}",
                    "name": f"Breakout {index}",
                    "source_id": original["id"],
                    "allocation_pct": 100 / count,
                    "config": {
                        "order_size_pct": 60,
                        "hold_sessions": 3,
                        "cost_bps": 7,
                        "target_pct": 10 + index,
                        "stop_pct": 3,
                        "trailing_enabled": True,
                        "trailing_pct": 2,
                    },
                }
                for index in range(count)
            ],
        }
    )
    return library.normalize_draft(store, OWNER, value)


def test_replace_one_of_eight_preserves_rules_account_and_unaffected_strategies(store):
    original = chosen(store, count=8)
    frozen = copy.deepcopy(original)
    newer = source(store, month=2, filename="February.csv")
    result = reuse_draft(store, OWNER, original, replacements={"strategy-3": newer["id"]})
    updated = result["draft"]
    assert original == frozen
    expected = copy.deepcopy(frozen)
    expected["portfolio"]["strategies"][3]["source_id"] = newer["id"]
    expected["portfolio"].pop("date_from")
    expected["portfolio"].pop("date_to")
    expected["sources"][newer["id"]] = service.source_receipt(store, OWNER, newer["id"])
    assert updated == expected
    assert result["changes"]["rules_unchanged"] is True
    change = result["changes"]["sources"]
    assert len(change) == 1 and change[0]["strategy_id"] == "strategy-3"
    assert change[0]["before"]["filename"] == "Original.csv"
    assert change[0]["after"] == {
        "source_id": newer["id"],
        "filename": "February.csv",
        "signal_count": 10,
        "symbol_count": 1,
        "date_from": "2026-02-05",
        "date_to": "2026-02-14",
    }
    assert result["changes"]["fields"] == [
        {"key": "date_from", "before": "2026-01-06", "after": None},
        {"key": "date_to", "before": "2026-01-12", "after": None},
    ]


@pytest.mark.parametrize("timed,interval", [(False, "D"), (True, "1m")])
def test_native_preflight_recomputes_interval_dates_and_reservation_from_new_csv(
    store, timed, interval
):
    original = chosen(store, count=1)
    before = portfolio.preview(store, OWNER, library.portfolio_payload(original))
    replacement = source(store, month=2, timed=timed)
    updated = reuse_draft(store, OWNER, original, replacements={"strategy-0": replacement["id"]})[
        "draft"
    ]
    after = portfolio.preview(store, OWNER, library.portfolio_payload(updated))
    assert before["interval"] == "D"
    assert after["interval"] == interval
    assert after["receipt"]["signal_count"] == 10
    assert after["receipt"]["date_from"] == "2026-02-05"
    assert after["receipt"]["date_to"] == "2026-02-14"
    assert after["period_plan"]["mode"] == "reserve"
    assert after["period_plan"]["train_pct"] == 70
    assert after["period_plan"]["selection"] == {"from": "2026-02-05", "to": "2026-02-11"}
    assert after["period_plan"]["evaluation"] == {"from": "2026-02-12", "to": "2026-02-14"}
    assert (
        before["period_plan"]["signal_dates_sha256"] != after["period_plan"]["signal_dates_sha256"]
    )
    assert set(updated["sources"]) == {replacement["id"]}
    assert "period_plan" not in updated["portfolio"]


def test_explicit_same_source_keeps_existing_date_filter_and_has_no_false_diff(store):
    original = chosen(store)
    identifier = original["portfolio"]["strategies"][0]["source_id"]
    result = reuse_draft(store, OWNER, original, replacements={"strategy-0": identifier})
    assert result["draft"] == original
    assert result["changes"] == {"sources": [], "fields": [], "rules_unchanged": True}


@pytest.mark.parametrize("mode", ["backtest", "optimize"])
def test_refine_search_keeps_chosen_values_and_never_restores_old_search_ranges(store, mode):
    original = chosen(store)
    original["portfolio"]["strategies"][0]["search"] = {
        "target_pct": {"min": 90, "max": 100, "step": 5}
    }
    original["portfolio"]["optimization"] = copy.deepcopy(original["optimization"])
    frozen = copy.deepcopy(original)
    result = reuse_draft(store, OWNER, original, mode=mode)
    updated = result["draft"]
    assert updated["optimizing"] is (mode == "optimize")
    assert updated["optimization"] == frozen["optimization"]
    for before, after in zip(
        frozen["portfolio"]["strategies"], updated["portfolio"]["strategies"], strict=True
    ):
        assert after == {**before, "search": {}}
    assert updated["portfolio"]["validation"] == frozen["portfolio"]["validation"]
    assert updated["portfolio"]["date_to"] == frozen["portfolio"]["date_to"]
    assert "optimization" not in updated["portfolio"]
    assert any(row["key"] == "search" for row in result["changes"]["fields"])
    assert original == frozen
    if mode == "optimize":
        # Native preflight still computes data needs without guessing search axes;
        # the builder asks the user to choose ranges before its Run action.
        preview = portfolio.preview(store, OWNER, library.portfolio_payload(updated))
        assert all(row["search"] == {} for row in preview["portfolio"]["strategies"])


def test_unknown_strategy_wrong_owner_and_missing_source_leave_original_unchanged(store):
    original = chosen(store)
    frozen = copy.deepcopy(original)
    foreign = source(store, owner="someone-else", month=2)
    for replacements, error, message in (
        ({"unknown-strategy": foreign["id"]}, ValueError, "Choose a strategy"),
        ({"strategy-0": foreign["id"]}, LookupError, "Source not found"),
        ({"strategy-0": "f" * 32}, LookupError, "Source not found"),
    ):
        with pytest.raises(error, match=message):
            reuse_draft(store, OWNER, original, replacements=replacements)
        assert original == frozen
    with pytest.raises(LookupError, match="Source not found"):
        reuse_draft(store, "someone-else", original)


@pytest.mark.parametrize(
    "replacements", [[], {str(i): "f" * 32 for i in range(9)}, {"strategy-0": "bad"}]
)
def test_invalid_replacement_mapping_rejected_before_reading_sources(
    store, monkeypatch, replacements
):
    original = chosen(store)
    monkeypatch.setattr(
        service, "source_receipt", lambda *a: pytest.fail("Invalid mapping read sources")
    )
    with pytest.raises(ValueError):
        reuse_draft(store, OWNER, original, replacements=replacements)


@pytest.mark.parametrize("invalid", ["portfolio", "synthetic", "receipt"])
def test_combined_portfolio_demo_and_invalid_receipt_cannot_replace_signal_csv(store, invalid):
    original = chosen(store)
    replacement = source(store, month=2, kind="fixture" if invalid == "synthetic" else "broker")
    if invalid != "synthetic":
        with store.sessions.begin() as db:
            row = db.get(ResearchSourceReceipt, replacement["id"])
            receipt = json.loads(row.receipt)
            if invalid == "portfolio":
                receipt["receipt"]["input_type"] = "portfolio"
            else:
                receipt["receipt"]["signal_count"] = 0
            row.receipt = service.encoded(receipt).decode()
    with pytest.raises(ValueError, match="portfolio|synthetic|receipt"):
        reuse_draft(store, OWNER, original, replacements={"strategy-0": replacement["id"]})


def test_display_receipts_use_owned_counts_and_dates_and_legacy_sources_need_no_guessed_identity(
    store,
):
    original = chosen(store)
    original_id = original["portfolio"]["strategies"][0]["source_id"]
    original["sources"][original_id]["receipt"].update(signal_count=999, date_to="2099-01-01")
    replacement = source(store, month=2)
    with store.sessions.begin() as db:
        row = db.get(ResearchSourceReceipt, replacement["id"])
        receipt = json.loads(row.receipt)
        receipt["receipt"].pop("signals_sha256")
        receipt["receipt"].pop("original_csv_sha256")
        row.receipt = service.encoded(receipt).decode()
    result = reuse_draft(store, OWNER, original, replacements={"strategy-0": replacement["id"]})
    assert result["changes"]["sources"][0]["before"]["signal_count"] == 10
    assert result["changes"]["sources"][0]["before"]["date_to"] == "2026-01-14"
    assert result["draft"]["portfolio"]["strategies"][0]["source_id"] == replacement["id"]


def test_repeated_preview_has_no_acquisition_writes_or_retained_connections(store, monkeypatch):
    original = chosen(store)
    replacement = source(store, month=2)
    with store.sessions() as db:
        source_count = db.scalar(select(func.count()).select_from(ResearchSource))
    for name in ("submit", "save_artifact", "source_for", "read_artifact"):
        monkeypatch.setattr(
            service, name, lambda *a, **kw: pytest.fail("Reuse must read receipts only")
        )
    monkeypatch.setattr(
        portfolio, "_prepare_prices", lambda *a, **kw: pytest.fail("Reuse must not acquire prices")
    )
    opened, closed = [], []

    def on_connect(*args):
        opened.append(1)

    def on_close(*args):
        closed.append(1)

    event.listen(store.engine, "connect", on_connect)
    event.listen(store.engine, "close", on_close)
    try:
        for _ in range(12):
            reuse_draft(store, OWNER, original, replacements={"strategy-0": replacement["id"]})
            with pytest.raises(LookupError):
                reuse_draft(store, OWNER, original, replacements={"strategy-0": "f" * 32})
            assert len(opened) == len(closed)
        with store.sessions() as db:
            assert db.scalar(select(func.count()).select_from(ResearchSource)) == source_count
            assert db.scalar(select(func.count()).select_from(ResearchJob)) == 0
        assert len(opened) == len(closed)
        with store.sessions.begin() as db:
            db.get(ResearchSourceReceipt, replacement["id"]).receipt = "invalid json"
        with pytest.raises(json.JSONDecodeError):
            reuse_draft(store, OWNER, original, replacements={"strategy-0": replacement["id"]})
        assert len(opened) == len(closed)
    finally:
        event.remove(store.engine, "connect", on_connect)
        event.remove(store.engine, "close", on_close)
