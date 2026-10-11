"""Explicit unsupported asset declarations fail before native NSE preparation."""

import pytest

from research.signals import normalize_csv


@pytest.mark.parametrize("exchange", ["NSE", "nse", "NSE_EQ", "NSE CM", ""])
@pytest.mark.parametrize("asset", ["EQ", "Equity", "cash-equity", "STOCK", ""])
def test_supported_declarations_retain_the_plain_chartink_signal_contract(exchange, asset):
    plain = normalize_csv(b"Date,Symbol\n2026-01-21,HINDZINC\n")
    declared = normalize_csv(
        f"Date,Symbol,Trading Exchange,Asset Class\n2026-01-21,HINDZINC,{exchange},{asset}\n".encode()
    )
    assert declared == plain


@pytest.mark.parametrize("exchange", ["BSE", "NFO", "NSE_INDEX", "NASDAQ", "N/S/E"])
def test_foreign_or_derivative_exchange_is_not_silently_reinterpreted_as_nse(exchange):
    with pytest.raises(ValueError, match="Row 2: exchange.*unsupported.*NSE cash-equity"):
        normalize_csv(f"Date,Symbol,Exchange\n2026-01-21,HINDZINC,{exchange}\n".encode())


@pytest.mark.parametrize("asset", ["Futures", "Option", "Index", "Crypto", "ETF", "Equity Future"])
@pytest.mark.parametrize("column", ["Asset Class", "AssetType", "Instrument Type"])
def test_unsupported_asset_fails_intake_even_if_symbol_matches_nse_equity(asset, column):
    with pytest.raises(ValueError, match="Row 2: asset class.*unsupported.*NSE cash-equity"):
        normalize_csv(f"Date,Symbol,{column}\n2026-01-21,HINDZINC,{asset}\n".encode())


def test_duplicate_scope_aliases_are_rejected_and_no_asset_guessed_from_descriptions():
    with pytest.raises(ValueError, match="Ambiguous duplicate column: exchange"):
        normalize_csv(b"Date,Symbol,Exchange,TradingExchange\n2026-01-21,HINDZINC,NSE,NFO\n")
    with pytest.raises(ValueError, match="Ambiguous duplicate column: asset_class"):
        normalize_csv(b"Date,Symbol,AssetClass,InstrumentType\n2026-01-21,HINDZINC,EQ,FUT\n")
    descriptive = normalize_csv(
        b"Date,Symbol,Company Name,Sector,Market Cap Name\n2026-01-21,HINDZINC,Futures Ltd,Metals,Largecap\n"
    )
    assert descriptive["signals"][0]["symbol"] == "HINDZINC"
    assert descriptive["signals"][0]["sector"] == "Metals"


def test_unsupported_declaration_in_duplicate_row_is_not_dropped_silently():
    with pytest.raises(ValueError, match="Row 3: exchange"):
        normalize_csv(b"Date,Symbol,Exchange\n2026-01-21,HINDZINC,NSE\n2026-01-21,HINDZINC,NFO\n")
