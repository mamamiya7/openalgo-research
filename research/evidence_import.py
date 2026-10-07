"""Offline, hash-pinned public NSE evidence conversion; no runtime cache imports.

This importer admits original exchange records, not provider-adjusted seed rows.
The reviewed bundle manifest is pinned in code; changing it requires a reviewed
importer version. No request can certify arbitrary uploaded OHLC using flags.
"""

import csv
import hashlib
import io
import json
import math
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

from research.data import validate_snapshot

IMPORT_VERSION = "nse-public-evidence-v2"
MANIFEST_SHA256 = "80c5df101379250db495ae872474535251d2af8cdc22acb66c009f03c50a226d"
DOCUMENTS_SHA256 = "8ff52f848ef4e65567f6a7cdf06c0e7b4d452454040f7acb77ed793b443fc51b"
CALENDAR_VERSION = "nse-cash-2025-2026-reviewed-2026-09-06"
CALENDAR_SOURCES = [
    f"https://nsearchives.nseindia.com/content/circulars/CMTR{x}.pdf"
    for x in (65587, 65729, 71775, 72260, 72349)
]
HOLIDAYS = {
    2025: set(
        "02-26 03-14 03-31 04-10 04-14 04-18 05-01 08-15 08-27 10-02 10-22 11-05 12-25".split()
    ),
    2026: set(
        "01-15 01-26 03-03 03-26 03-31 04-03 04-14 05-01 05-28 06-26 09-14 10-02 10-20 11-10 11-24 12-25".split()
    ),
}
SPECIAL = {"2025-02-01", "2025-10-21", "2026-02-01", "2026-11-08"}
PRICES = ("open", "high", "low", "close")
REVIEWED_RULES = {
    "version": "reviewed-2026-09-05",
    "securities": [
        {
            "symbol": "GROWWSLVR",
            "reviewed_from": "2025-12-05",
            "reviewed_through": "2026-09-04",
            "sources": [
                {
                    "url": "https://nsearchives.nseindia.com/content/circulars/CMPT72565.pdf",
                    "name": "CMPT72565-groww-split.pdf",
                    "sha256": "bb41ba39cf0730ebce2072b8afc5d2ee929bcc49842a5dcf0bbba8e963ba8fc9",
                }
            ],
            "limitations": [],
            "actions": [
                {
                    "kind": "unit_split",
                    "ex_date": "2026-02-06",
                    "backward_price_factor": 0.1,
                    "formula": "adjusted_OHLC = official_raw_OHLC / 10 for date < "
                    "2026-02-06; otherwise factor=1",
                    "sources": [
                        {
                            "url": "https://nsearchives.nseindia.com/content/circulars/CMPT72565.pdf",
                            "name": "CMPT72565-groww-split.pdf",
                            "sha256": "bb41ba39cf0730ebce2072b8afc5d2ee929bcc49842a5dcf0bbba8e963ba8fc9",
                        }
                    ],
                }
            ],
        },
        {
            "symbol": "IVZINGOLD",
            "reviewed_from": "2025-12-05",
            "reviewed_through": "2026-09-04",
            "sources": [
                {
                    "url": "https://nsearchives.nseindia.com/content/circulars/CML73909.pdf",
                    "name": "CML73909-invesco-split.pdf",
                    "sha256": "237ef2ba0c6545fce6f56b2605df85ae4b7d76b2f8db976e0ec2bff0120e096d",
                },
                {
                    "url": "https://nsearchives.nseindia.com/content/circulars/CMPT73946.pdf",
                    "name": "CMPT73946-invesco-split.pdf",
                    "sha256": "4dd3999f2aa7ccbcff7bd8a636abcdb26ea1fa3fcc7c26162ae719c9e8aada7a",
                },
            ],
            "limitations": [],
            "actions": [
                {
                    "kind": "unit_split",
                    "ex_date": "2026-04-30",
                    "backward_price_factor": 0.01,
                    "formula": "adjusted_OHLC = official_raw_OHLC / 100 for date < "
                    "2026-04-30; otherwise factor=1",
                    "sources": [
                        {
                            "url": "https://nsearchives.nseindia.com/content/circulars/CML73909.pdf",
                            "name": "CML73909-invesco-split.pdf",
                            "sha256": "237ef2ba0c6545fce6f56b2605df85ae4b7d76b2f8db976e0ec2bff0120e096d",
                        },
                        {
                            "url": "https://nsearchives.nseindia.com/content/circulars/CMPT73946.pdf",
                            "name": "CMPT73946-invesco-split.pdf",
                            "sha256": "4dd3999f2aa7ccbcff7bd8a636abcdb26ea1fa3fcc7c26162ae719c9e8aada7a",
                        },
                    ],
                }
            ],
        },
        {
            "symbol": "MAHAPEXLTD",
            "reviewed_from": "2025-12-05",
            "reviewed_through": "2026-09-04",
            "sources": [
                {
                    "url": "https://nsearchives.nseindia.com/corporate/MAHAPEXLTD_11032026174420_OutcomeBoard11032026.pdf",
                    "name": "MAHAPEXLTD-board-20260311.pdf",
                    "sha256": "69fdd2c244886b4446e1e8ea20446897785aae3adff91d577f7a618635d1dd6a",
                },
                {
                    "url": "https://nsearchives.nseindia.com/corporate/MAHAPEXLTD_30032026124412_Letter_of_offer_Disclosure-SD.pdf",
                    "name": "MAHAPEXLTD-letter-of-offer.pdf",
                    "sha256": "554511e4ca74756446c69ee36c34d1288df01363615009d6487dcec716507ea8",
                },
            ],
            "limitations": [
                "Rights price adjustment removes mechanical dilution using "
                "theoretical entitlement value. It does not model subscription "
                "cash, entitlement sale prices, exercise decisions, or actual "
                "issuance date.",
                "Do not treat rights as a free 2:1 split or apply a 0.5 multiplier.",
            ],
            "actions": [
                {
                    "kind": "rights_issue",
                    "ex_date": "2026-03-20",
                    "backward_price_factor": 0.5394011032308904,
                    "formula": "TERP=(126.90+1*10)/(1+1)=68.45; "
                    "adjustment=68.45/126.90=1369/2538; multiply official raw "
                    "OHLC before 2026-03-20 only.",
                    "sources": [
                        {
                            "url": "https://nsearchives.nseindia.com/corporate/MAHAPEXLTD_11032026174420_OutcomeBoard11032026.pdf",
                            "name": "MAHAPEXLTD-board-20260311.pdf",
                            "sha256": "69fdd2c244886b4446e1e8ea20446897785aae3adff91d577f7a618635d1dd6a",
                        },
                        {
                            "url": "https://nsearchives.nseindia.com/corporate/MAHAPEXLTD_30032026124412_Letter_of_offer_Disclosure-SD.pdf",
                            "name": "MAHAPEXLTD-letter-of-offer.pdf",
                            "sha256": "554511e4ca74756446c69ee36c34d1288df01363615009d6487dcec716507ea8",
                        },
                    ],
                    "cum_right_quote_source": {
                        "url": "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20260319_F_0000.csv.zip",
                        "sha256": "b0c85978871cb68dad3c3d0973139431a19bc18c4f8d312236ee4c223596f931",
                    },
                }
            ],
        },
        {
            "symbol": "CLCIND",
            "reviewed_from": "2025-12-05",
            "reviewed_through": "2026-09-04",
            "sources": [
                {
                    "url": "https://www.clcindia.com/assets/uploads/investors/NSE_trading_approval.pdf",
                    "name": "CLCIND-issuer-NSE-trading-approval.pdf",
                    "sha256": "0a2145c3b616b3114e53043e7d20559c9a902ea464799166948e3a8137b21603",
                },
                {
                    "name": "SURV74608",
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV74608.zip",
                    "sha256": "0ece7db126e13fa50a923448202b5ee145c2405861d3c46c8f0db9bcf18fcc00",
                },
                {
                    "name": "SURV75520",
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV75520.zip",
                    "sha256": "b6ec10f9ef3f67a66d369db0f0dbbb2855f58f2b65e3a61bcbc1c938a1a353c8",
                },
                {
                    "name": "SURV75990",
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV75990.pdf",
                    "sha256": "9ae80a2b5007f22b3abc1c56cd996c252f6e1b2a985b0b16bc8a5c6320978936",
                },
            ],
            "limitations": [
                "Use exchange-traded bars only from recommencement onward. Do not "
                "fabricate candles for non-traded sessions.",
                "A mirrored NSE/CML72500 text internally conflicts (95% reduction "
                "and1-for100); no cross-restructuring ratio is relied upon. The "
                "issuer-hosted NSE/LIST/1365 letter verifies restart without "
                "requiring this ratio.",
                "Verified restrictions include ASM IBC Stage I from 9 June "
                "through 2 August 2026 and GSM III from 28 August 2026. These "
                "restrict trading frequency and may require extra deposits; this "
                "daily simulator does not model those requirements.",
            ],
            "trading_restart": "2026-01-30",
            "actions": [],
        },
        {
            "symbol": "SWANDEF",
            "reviewed_from": "2025-12-05",
            "reviewed_through": "2026-09-04",
            "actions": [],
            "sources": [
                {
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV72551.zip",
                    "sha256": "9e731a00b6500de407a6c946f10544748e69c09fb14221b4bbe213f1f01be71f",
                    "name": "Stage I list effective 2026-02-01, Annexure IBC",
                },
                {
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV70074.pdf",
                    "sha256": "3ba26eabb8138eede6278a537280db1eaf5566b6900f33064847d1da5781da10",
                    "name": "ASM IBC Stage I once-weekly trading framework",
                },
                {
                    "name": "SURV72214",
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV72214.zip",
                    "sha256": "08156b923723e4e4c35932604f77263511829e40029c5d24589ade8b4eef14b7",
                },
                {
                    "name": "SURV73062",
                    "url": "https://nsearchives.nseindia.com/content/circulars/SURV73062.zip",
                    "sha256": "35f228cb4f914711b3eeb3d5048aca98dc7495f4cb0db3594be9395f0be3e375",
                },
            ],
            "limitations": [
                "ASM IBC Stage I weekly trading restriction is verified from 9 "
                "January through 1 March 2026. A market-wide open day does not "
                "establish a tradable session for this security.",
                "Use actual traded exchange records only. Repeated provider marks "
                "on days with no exchange trade cannot be used as entry or exit "
                "fills.",
                "All 144 observed exchange bars in the reviewed window match the "
                "provider OHLC basis; no price scaling or corporate-action factor "
                "is applied.",
            ],
        },
    ],
}


