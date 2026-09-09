"""Bounded native archive reads and conservative cached-price admission."""

import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def native_historify_read(symbol, first, last, expected_path, *, interval="D"):
    from database import historify_db

    target = Path(expected_path).resolve()
    if Path(historify_db.get_db_path()).resolve() != target:
        raise ValueError(
            "Historify was initialized with a different archive; restart with matching configuration"
        )
    if interval == "D":
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        if end < start or (end - start).days > 3700:
            raise ValueError("Historify read exceeds the bounded daily range")
        lower = int(datetime.combine(start, time.min, IST).timestamp())
        upper = int(datetime.combine(end + timedelta(days=1), time.min, IST).timestamp())
        limit = 3000
    elif interval == "1m":
        start, end = datetime.fromisoformat(first), datetime.fromisoformat(last)
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or end < start
            or end - start > timedelta(days=5)
        ):
            raise ValueError("Minute archive reads require aware timestamps within five days")
        lower, upper, limit = int(start.timestamp()), int(end.timestamp()) + 1, 10000
    else:
        raise ValueError("Research archive supports daily or one-minute prices")
    if not target.exists():
        return []
    # Match native connection mode/retries. A short connection avoids keeping a
    # DuckDB file lock while the external broker request is in flight.
    with historify_db.get_connection() as connection:
        if not connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name='market_data'"
        ).fetchone():
            return []
        records = connection.execute(
            "SELECT timestamp, open, high, low, close, volume, oi FROM market_data "
            "WHERE symbol=? AND exchange=? AND interval=? AND timestamp>=? AND timestamp<? ORDER BY timestamp LIMIT ?",
            [symbol.upper(), "NSE", interval, lower, upper, limit + 1],
        ).fetchall()
    if len(records) > limit:
        raise ValueError("Historify rows exceed the bounded archive read")
    return [
        dict(zip(("timestamp", "open", "high", "low", "close", "volume", "oi"), row, strict=True))
        for row in records
    ]


def native_historify_write(symbol, rows, expected_path, *, interval="1m"):
    import pandas as pd

    from database import historify_db

    if interval not in {"D", "1m"} or len(rows) > 10000:
        raise ValueError("Unsupported or oversized research archive write")
    if Path(historify_db.get_db_path()).resolve() != Path(expected_path).resolve():
        raise ValueError("Historify was initialized with a different archive")
    if not rows:
        return 0
    historify_db.init_database()
    count = historify_db.upsert_market_data(pd.DataFrame(rows), symbol, "NSE", interval)
    if count != len(rows):
        raise ValueError("Historify did not acknowledge all downloaded rows")
    return count


def checked_archive_rows(rows, symbol, first, last, reference):
    from services.research_acquisition import trading_date

    if not isinstance(rows, list) or len(rows) > 3000:
        raise ValueError("Historify reader must return at most 3000 daily rows")
    admitted, adjusted, observations, rejected, seen = {}, {}, [], [], set()
    for row in rows:
        day = first
        try:
            day = trading_date(row["timestamp"])
            if day in seen:
                admitted.pop(day, None)
                adjusted.pop(day, None)
                raise ValueError("duplicate_timestamp")
            seen.add(day)
            candle = {key: float(row[key]) for key in ("open", "high", "low", "close")}
            volume = float(row.get("volume", 0))
            if (
                not first <= day <= last
                or not math.isfinite(volume)
                or volume < 0
                or not all(math.isfinite(value) and value > 0 for value in candle.values())
                or not candle["low"]
                <= min(candle["open"], candle["close"])
                <= max(candle["open"], candle["close"])
                <= candle["high"]
            ):
                raise ValueError("malformed_archive_candle")
            observations.append(
                {"date": day, "source_timestamp": row["timestamp"], **candle, "volume": volume}
            )
            official = reference.get("raw_bars", {}).get(symbol, {}).get(day)
            identity = (
                reference.get("provenance", {})
                .get("symbol_identities", {})
                .get(symbol, {})
                .get(day)
            )
            if (
                not official
                or not identity
                or day not in reference.get("bars", {}).get(symbol, {})
                or not all(
                    math.isclose(candle[key], official[key], rel_tol=2e-5, abs_tol=0.02)
                    for key in candle
                )
            ):
                raise ValueError("unverified_identity_or_basis")
            admitted[day] = candle
            adjusted[day] = dict(candle)
            for action in reference["provenance"].get("actions", {}).get(symbol, []):
                if day < action["ex_date"] <= reference["provenance"].get("actions_as_of", last):
                    adjusted[day] = {
                        key: value * action["backward_price_factor"]
                        for key, value in adjusted[day].items()
                    }
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            reason = str(error) if isinstance(error, ValueError) else "malformed_archive_candle"
            if reason not in {
                "duplicate_timestamp",
                "malformed_archive_candle",
                "unverified_identity_or_basis",
            }:
                reason = "malformed_archive_candle"
            rejected.append(
                {
                    "kind": "quarantined"
                    if reason == "unverified_identity_or_basis"
                    else "malformed",
                    "reason": reason,
                    "symbol": symbol,
                    "date": day,
                }
            )
    return admitted, adjusted, observations, rejected
