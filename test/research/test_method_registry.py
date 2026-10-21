"""Pinned explanatory domains remain separate from executable recipe identities."""

from copy import deepcopy

import pytest

from research.method_registry import CATALOG_ID, catalog, pinned_hypotheses
from research.report_contract import fingerprint


def test_exact_existing_catalog_has_32_disjoint_families_and_92_choices():
    value = catalog()
    assert value["family_count"] == len(value["families"]) == 32
    assert value["hypothesis_count"] == 92
    choices = [item for row in value["families"] for item in row["registered_hypothesis_ids"]]
    assert len(choices) == len(set(choices)) == 92
    assert set(choices) == {row["id"] for row in pinned_hypotheses()}
    assert fingerprint(pinned_hypotheses()) == CATALOG_ID
    assert value["id"] == fingerprint({key: item for key, item in value.items() if key != "id"})


def test_registry_returns_fresh_values_and_defines_every_component():
    original = catalog()
    changed = deepcopy(original)
    changed["methods"][0]["formula"] = "edited"
    assert catalog() == original
    methods = {row["id"]: row for row in original["methods"]}
    for family in original["families"]:
        for identifier in family["method_ids"]:
            method = methods[identifier]
            assert method["formula"] and method["availability"] and method["missing"]
            assert method["support"]["engine"] == "vectorbt"
            assert method["support"]["feature_interval"] == "D"
    assert original["capability_is_price_coverage"] is False


def test_blocked_methods_do_not_inflate_registered_counts_or_claim_minute_features():
    value = catalog()
    assert {row["id"] for row in value["blocked_families"]} == {
        "volume_liquidity",
        "minute_indicators",
        "learned_models",
    }
    assert all(row["status"] == "unsupported" for row in value["blocked_families"])
    assert not {row["id"] for row in value["blocked_families"]}.intersection(
        row["id"] for row in value["families"]
    )


def test_catalog_drift_requires_an_explicit_new_registry_version(monkeypatch):
    from research import method_registry

    altered = pinned_hypotheses()
    altered[0]["label"] = "Unversioned edit"
    monkeypatch.setattr(method_registry, "hypotheses", lambda: altered)
    with pytest.raises(ValueError, match="pinned method catalogue changed"):
        catalog()


def test_market_metadata_matches_inclusive_native_regime_thresholds_and_ties():
    from research.regimes import recipe

    native = recipe()
    methods = {row["id"]: row for row in catalog()["methods"]}
    trend = methods["market_trend"]
    volatility = methods["market_volatility"]
    assert trend["comparison_policy"] == {
        "kind": "inclusive_thresholds",
        "thresholds": native["trend"],
    }
    assert volatility["comparison_policy"] == {
        "kind": "inclusive_percentile_threshold",
        **native["volatility"],
        "percentiles": native["percentiles"],
    }
    assert volatility["comparison_policy"]["percentiles"]["tie_relative_tolerance"] == 1e-9
    assert "includes percentile90" in volatility["equality"]
    for method in (trend, volatility):
        assert method["availability"] == native["availability"]
        assert "strictly before" in method["availability"]
        assert "<=" not in method["availability"]
        assert str(native["required_sessions"]) in method["observations"]


def test_stock_metadata_preserves_native_strict_equality_and_prior_session_guard():
    from research.stock_features import recipe
    from research.stock_indicators import _compare

    native = recipe()
    methods = {row["id"]: row for row in catalog()["methods"]}
    for identifier in (
        "ema_trend",
        "momentum",
        "rsi",
        "bollinger",
        "atr",
        "relative_strength",
    ):
        method = methods[identifier]
        assert method["availability"] == native["availability"]
        assert method["comparison_policy"] == {
            "kind": "strict",
            "relative_tolerance": 1e-12,
            "absolute_tolerance": 1e-12,
            "equal_passes": False,
        }
    assert _compare({"value": 1.0}, {"threshold": 1.0, "operator": "gt"}) is False
    assert _compare({"value": 1.0 + 5e-13}, {"threshold": 1.0, "operator": "gt"}) is False
    assert _compare({"value": 1.0 + 2e-12}, {"threshold": 1.0, "operator": "gt"}) is True


def test_none_and_conjunction_do_not_claim_an_independent_strict_comparison():
    methods = {row["id"]: row for row in catalog()["methods"]}
    assert methods["none"]["comparison_policy"] == {"kind": "none"}
    assert methods["none"]["requirements"] == []
    assert methods["conjunction"]["comparison_policy"] == {
        "kind": "component_policies",
        "operator": "AND",
        "unknown_dominates_false": True,
    }
    assert "each registered component" in methods["conjunction"]["equality"]


@pytest.mark.parametrize(
    "version,expected",
    [
        (
            "automatic-hypotheses-v8",
            "65c3a8c2eaaa9ae0dc24504e725d28aef2c4b96589cf805ad994d63faefac6a1",
        ),
        (
            "automatic-hypotheses-v9",
            "f30fa47c93e6eb893a0551f7cd764a591a762961571e9f8cb6cbd57f2278dce8",
        ),
    ],
)
def test_execution_admission_keeps_prior_registry_bytes_exact(version, expected):
    assert fingerprint(catalog(version)) == expected


def test_execution_registry_reuses_the_exact_expanded_indicator_catalog():
    previous = catalog("automatic-hypotheses-v9")
    execution = catalog("automatic-hypotheses-v10")
    assert execution["family_count"] == 32
    assert execution["hypothesis_count"] == 108
    for key in ("methods", "families", "catalog_id", "blocked_families"):
        assert execution[key] == previous[key]
    assert pinned_hypotheses("automatic-hypotheses-v10") == pinned_hypotheses(
        "automatic-hypotheses-v9"
    )
    assert "do not add indicator families" in execution["execution_scope"]