# Explicitly reviewed identity transitions: old ISIN/ex-date from clearing
# circulars, new ISIN from the pinned final archive for that exact ex-date.
for _rule in REVIEWED_RULES["securities"]:
    if _rule["symbol"] in ("GROWWSLVR", "IVZINGOLD"):
        _old, _new, _day = {
            "GROWWSLVR": ("INF666M01LA1", "INF666M01OF4", "2026-02-06"),
            "IVZINGOLD": ("INF205K01361", "INF205KA1BP1", "2026-04-30"),
        }[_rule["symbol"]]
        _rule["identity_mappings"] = [
            {
                "from_isin": _old,
                "to_isin": _new,
                "effective_date": _day,
                "sources": _rule["sources"],
                "policy": "verified-transition-boundary-gap-v1",
            }
        ]
del _rule, _old, _new, _day


def reviewed_sessions(first, last):
    """Reviewed exchange sessions; unknown years fail instead of inventing weekdays."""
    first, last = date.fromisoformat(first), date.fromisoformat(last)
    if first > last or set(range(first.year, last.year + 1)) - HOLIDAYS.keys():
        raise ValueError("Verified calendar supports 2025–2026 only")
    result = []
    day = first
    while day <= last:
        if day.isoformat() in SPECIAL or (
            day.weekday() < 5 and day.strftime("%m-%d") not in HOLIDAYS[day.year]
        ):
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def _checked_file(path, digest, limit):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError(f"Evidence file exceeds size bound: {path.name}")
    value = path.read_bytes()
    if hashlib.sha256(value).hexdigest() != digest:
        raise ValueError(f"Evidence checksum mismatch: {path.name}")
    return value


