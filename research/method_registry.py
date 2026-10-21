"""Pinned explanatory method definitions; never an executable search catalogue.

V1 describes the existing v8 programme only. It cannot add hypotheses, change
admission, acquire prices or reinterpret a saved calculation's numerical rules.
"""

import json
from dataclasses import asdict, dataclass

from research.joint_stock_market import MARKETS
from research.joint_stock_market import catalog as hypotheses
from research.report_contract import fingerprint

VERSION = "research-method-registry-v1"
RECIPE_VERSION = "automatic-hypotheses-v8"
EXPANDED_RECIPE_VERSION = "automatic-hypotheses-v9"
EXECUTION_RECIPE_VERSION = "automatic-hypotheses-v10"
PRIORITY_RECIPE_VERSION = "automatic-hypotheses-v11"
CATALOG_ID = "bd3cd74734aac954372b379f88c4fb45b74f18de797824e2e389c5831ff87c28"
EXPANDED_CATALOG_ID = "2c8b8afcb6c378f82ba54198eb0ac4ae67769c3dda533b28a2cadfdcdfdc8c88"
MAX_BYTES = 64 * 1024


def bounded(value, maximum=MAX_BYTES):
    """Bound canonical metadata without creating a price-sized JSON string."""
    size = 0
    try:
        for part in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            size += len(part.encode())
            if size > maximum:
                raise ValueError("Method metadata exceeds its size limit")
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("Method metadata must be bounded canonical JSON") from error


@dataclass(frozen=True)
class MethodSpec:
    id: str
    label: str
    formula: str
    requirements: tuple[str, ...]
    observations: str
    implementation: str


_SPECS = (
    MethodSpec(
        "none",
        "No stock condition",
        "Retain original opportunities and their price exclusions",
        (),
        "No auxiliary observations",
        "research.stock_features",
    ),
    MethodSpec(
        "ema_trend",
        "Stock EMA trend",
        "Seed with mean of first N closes; EMA=2/(N+1)*close+(1-2/(N+1))*previous; compare last close with EMA",
        ("stock_daily_close",),
        "5*N prior consecutive closes; N=10 or20",
        "research.stock_features",
    ),
    MethodSpec(
        "momentum",
        "Stock momentum",
        "100*(last_close/close_N_sessions_ago-1)",
        ("stock_daily_close",),
        "N+1 prior consecutive closes; N=5 or20",
        "research.stock_features",
    ),
    MethodSpec(
        "rsi",
        "Stock RSI",
        "Pinned OpenAlgo Wilder RSI; mean initial gains/losses, then Wilder smoothing; native zero-loss result100 retained, including flat windows",
        ("stock_daily_close",),
        "100 prior consecutive closes; only registered period/operator/threshold tuples",
        "openalgo.ta.rsi@2.0.5",
    ),
    MethodSpec(
        "bollinger",
        "Bollinger breakout or pullback",
        "Close minus selected20-session band; population variance ddof=0; multiplier2; zero-width bands unknown",
        ("stock_daily_close",),
        "20 prior consecutive closes",
        "openalgo.ta.bbands@2.0.5",
    ),
    MethodSpec(
        "atr",
        "Stock ATR as percent of price",
        "100*Wilder ATR14/last_close; first true range high-low; zero ATR is defined",
        ("stock_daily_ohlc",),
        "100 prior consecutive OHLC observations",
        "openalgo.ta.atr@2.0.5",
    ),
    MethodSpec(
        "relative_strength",
        "Relative strength against Nifty",
        "100*((stock_last/stock_first)/(index_last/index_first)-1); not RSI, arithmetic excess return or total return",
        ("stock_daily_close", "NIFTY_NSE_INDEX_daily_close"),
        "N+1 aligned prior consecutive closes; N=5,20 or60",
        "research.stock_relative_strength",
    ),
    MethodSpec(
        "conjunction",
        "Registered stock condition pairs",
        "AND of the exact registered components; unknown dominates false; no arbitrary Boolean expression",
        ("registered_component_inputs",),
        "Every component's complete prior window",
        "research.stock_indicators/research.stock_relative_strength",
    ),
    MethodSpec(
        "market_trend",
        "Nifty trend",
        "Fixed SMA20/SMA60 gap>=0.5%, momentum20>=1% and directional efficiency>=0.30 must agree for rising trend; falling uses gap<=-0.5% and momentum<=-1%; efficiency<=0.20 is range",
        ("NIFTY_NSE_INDEX_daily_close",),
        "150 consecutive required sessions;160 retained warmup sessions; fixed research-regimes-v1",
        "research.regimes@research-regimes-v1",
    ),
    MethodSpec(
        "market_volatility",
        "Nifty volatility",
        "20-return sample volatility annualized252; percentile against90 strictly earlier observations; high>=90th percentile, normal below",
        ("NIFTY_NSE_INDEX_daily_close",),
        "150 consecutive required sessions;160 retained warmup sessions; ties use fixed midrank policy",
        "research.regimes@research-regimes-v1",
    ),
)


