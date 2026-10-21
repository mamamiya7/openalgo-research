"""Freeze exact native volume provenance beside an already retained OHLC context."""

from pathlib import Path

from research.portfolio import fingerprint
from research.portfolio_execution_features import prepare as prepare_features
from services.research_historify import ARCHIVE_READ_BATCH_SIZE, NativeHistorifyArchive


def prepare(store, evidence, *, cancelled):
    context = evidence["automatic_stock_market_context"]
    if evidence.get("portfolio_execution_features") is not None:
        return prepare_features(evidence, context)
    volume_records = {}
    required = any(
        row.get("execution_policy", {}).get("capacity_mode") == "prior20_broker_reported"
        for row in evidence["strategies"]
    )
    if required:
        from services.research_sources import acquisition_paths
        from services.research_volume_provenance import NativeVolumeProvenance

        target = Path(acquisition_paths(store)[0]).resolve()
        archive = NativeHistorifyArchive(target, interval="D", exchange="NSE")
        provenance = NativeVolumeProvenance(
            store.root / "volume-provenance", archive_id=fingerprint(str(target))
        )
        symbols = sorted(context["series"])
        for offset in range(0, len(symbols), ARCHIVE_READ_BATCH_SIZE):
            cancelled()
            batch = symbols[offset : offset + ARCHIVE_READ_BATCH_SIZE]
            windows = []
            for symbol in batch:
                dates = context["series"][symbol]["evidence"]["required_dates"]
                windows.append((symbol, dates[0], dates[-1]))
            reads = archive.read_many(windows, check=cancelled)
            for (symbol, first, last), rows in zip(windows, reads, strict=True):
                captured = provenance.lookup(symbol, first, last, rows)
                bars = context["series"][symbol]["evidence"]["bars"]
                volume_records[symbol] = {
                    day: record
                    for day, record in captured.items()
                    if day in bars
                    and all(
                        record["candle"][key] == bars[day][key]
                        for key in ("timestamp", "open", "high", "low", "close")
                    )
                }
            cancelled()
    prepared = prepare_features(evidence, context, volume_records)
    if required and prepared["portfolio_execution_features"]["counts"]["capacity_available"] == 0:
        raise ValueError(
            "Broker volume could not support the selected participation limit. "
            "The current model needs 20 completed NSE equity sessions with reviewed volume provenance. "
            "Turn off ‘Use broker-volume participation limit’ to keep correlation limits, "
            "or reconnect a supported broker and retry."
        )
    return prepared
