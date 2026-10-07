"""Missing native specifications produce exact eligibility gaps, never invented ticks."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_portfolio_coverage import evidence
from test_vectorbt_portfolio import signal, snapshot, strategy

from services.research_instruments import native_instruments, prepare_nautilus_instruments


@pytest.fixture
def master(monkeypatch):
    from database import symbol

    engine = create_engine("sqlite:///:memory:")
    symbol.SymToken.__table__.create(engine)
    monkeypatch.setattr(symbol, "engine", engine)
    with Session(engine) as db:
        db.add(
            symbol.SymToken(
                symbol="AAA", brsymbol="AAA-EQ", exchange="NSE", lotsize=1, tick_size=0.05
            )
        )
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def test_native_metadata_missing_symbol_requires_explicit_partial_policy(master):
    with pytest.raises(ValueError, match="BBB"):
        native_instruments(["AAA", "BBB"])
    found = native_instruments(["AAA", "BBB"], allow_missing=True)
    assert set(found) == {"AAA"}
    assert found["AAA"]["tick_size"] == "0.05"


def test_unavailable_instrument_excludes_only_affected_rows_and_retains_original(master):
    original = evidence([strategy("a"), strategy("b", signals=[signal("BBB")])], snapshot())
    frozen = prepare_nautilus_instruments(original)
    assert frozen["snapshot"]["instrument_gaps"] == ["BBB"]
    assert frozen["signal_coverage"]["eligible_signals"] == 1
    assert frozen["signal_coverage"]["excluded_signals"] == 1
    assert (
        "specifications unavailable" in frozen["strategies"][1]["signals"][0]["research_exclusion"]
    )
    assert "research_exclusion" not in original["strategies"][1]["signals"][0]
    assert frozen["signals"] == original["signals"]
    assert frozen["snapshot"]["bars"] == original["snapshot"]["bars"]


def test_no_available_specifications_still_requires_master_refresh(master):
    original = evidence([strategy("b", signals=[signal("BBB")])], snapshot())
    with pytest.raises(ValueError, match="Refresh master contracts"):
        prepare_nautilus_instruments(original)


@pytest.mark.parametrize("lotsize,tick", [(2, 0.05), (1, 0), (1, -0.05)])
def test_invalid_native_specifications_are_not_ignored(master, lotsize, tick):
    from database.symbol import SymToken

    with Session(master) as db:
        db.add(
            SymToken(symbol="BAD", brsymbol="BAD", exchange="NSE", lotsize=lotsize, tick_size=tick)
        )
        db.commit()
    with pytest.raises(ValueError, match="BAD"):
        native_instruments(["AAA", "BAD"], allow_missing=True)