def _policies(identifier):
    """Literal explanatory policies, independently checked against native recipes."""
    stock_availability = (
        "Prior session dates only; recorded daily close available_at <= actual entry decision"
    )
    if identifier == "none":
        return {
            "availability": "No auxiliary observations or availability condition",
            "missing": "Retain the original opportunities and their original price exclusions",
            "equality": "No scalar comparison",
            "comparison_policy": {"kind": "none"},
        }
    if identifier == "conjunction":
        return {
            "availability": "Every component must meet its own prior-session and recorded close availability policy",
            "missing": "Unknown in any component dominates false; never fill or treat it as passing",
            "equality": "Use each registered component's comparison policy; AND is not a scalar comparison",
            "comparison_policy": {
                "kind": "component_policies",
                "operator": "AND",
                "unknown_dominates_false": True,
            },
        }
    if identifier in ("market_trend", "market_volatility"):
        common = {
            "availability": "recorded daily session close strictly before decision time",
            "missing": "Unknown until a complete contiguous150-session window exists; unknown excludes the condition",
        }
        if identifier == "market_trend":
            return {
                **common,
                "equality": "Inclusive regime thresholds: rising/falling limits pass at equality; range efficiency <=0.20",
                "comparison_policy": {
                    "kind": "inclusive_thresholds",
                    "thresholds": {
                        "minimum_sma_gap_pct": 0.5,
                        "minimum_momentum_pct": 1.0,
                        "minimum_directional_efficiency": 0.30,
                        "maximum_range_efficiency": 0.20,
                        "agreement": "SMA20/SMA60 direction and momentum must agree; otherwise unknown",
                    },
                },
            }
        return {
            **common,
            "equality": "High volatility includes percentile90; percentile ties use relative tolerance1e-9 and absolute tolerance1e-12 with midpoint rank",
            "comparison_policy": {
                "kind": "inclusive_percentile_threshold",
                "high_at_or_above_percentile": 90.0,
                "percentiles": {
                    "strictly_prior_observations": 90,
                    "formula": "100 * (less + 0.5 * equal) / observations",
                    "tie_relative_tolerance": 1e-9,
                    "tie_absolute_tolerance": 1e-12,
                },
            },
        }
    return {
        "availability": stock_availability,
        "missing": "Unknown input excludes the condition; never fill or treat it as passing",
        "equality": "Strict comparison; relative/absolute tolerance1e-12 counts as equal and does not pass",
        "comparison_policy": {
            "kind": "strict",
            "relative_tolerance": 1e-12,
            "absolute_tolerance": 1e-12,
            "equal_passes": False,
        },
    }