def parse_archive(payload, day, symbols):
    """Read one bounded archive; retain rejection evidence, never fabricate fills."""
    names = set(symbols)
    rows, findings = {}, []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if (
            len(members) != 1
            or not members[0].filename.lower().endswith(".csv")
            or members[0].file_size > 20_000_000
        ):
            raise ValueError("Unexpected NSE archive structure")
        with (
            archive.open(members[0]) as stream,
            io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text,
        ):
            reader = csv.DictReader(text)
            required = {
                "TradDt",
                "TckrSymb",
                "SctySrs",
                "ISIN",
                "OpnPric",
                "HghPric",
                "LwPric",
                "ClsPric",
                "TtlTradgVol",
                "TtlNbOfTxsExctd",
            }
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("NSE archive schema is not recognized")
            for row in reader:
                if row["TradDt"] != day:
                    raise ValueError("NSE archive date differs from its manifest")
                symbol = row["TckrSymb"]
                if symbol not in names:
                    continue
                kind = None
                if row["SctySrs"] not in {"EQ", "BE", "BZ"}:
                    kind = "excluded_series"
                elif not re.fullmatch(r"IN[A-Z0-9]{10}", row["ISIN"]):
                    kind = "unavailable_identity"
                try:
                    prices = dict(
                        zip(
                            PRICES,
                            [float(row[k]) for k in ("OpnPric", "HghPric", "LwPric", "ClsPric")],
                            strict=True,
                        )
                    )
                    volume, trades = float(row["TtlTradgVol"]), float(row["TtlNbOfTxsExctd"])
                    if not kind and (not math.isfinite(volume) or not math.isfinite(trades)):
                        kind = "malformed"
                    if not kind and (volume <= 0 or trades <= 0):
                        kind = "no_trade"
                    if not kind and (
                        not all(math.isfinite(v) and v > 0 for v in prices.values())
                        or not prices["low"]
                        <= min(prices["open"], prices["close"])
                        <= max(prices["open"], prices["close"])
                        <= prices["high"]
                    ):
                        kind = "malformed"
                except (TypeError, ValueError):
                    kind = kind or "malformed"
                if kind:
                    findings.append(
                        {"symbol": symbol, "date": day, "kind": kind, "series": row["SctySrs"]}
                    )
                    continue
                item = {
                    "bar": prices,
                    "isin": row["ISIN"],
                    "series": row["SctySrs"],
                    "volume": volume,
                    "trades": trades,
                }
                rows.setdefault(symbol, []).append(item)
    admitted = {}
    for symbol, candidates in rows.items():
        if len(candidates) != 1:
            findings.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "kind": "quarantined",
                    "reason": "ambiguous eligible records",
                }
            )
        else:
            admitted[symbol] = candidates[0]
    return admitted, findings


