"""Strict deterministic scanner intake; invalid rows never disappear silently."""

import csv
import io
import re
from datetime import datetime, timedelta, timezone

MAX_SIGNALS = 25000
IST = timezone(timedelta(hours=5, minutes=30))


def signal_time(value):
    """CSV clocks without an offset are exchange-local; preserve observed seconds."""
    try:
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?",
            value,
        ):
            raise ValueError
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=IST)
        instant = instant.astimezone(IST)
        return instant.date().isoformat(), instant.isoformat()
    except (ValueError, TypeError) as exc:
        raise ValueError("Use an ISO timestamp or Date and Time in exchange time") from exc


def normalize_csv(content: bytes) -> dict:
    if not isinstance(content, bytes) or len(content) > 8 * 1024 * 1024:
        raise ValueError("CSV must be UTF-8 and at most 8 MiB")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        aliases = {
            "date": "date",
            "signaldate": "date",
            "scandate": "date",
            "timestamp": "timestamp",
            "datetime": "timestamp",
            "signaltime": "time",
            "time": "time",
            "symbol": "symbol",
            "ticker": "symbol",
            "nsesymbol": "symbol",
            "sector": "sector",
            "industry": "sector",
            "marketcapname": "marketcapname",
            "marketcap": "marketcapname",
        }
        columns = {}
        for name in reader.fieldnames or []:
            canonical = aliases.get(re.sub(r"[^a-z0-9]", "", name.lower()))
            if canonical in columns:
                raise ValueError(f"Ambiguous duplicate column: {canonical}")
            if canonical:
                columns[canonical] = name
        if "symbol" not in columns or not ({"date", "timestamp"} & columns.keys()):
            raise ValueError("CSV requires Date and Symbol (or Ticker) columns")
        signals, seen, duplicates, count = [], set(), 0, 0
        for count, row in enumerate(reader, 1):
            if count > MAX_SIGNALS:
                raise ValueError(f"At most {MAX_SIGNALS} input rows are supported")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Row {count + 1}: column count does not match header")
            raw_date = row[columns["date"]].strip() if "date" in columns else ""
            date = None
            timestamp = None
            for fmt, pattern in (
                ("%Y-%m-%d", r"\d{4}-\d{2}-\d{2}"),
                ("%d-%m-%Y", r"\d{2}-\d{2}-\d{4}"),
            ):
                if re.fullmatch(pattern, raw_date):
                    try:
                        date = datetime.strptime(raw_date, fmt).date().isoformat()
                    except ValueError:
                        pass
            raw_timestamp = row[columns["timestamp"]].strip() if "timestamp" in columns else ""
            raw_time = row[columns["time"]].strip() if "time" in columns else ""
            if raw_timestamp:
                if raw_date and not date:
                    raise ValueError(f"Row {count + 1}: Date is invalid")
                parsed_date, timestamp = signal_time(raw_timestamp)
                if date and date != parsed_date:
                    raise ValueError(f"Row {count + 1}: Date and Timestamp disagree")
                if raw_time and signal_time(f"{parsed_date}T{raw_time}")[1] != timestamp:
                    raise ValueError(f"Row {count + 1}: Time and Timestamp disagree")
                date = parsed_date
            elif raw_time and date:
                date, timestamp = signal_time(f"{date}T{raw_time}")
            elif "T" in raw_date or " " in raw_date:
                date, timestamp = signal_time(raw_date)
            elif "timestamp" in columns or "time" in columns:
                raise ValueError(f"Row {count + 1}: a signal time is missing")
            symbol = row[columns["symbol"]].strip().upper()
            if not date or not re.fullmatch(r"[A-Z0-9][A-Z0-9&.\-]{0,29}", symbol):
                raise ValueError(
                    f"Row {count + 1}: use ISO or DD-MM-YYYY date and an NSE equity symbol"
                )
            key = (timestamp or date, symbol)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            signals.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "row": count + 1,
                    **({"timestamp": timestamp} if timestamp else {}),
                    **{
                        key: row[columns[key]].strip() if key in columns else ""
                        for key in ("sector", "marketcapname")
                    },
                }
            )
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Malformed UTF-8 CSV") from exc
    if not signals:
        raise ValueError("CSV contains no dated signals")
    dates = [s["date"] for s in signals]
    if (datetime.fromisoformat(max(dates)) - datetime.fromisoformat(min(dates))).days > 3653:
        raise ValueError("Scanner Research supports at most ten years of signals")
    return {
        "signals": signals,
        "receipt": {
            "input_rows": count,
            "signal_count": len(signals),
            "duplicates_removed": duplicates,
            "date_from": min(dates),
            "date_to": max(dates),
            "symbol_count": len({s["symbol"] for s in signals}),
            "warnings": [
                "Exact timestamp-symbol duplicates removed; first CSV occurrence retained."
                if any(signal.get("timestamp") for signal in signals)
                else "Exact dated-symbol duplicates removed; first CSV occurrence retained."
            ]
            if duplicates
            else [],
        },
    }
