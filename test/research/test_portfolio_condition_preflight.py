"""Browser JSON numeric round trips cannot discard a registered chosen rule."""

from copy import deepcopy

import pytest
from test_automatic_catalog import request

from research.entry_conditions import stock_module
from research.portfolio import fingerprint, normalize
from services import research_library, research_portfolio


def browser_numbers(value):
    if isinstance(value, dict):
        return {key: browser_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_numbers(item) for item in value]
    if type(value) is float and value.is_integer():
        return int(value)
    return value


def payload(version, hypothesis_id):
    raw = request()
    raw["version"] = "research-portfolio-v1"
    for row in raw["strategies"]:
        row["search"] = {}
        row["type"] = "signals"
    raw.pop("automatic_research")
    rule = next(row for row in stock_module(version).catalog() if row["id"] == hypothesis_id)
    raw["entry_conditions"] = {"version": version, "hypothesis": rule}
    return raw


@pytest.fixture(autouse=True)
def source(monkeypatch):
    monkeypatch.setattr(
        research_portfolio.service,
        "source_for",
        lambda *_: {
            "signals": [
                {"symbol": "AAA", "date": f"2026-01-{day}", "row": i + 2}
                for i, day in enumerate((21, 22, 23))
            ],
            "receipt": {"input_rows": 3},
        },
    )
    monkeypatch.setattr(
        research_library.evidence_service,
        "source_receipt",
        lambda _store, _owner, identifier: {"id": identifier, "receipt": {"signal_count": 3}},
    )


@pytest.mark.parametrize(
    "version,identifier",
    [
        ("research-stock-entry-conditions-v1", "ema_above_20"),
        ("research-stock-entry-conditions-v2", "rsi_14_gt60"),
        ("research-stock-entry-conditions-v3", "relative_20_gt2"),
        ("research-joint-entry-conditions-v1", "rsi_14_gt60__all"),
    ],
)
def test_preflight_and_submission_resolve_browser_rule_to_exact_saved_canonical_identity(
    version, identifier
):
    original = payload(version, identifier)
    original["strategies"][0]["entry_condition"] = deepcopy(original["entry_conditions"])
    canonical = normalize(original)
    wire = browser_numbers(original)
    untouched = deepcopy(wire)
    # Canonical fingerprint remains strict. The request boundary restores only
    # a catalog-proven mathematically identical descriptor.
    assert fingerprint(wire["entry_conditions"]) != fingerprint(original["entry_conditions"])
    preflight = research_portfolio.preview(None, "controlled-owner", wire)
    submitted = research_portfolio.resolve_inputs(None, "controlled-owner", wire)
    assert preflight["portfolio"] == submitted["portfolio"] == canonical
    assert fingerprint(preflight["portfolio"]) == fingerprint(canonical)
    assert preflight["receipt"]["signal_count"] == 3
    assert wire == untouched
    # Editing/saving the reused setup takes the separate library-draft ingress.
    draft = {**research_library.fresh_draft(), "portfolio": wire}
    saved = research_library.normalize_draft(None, "controlled-owner", draft)
    assert saved["portfolio"]["entry_conditions"] == original["entry_conditions"]
    assert fingerprint(saved["portfolio"]["entry_conditions"]) == fingerprint(
        original["entry_conditions"]
    )
    assert fingerprint(saved["portfolio"]["strategies"][0]["entry_condition"]) == fingerprint(
        original["entry_conditions"]
    )
    assert draft["portfolio"] == untouched


@pytest.mark.parametrize(
    "mutation", ["boolean", "threshold", "huge_integer", "label", "extra", "missing"]
)
def test_preflight_never_repairs_a_changed_or_ambiguous_rule(mutation):
    wire = browser_numbers(payload("research-joint-entry-conditions-v1", "rsi_14_gt60__all"))
    hypothesis = wire["entry_conditions"]["hypothesis"]
    stock = hypothesis["stock_hypothesis"]
    if mutation == "boolean":
        stock["lookback"] = True
    elif mutation == "threshold":
        stock["threshold"] = 60.0000000001
    elif mutation == "huge_integer":
        stock["threshold"] = 10**1000
    elif mutation == "label":
        stock["label"] = "Changed rule"
    elif mutation == "extra":
        stock["extra"] = 1
    else:
        stock.pop("threshold")
    with pytest.raises(ValueError, match="supported joint"):
        research_portfolio.preview(None, "controlled-owner", wire)


def test_boolean_zero_cannot_masquerade_as_numeric_zero_in_a_legacy_rule():
    wire = browser_numbers(payload("research-stock-entry-conditions-v1", "ema_above_20"))
    wire["entry_conditions"]["hypothesis"]["threshold"] = False
    with pytest.raises(ValueError):
        research_portfolio.preview(None, "controlled-owner", wire)


def test_boolean_in_per_strategy_marker_cannot_match_registered_numeric_zero():
    wire = browser_numbers(payload("research-stock-entry-conditions-v1", "ema_above_20"))
    wire["strategies"][0]["entry_condition"] = deepcopy(wire["entry_conditions"])
    wire["strategies"][0]["entry_condition"]["hypothesis"]["threshold"] = False
    with pytest.raises(ValueError, match="saved strategy entry condition changed"):
        research_portfolio.preview(None, "controlled-owner", wire)
    with pytest.raises(ValueError, match="saved strategy entry condition changed"):
        research_library.normalize_draft(
            None, "controlled-owner", {**research_library.fresh_draft(), "portfolio": wire}
        )
