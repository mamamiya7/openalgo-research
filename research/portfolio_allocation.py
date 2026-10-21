"""Causal, bounded source-universe cluster allocation; no forecast or matrix inverse."""

from math import isfinite

from research.portfolio import fingerprint

VERSION = "research-cluster-allocation-v1"
MODE = "prior60_cluster_inverse_vol"


def build(prepared, entry_session):
    """Equal cluster budgets, inverse-volatility weights inside each cluster.

    Exactly aligned prior returns only. Missing and constant series are explicitly
    unavailable; neither zero risk nor synthetic diversification is inferred.
    Singular covariance is permitted: no inverse is taken and 10% diagonal
    shrinkage bounds dependence estimation. Clustering uses positive regularized correlation .8.
    """
    import numpy as np

    if not 1 <= len(prepared) <= 200:
        raise ValueError("Allocation source universe exceeds its supported bound")
    groups = {}
    for symbol, record in sorted(prepared.items()):
        if record is not None:
            groups.setdefault(tuple(record["dates"]), []).append(symbol)
    if not groups:
        return None
    # Use the largest aligned calendar cohort; deterministic tie break. Symbols
    # on another calendar are unavailable, never filled with invented returns.
    dates, symbols = min(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    symbols = [s for s in symbols if np.std(prepared[s]["returns"]) > 1e-14]
    if not symbols:
        return None
    matrix = np.asarray([prepared[s]["returns"] for s in symbols], dtype=float)
    if matrix.shape != (len(symbols), 60) or not np.isfinite(matrix).all():
        raise ValueError("Allocation requires sixty finite prior returns")
    covariance = np.atleast_2d(np.cov(matrix, ddof=1))
    covariance = 0.9 * covariance + 0.1 * np.diag(np.diag(covariance))
    volatility = np.sqrt(np.diag(covariance))
    raw = covariance / np.outer(volatility, volatility)
    components, remaining = [], set(range(len(symbols)))
    while remaining:
        pending, component = [min(remaining)], set()
        while pending:
            i = pending.pop()
            if i in component:
                continue
            component.add(i)
            pending.extend(j for j in sorted(remaining - component) if raw[i, j] >= 0.8)
        remaining -= component
        components.append(sorted(component))
    weights, clusters = {}, []
    for component in components:
        inverse = [1 / volatility[i] for i in component]
        total = sum(inverse)
        clusters.append([symbols[i] for i in component])
        for i, value in zip(component, inverse, strict=True):
            weights[symbols[i]] = float(value / total / len(components))
    result = {
        "version": VERSION,
        "entry_session": entry_session,
        "from": dates[0],
        "to": dates[-1],
        "observations": 60,
        "available_at": max(prepared[s]["available_at"] for s in symbols),
        "source_id": fingerprint({s: prepared[s]["source_id"] for s in symbols}),
        "weights": weights,
        "clusters": clusters,
        "unavailable": sorted(set(prepared) - set(symbols)),
        "shrinkage": 0.1,
    }
    return {**result, "id": fingerprint(result)}


def validate(value, *, symbol, entry_session, decision_at, required_symbols=()):
    from datetime import date, datetime
    from re import fullmatch

    from research.stock_features import _json_size

    _json_size(value, 64 * 1024)

    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("allocation_unavailable: prior aligned prices are required")
    if value.get("id") != fingerprint({k: v for k, v in value.items() if k != "id"}):
        raise ValueError("Allocation evidence identity changed")
    if (
        set(value)
        != {
            "version",
            "entry_session",
            "from",
            "to",
            "observations",
            "available_at",
            "source_id",
            "weights",
            "clusters",
            "unavailable",
            "shrinkage",
            "id",
        }
        or value.get("entry_session") != entry_session
        or not isinstance(value.get("source_id"), str)
        or not fullmatch(r"[a-f0-9]{64}", value["source_id"])
    ):
        raise ValueError("Allocation evidence belongs to another entry or source")
    weights, clusters = value.get("weights"), value.get("clusters")
    if (
        not isinstance(weights, dict)
        or not 1 <= len(weights) <= 200
        or any(
            type(w) not in (int, float) or not isfinite(w) or not 0 < w <= 1
            for w in weights.values()
        )
        or any(
            not isinstance(s, str) or not fullmatch(r"[A-Z0-9][A-Z0-9 &._-]{0,99}", s)
            for s in weights
        )
        or not isinstance(value.get("unavailable"), list)
        or len(value["unavailable"]) > 200
        or any(not isinstance(s, str) or s in weights for s in value["unavailable"])
        or len(value["unavailable"]) != len(set(value["unavailable"]))
        or abs(sum(weights.values()) - 1) > 1e-10
        or not isinstance(clusters, list)
        or not clusters
        or any(not isinstance(c, list) or not c for c in clusters)
        or sorted(s for c in clusters for s in c) != sorted(weights)
        or any(abs(sum(weights[s] for s in c) - 1 / len(clusters)) > 1e-10 for c in clusters)
        or value.get("observations") != 60
        or value.get("shrinkage") != 0.1
        or not value["from"] <= value["to"] < entry_session
    ):
        raise ValueError("Allocation evidence has invalid weights or calendar")
    try:
        for field in ("from", "to", "entry_session"):
            if date.fromisoformat(value[field]).isoformat() != value[field]:
                raise ValueError
        available, decision = (
            datetime.fromisoformat(value["available_at"]),
            datetime.fromisoformat(decision_at),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Allocation evidence has invalid availability") from error
    if available.tzinfo is None or decision.tzinfo is None or available > decision:
        raise ValueError("Allocation evidence was unavailable at entry")
    if any(s not in weights for s in (symbol, *required_symbols)):
        raise ValueError("allocation_unavailable: a new or held symbol lacks aligned prior returns")
    return value