def public_snapshot(signals, evidence_dir, progress=None, *, extension_dir=None):
    """Convert the reviewed public seeds directory to a self-contained snapshot.

    progress(completed, total) may raise cancellation. Source paths are never
    retained in evidence. Calendar warmup is global; OHLC begins per symbol.
    """
    if not signals or len(signals) > 25000:
        raise ValueError("Supply 1–25000 normalized signals")
    root = Path(evidence_dir)
    manifest_bytes = _checked_file(root / "nse_daily" / "manifest.json", MANIFEST_SHA256, 1_000_000)
    manifest = json.loads(manifest_bytes)
    extension = _extension_manifest(extension_dir) if extension_dir else None
    if extension:
        baseline_end = manifest["coverage_end"]
        baseline_days = sorted(entry["date"] for entry in manifest["archives"].values())
        if extension.get("overlap_dates") != baseline_days[-2:]:
            raise ValueError("Official extension is missing pinned overlap evidence")
        for day in baseline_days[-2:]:
            name = day.replace("-", "") + ".zip"
            if (
                extension["archives"].get(name, {}).get("sha256")
                != manifest["archives"][name]["sha256"]
            ):
                raise ValueError("Official extension overlap conflict")
        for day in reviewed_sessions(baseline_end, extension["coverage_end"]):
            if day > baseline_end and day.replace("-", "") + ".zip" not in extension["archives"]:
                raise ValueError("Official extension lacks a required session archive")
        manifest["archives"].update(extension["archives"])
        manifest["coverage_end"] = extension["coverage_end"]
    documents = json.loads(
        _checked_file(root / "exchange_evidence" / "manifest.json", DOCUMENTS_SHA256, 1_000_000)
    )
    first, final_signal = min(s["date"] for s in signals), max(s["date"] for s in signals)
    # A full 252-session hold requires later acquisition beyond this seed's tail.
    # Preserve the actual archive end rather than invent unavailable future bars.
    last = manifest["coverage_end"]
    if first < manifest["coverage_start"] or final_signal > last:
        raise ValueError(
            f"Public evidence covers signals {manifest['coverage_start']} through {last}; select a covered CSV or acquire additional verified evidence"
        )
    calendar_start = max(date(2025, 1, 1), date.fromisoformat(first) - timedelta(days=30))
    all_sessions = reviewed_sessions(calendar_start.isoformat(), last)
    first_index = next(i for i, day in enumerate(all_sessions) if day >= first)
    sessions = all_sessions[max(0, first_index - 7) :]
    starts = {}
    for s in signals:
        starts[s["symbol"]] = min(starts.get(s["symbol"], s["date"]), s["date"])
    raw = {symbol: {} for symbol in starts}
    identities = {symbol: {} for symbol in starts}
    identity_anchors = {}
    findings, receipts, actions, limitations = [], [], {}, []
    rules = {rule["symbol"]: rule for rule in REVIEWED_RULES["securities"]}
    source_documents = {}
    for symbol in starts:
        if symbol not in rules:
            continue
        rule = rules[symbol]
        for receipt in rule["sources"]:
            name = receipt["url"].rsplit("/", 1)[-1]
            doc = documents["documents"].get(name, {})
            if doc.get("sha256") != receipt["sha256"]:
                raise ValueError("Reviewed action/restriction document identity mismatch")
            _checked_file(root / "exchange_evidence" / name, receipt["sha256"], 5_000_000)
            source_documents[name] = doc
        actions[symbol] = rule["actions"]
        limitations.extend(f"{symbol}: {text}" for text in rule.get("limitations", []))
    acquisition_first = min(first, baseline_days[-2]) if extension else first
    selected = [
        (name, entry)
        for name, entry in manifest["archives"].items()
        if acquisition_first <= entry["date"] <= last
    ]
    for index, (name, entry) in enumerate(selected):
        if progress:
            progress(index, len(selected))
        if name != entry["date"].replace("-", "") + ".zip":
            raise ValueError("Invalid archive filename")
        archive_path = (
            Path(extension_dir) / (entry["sha256"] + ".zip")
            if extension and entry["date"] > baseline_end
            else root / "nse_daily" / name
        )
        payload = _checked_file(archive_path, entry["sha256"], 5_000_000)
        admitted, rejected = parse_archive(payload, entry["date"], starts)
        findings.extend(f for f in rejected if f["date"] >= starts[f["symbol"]])
        receipts.append(
            {"date": entry["date"], "sha256": entry["sha256"], "source_url": entry["source_url"]}
        )
        for symbol, item in admitted.items():
            day = entry["date"]
            if day < starts[symbol]:
                if not extension or day <= baseline_end:
                    identity_anchors[symbol] = {
                        "date": day,
                        "isin": item["isin"],
                        "source_sha256": entry["sha256"],
                    }
                continue
            raw[symbol][day] = item["bar"]
            identities[symbol][day] = {k: item[k] for k in ("isin", "series", "volume", "trades")}
    bars = {}
    for symbol, series in raw.items():
        bars[symbol] = {}
        if not series:
            findings.append(
                {
                    "symbol": symbol,
                    "date": starts[symbol],
                    "kind": "unavailable_identity",
                    "reason": "No eligible traded security in the checked official archives",
                }
            )
        previous = None
        admitted_isin = identity_anchors.get(symbol, {}).get("isin")
        for day, candle in sorted(series.items()):
            adjusted = dict(candle)
            for action in actions.get(symbol, []):
                if day < action["ex_date"] <= last:
                    adjusted = {k: v * action["backward_price_factor"] for k, v in adjusted.items()}
            identity = identities[symbol][day]
            if extension and starts[symbol] > baseline_end and not identity_anchors.get(symbol):
                findings.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        "kind": "quarantined",
                        "reason": "missing_verified_overlap_identity",
                    }
                )
                continue
            if admitted_isin is None:
                admitted_isin = identity["isin"]
            if identity["isin"] != admitted_isin:
                mappings = rules.get(symbol, {}).get("identity_mappings", [])
                reviewed = next(
                    (
                        mapping
                        for mapping in mappings
                        if mapping.get("from_isin") == admitted_isin
                        and mapping.get("to_isin") == identity["isin"]
                        and mapping.get("effective_date") == day
                        and mapping.get("sources")
                        and all(
                            source.get("sha256")
                            in {doc["sha256"] for doc in source_documents.values()}
                            for source in mapping["sources"]
                        )
                    ),
                    None,
                )
                findings.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        "kind": "quarantined",
                        "reason": "reviewed_identity_boundary"
                        if reviewed
                        else "unreviewed_identity_segment",
                        "established_isin": admitted_isin,
                        "observed_isin": identity["isin"],
                        "mapping": reviewed,
                    }
                )
                if reviewed:
                    admitted_isin = identity["isin"]
                    previous = (day, adjusted)
                # Missing boundary prevents a funded old-security lot silently
                # crossing identity. Unreviewed B never establishes a new anchor.
                continue
            if previous:
                before, prior = previous
                ratio = adjusted["open"] / prior["close"]
                if ratio < 0.70 or ratio > 1.30:
                    findings.append(
                        {
                            "symbol": symbol,
                            "date": day,
                            "kind": "quarantined",
                            "reason": "suspicious_discontinuity",
                            "opening_ratio": ratio,
                        }
                    )
                    # The raw observation is retained for audit but cannot fill or mark.
                    previous = (day, adjusted)
                    continue
            bars[symbol][day] = adjusted
            previous = (day, adjusted)
        for day in sessions:
            if day < starts[symbol] or day in series:
                continue
            restricted = (symbol == "SWANDEF" and "2026-01-09" <= day <= "2026-03-01") or (
                symbol == "CLCIND" and ("2026-06-09" <= day <= "2026-08-02" or day >= "2026-08-28")
            )
            if restricted and any(entry["date"] == day for _, entry in selected):
                findings.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        "kind": "restricted_no_trade",
                        "reason": "No traded record in complete official daily archive during documented restriction",
                    }
                )
    if progress:
        progress(len(selected), len(selected))
    provenance = {
        "provider": "NSE final CM bhavcopy",
        "exchange": "NSE",
        "interval": "D",
        "timezone": "Asia/Kolkata",
        "adjustment_basis": "official-raw-with-reviewed-actions-v1",
        "calendar_basis": CALENDAR_VERSION,
        "calendar_sources": CALENDAR_SOURCES,
        "calendar_verified": True,
        "identity_verified": True,
        "identity_scope": "Admitted bars only; rejected raw segments do not establish instrument continuity",
        "identity_anchors": identity_anchors,
        "identity_mappings": {
            symbol: rules.get(symbol, {}).get("identity_mappings", []) for symbol in starts
        },
        "synthetic": False,
        "import_version": IMPORT_VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "extension_manifest_sha256": extension["manifest_sha256"] if extension else None,
        "documents_manifest_sha256": DOCUMENTS_SHA256,
        "source_receipts": receipts,
        "source_documents": source_documents,
        "symbol_identities": identities,
        "actions": actions,
        "actions_as_of": last,
        "quality_findings": findings,
        "limitations": limitations
        + [
            "Raw exchange prices have only the explicitly recorded reviewed action overlays. Unknown corporate actions, dividends, entitlement cashflows and liquidity constraints are not simulated.",
            "Scanner CSV membership is not evidence of point-in-time scanner selection or survivorship-free universe.",
            "Seven earlier calendar sessions do not establish scanner observations before the earliest uploaded signal; absent scanner counts there are an explicit zero-count assumption.",
            "EQ, BE and BZ traded records are admitted. BE/BZ and surveillance restrictions can require trade-to-trade settlement or deposits that this daily simulation does not model.",
        ],
        "available_through": last,
        "warmup_sessions": min(first_index, 7),
    }
    snapshot = {"sessions": sessions, "bars": bars, "raw_bars": raw, "provenance": provenance}
    snapshot["coverage"] = validate_snapshot(snapshot, signals)
    if not any(bars.values()):
        snapshot["coverage"]["status"] = "blocked"
        snapshot["coverage"]["warnings"].append(
            "No requested symbols have admitted official price observations."
        )
    return snapshot