def pinned_hypotheses(recipe_version=RECIPE_VERSION):
    if recipe_version in (
        EXPANDED_RECIPE_VERSION,
        EXECUTION_RECIPE_VERSION,
        PRIORITY_RECIPE_VERSION,
    ):
        from research.expanded_stock_market import catalog as expanded

        values, count, expected = expanded(), 108, EXPANDED_CATALOG_ID
    elif recipe_version == RECIPE_VERSION:
        values, count, expected = hypotheses(), 92, CATALOG_ID
    else:
        raise ValueError("Choose a pinned method programme")
    if len(values) != count or fingerprint(values) != expected:
        raise ValueError("The pinned method catalogue changed; register a new version")
    return values


def catalog(recipe_version=RECIPE_VERSION):
    """Return fresh JSON definitions; module state contains immutable tuples only."""
    rows = pinned_hypotheses(recipe_version)
    methods = []
    for spec in _SPECS:
        method = asdict(spec)
        if (
            recipe_version
            in (EXPANDED_RECIPE_VERSION, EXECUTION_RECIPE_VERSION, PRIORITY_RECIPE_VERSION)
            and spec.id == "momentum"
        ):
            method["observations"] = "N+1 prior consecutive closes; N=5,10,20 or60"
        method.update(
            requirements=list(spec.requirements),
            **_policies(spec.id),
            support={
                "engine": "vectorbt",
                "asset_profile": "NSE_long_cash_equities",
                "execution_intervals": ["D", "1m"],
                "feature_interval": "D",
            },
        )
        method["id_hash"] = fingerprint(method)
        methods.append(method)
    families = []
    market_labels = {row["id"]: row["label"] for row in MARKETS}
    by_method = {row["id"]: row for row in methods}
    for family in dict.fromkeys(row["family"] for row in rows):
        members = [row for row in rows if row["family"] == family]
        stock, market = family.split(":")
        method_ids = [stock]
        if market == "trend_up":
            method_ids.append("market_trend")
        elif market in ("volatility_normal", "volatility_high"):
            method_ids.append("market_volatility")
        families.append(
            {
                "id": family,
                "label": by_method[stock]["label"]
                + (" · " + market_labels[market] if market != "all" else ""),
                "method_ids": method_ids,
                "registered_hypothesis_ids": [row["id"] for row in members],
                "formula": " AND ".join(method_ids),
                "domain": [dict(row["stock_hypothesis"], market=row["market"]) for row in members],
            }
        )
    blocked = [
        {
            "id": "volume_liquidity",
            "label": "Volume and liquidity",
            "status": "unsupported",
            "reason": "The pinned auxiliary price contract does not retain qualified observed volume and units.",
        },
        {
            "id": "minute_indicators",
            "label": "Minute indicators",
            "status": "unsupported",
            "reason": "Minute execution clocks are supported; minute indicator discovery is not part of this recipe.",
        },
        {
            "id": "learned_models",
            "label": "Learned models and transforms",
            "status": "unsupported",
            "reason": "This recipe refits execution settings; no learned feature model or transform adapter is registered.",
        },
    ]
    result = {
        "version": VERSION,
        "recipe_version": recipe_version,
        "catalog_id": EXPANDED_CATALOG_ID
        if recipe_version
        in (EXPANDED_RECIPE_VERSION, EXECUTION_RECIPE_VERSION, PRIORITY_RECIPE_VERSION)
        else CATALOG_ID,
        "family_count": 32,
        "hypothesis_count": len(rows),
        "methods": methods,
        "families": families,
        "blocked_families": blocked,
        "execution_scope": "Existing registered entry-condition families only; exits, trailing and native clocks retain their separate frozen contracts",
        "capability_is_price_coverage": False,
    }
    if recipe_version == EXECUTION_RECIPE_VERSION:
        result["execution_scope"] = (
            "The same108 registered entry conditions; finite percentage or ATR exits and risk-constrained sizing are materialized from a separate frozen execution-search contract and do not add indicator families"
        )
    if recipe_version == PRIORITY_RECIPE_VERSION:
        result["execution_scope"] = (
            "The same108 registered entry conditions, finite ATR/percentage exits and risk-constrained sizing; one causal global entry priority is searched separately, without adding indicator families"
        )
    bounded(result)
    return {**result, "id": fingerprint(result)}