def _extension_manifest(directory):
    root = Path(directory)
    pointer = root / "current.json"
    if not pointer.is_file():
        return None
    pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
    digest = pointer_data.get("sha256", "")
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("Invalid official extension pointer")
    payload = _checked_file(root / (digest + ".json"), digest, 1_000_000)
    manifest = json.loads(payload)
    if (
        manifest.get("format") != "nse-official-extension-v1"
        or manifest.get("baseline_manifest_sha256") != MANIFEST_SHA256
        or manifest.get("calendar_version") != CALENDAR_VERSION
    ):
        raise ValueError("Unsupported official extension lineage/calendar version")
    archives = manifest.get("archives", {})
    if not isinstance(archives, dict) or len(archives) > 500:
        raise ValueError("Official extension archive limit exceeded")
    for name, receipt in archives.items():
        day = receipt.get("date", "")
        if (
            name != day.replace("-", "") + ".zip"
            or receipt.get("source_url") != archive_url(day)
            or not re.fullmatch("[a-f0-9]{64}", receipt.get("sha256", ""))
        ):
            raise ValueError("Invalid official extension archive identity")
        if reviewed_sessions(day, day) != [day]:
            raise ValueError("Extension observation is not a reviewed exchange session")
    manifest["manifest_sha256"] = digest
    return manifest


def archive_url(day):
    date.fromisoformat(day)
    return (
        "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_"
        + day.replace("-", "")
        + "_F_0000.csv.zip"
    )


def official_bundle_status(evidence_dir, extension_dir=None):
    """Cheap identity-checked availability; does not scan ZIPs or claim candle coverage."""
    baseline = json.loads(
        _checked_file(
            Path(evidence_dir) / "nse_daily" / "manifest.json", MANIFEST_SHA256, 1_000_000
        )
    )
    extension = _extension_manifest(extension_dir) if extension_dir else None
    return {
        "date_from": baseline["coverage_start"],
        "date_to": extension["coverage_end"] if extension else baseline["coverage_end"],
        "calendar_version": CALENDAR_VERSION,
        "calendar_years": sorted(HOLIDAYS),
        "import_version": IMPORT_VERSION,
        "extension_available": bool(extension),
        "extension_supported": bool(extension_dir),
        "range_basis": "Manifest availability; symbol admission and coverage are checked during preparation",
    }


def _official_fetch(url):
    from utils.httpx_client import get_httpx_client

    # URLs are constructed from a validated date, never supplied by the client.
    with get_httpx_client().stream("GET", url, timeout=20, follow_redirects=False) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 5_000_000:
                raise ValueError("Official archive exceeds size bound")
            chunks.append(chunk)
    return b"".join(chunks)


def _publish_bytes(root, name, payload):
    import os
    import tempfile

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(root / name)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def extend_official_bundle(
    evidence_dir, extension_dir, end_date, *, fetch=None, progress=None, cancelled=None
):
    """Acquire an append-only official bundle extension and verify baseline overlap.

    fetch(url)->bytes is injectable for offline native integration tests. Runtime
    uses HTTPS official archives with no redirects, arbitrary URL or trust flags.
    Unknown calendars/actions/ISIN segments cannot be approved by this API.
    """
    root = Path(extension_dir)
    if not root.is_absolute():
        raise ValueError("Official extension requires an absolute configured directory")
    baseline_root = Path(evidence_dir).resolve()
    target_root = root.resolve()
    if target_root.is_relative_to(baseline_root) or baseline_root.is_relative_to(target_root):
        raise ValueError(
            "Official extension must be separate from the read-only baseline directory"
        )
    baseline = json.loads(
        _checked_file(
            Path(evidence_dir) / "nse_daily" / "manifest.json", MANIFEST_SHA256, 1_000_000
        )
    )
    sessions = reviewed_sessions(baseline["coverage_start"], end_date)
    if end_date <= baseline["coverage_end"] or end_date not in sessions:
        raise ValueError("Choose a later reviewed exchange session for extension")
    existing = _extension_manifest(root)
    if existing and end_date < existing["coverage_end"]:
        raise ValueError("An extension cannot shrink retained official evidence")
    baseline_days = sorted(entry["date"] for entry in baseline["archives"].values())
    days = baseline_days[-2:] + [day for day in sessions if day > baseline["coverage_end"]]
    if len(days) > 500:
        raise ValueError("Extension exceeds 500 daily archives")
    root.mkdir(parents=True, exist_ok=True)
    acquire = fetch or _official_fetch
    entries = dict(existing["archives"]) if existing else {}
    for index, day in enumerate(days):
        if cancelled and cancelled():
            raise InterruptedError("Official extension cancelled")
        if progress:
            progress(index, len(days))
        name = day.replace("-", "") + ".zip"
        if day > baseline["coverage_end"] and name in entries:
            _checked_file(
                root / (entries[name]["sha256"] + ".zip"), entries[name]["sha256"], 5_000_000
            )
            continue
        payload = acquire(archive_url(day))
        if not isinstance(payload, bytes) or len(payload) > 5_000_000:
            raise ValueError("Invalid official archive payload")
        parse_archive(payload, day, ())  # Complete archive schema/date/size, no fake sessions.
        digest = hashlib.sha256(payload).hexdigest()
        if name in baseline["archives"] and digest != baseline["archives"][name]["sha256"]:
            raise ValueError("Official extension conflicts with pinned baseline overlap")
        if name in entries and entries[name]["sha256"] != digest:
            raise ValueError("Official extension cannot overwrite retained observations")
        if day > baseline["coverage_end"]:
            if (
                not (root / (digest + ".zip")).exists()
                and sum(path.stat().st_size for path in root.iterdir() if path.is_file())
                + len(payload)
                > 512 * 1024 * 1024
            ):
                raise ValueError("Official extension exceeds its 512 MiB directory bound")
            _publish_bytes(root, digest + ".zip", payload)
        entries[name] = {
            "date": day,
            "source_url": archive_url(day),
            "sha256": digest,
            "bytes": len(payload),
        }
    manifest = {
        "format": "nse-official-extension-v1",
        "baseline_manifest_sha256": MANIFEST_SHA256,
        "calendar_version": CALENDAR_VERSION,
        "coverage_start": baseline["coverage_start"],
        "coverage_end": end_date,
        "overlap_dates": baseline_days[-2:],
        "archives": entries,
        "action_policy": "Only versioned reviewed action and identity mappings; all other changes remain raw/quarantined",
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    _publish_bytes(root, digest + ".json", encoded)
    if cancelled and cancelled():
        raise InterruptedError("Official extension cancelled before publication")
    _publish_bytes(root, "current.json", json.dumps({"sha256": digest}).encode())
    if progress:
        progress(len(days), len(days))
    return {
        **official_bundle_status(evidence_dir, extension_dir),
        "manifest_sha256": digest,
        "archives": len(entries),
    }
