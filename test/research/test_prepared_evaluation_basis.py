"""Canonical basis reuse for private immutable markets, with fresh account IDs."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from test_evaluation_basis import complete
from test_portfolio_coverage import evidence as coverage_evidence
from test_vectorbt_portfolio import DAYS, signal, snapshot, strategy

from research import evaluation_basis
from research.prepared_calculation import prepared_calculation
from research.report_contract import fingerprint


def evidence():
    return complete(coverage_evidence([strategy(signals=[signal(), signal("BBB")])], snapshot()))


@pytest.mark.parametrize("minute", [False, True])
def test_cached_basis_preserves_dictionary_and_canonical_identity_across_accounts(minute):
    source = evidence()
    if minute:
        source["snapshot"] = snapshot(minute=True)
    variants = [source]
    for key in ("cost", "capital", "gate", "source"):
        current = deepcopy(source)
        if key == "cost":
            current["strategies"][0]["config"]["cost_bps"] = 17
        elif key == "capital":
            current["portfolio"]["capital"] = 31_000
        elif key == "gate":
            current["strategies"][0]["signals"][0]["research_exclusion"] = "test exclusion"
        else:
            current["strategies"][0]["signals"][0]["row"] = 91
        variants.append(current)
    expected = [
        evaluation_basis.build_evaluation_basis(row, period="selection") for row in variants
    ]
    original = evaluation_basis._snapshot_identities
    with prepared_calculation(source["snapshot"]) as owner:
        with patch.object(evaluation_basis, "_snapshot_identities", wraps=original) as hashed:
            actual = [
                evaluation_basis.build_evaluation_basis(
                    {**row, "snapshot": owner.snapshot}, period="selection"
                )
                for row in variants
            ]
            assert hashed.call_count == 1
        assert actual == expected
        assert [fingerprint(row) for row in actual] == [fingerprint(row) for row in expected]
        assert actual[0]["comparison"]["id"] != actual[1]["comparison"]["id"]
        assert actual[0]["comparison"]["id"] != actual[2]["comparison"]["id"]
        assert actual[0]["cohort_id"] != actual[3]["cohort_id"]
        assert actual[0]["source_id"] != actual[4]["source_id"]


def test_symbol_subset_cache_is_bounded_detached_and_cleared_on_close():
    source = evidence()
    original = evaluation_basis._snapshot_identities
    with prepared_calculation(source["snapshot"]) as owner:
        with patch.object(evaluation_basis, "_snapshot_identities", wraps=original) as hashed:
            for symbols in (("AAA",), ("BBB",), ("AAA", "BBB"), ("AAA",)):
                current = deepcopy(source)
                current["strategies"][0]["signals"] = [signal(symbol) for symbol in symbols]
                expected = evaluation_basis.build_evaluation_basis(current, period="full")
                actual = evaluation_basis.build_evaluation_basis(
                    {**current, "snapshot": owner.snapshot}, period="full"
                )
                assert actual == expected and fingerprint(actual) == fingerprint(expected)
                assert len(owner._evaluation_identities) <= 2
            # Four ordinary calls plus four cache misses after LRU eviction.
            assert hashed.call_count == 8
        identities = owner.evaluation_identities(["AAA"])
        identities["prices_id"] = "tampered"
        assert owner.evaluation_identities(["AAA"])["prices_id"] != "tampered"
    assert not owner._evaluation_identities


def test_mutable_snapshot_never_reuses_active_owner_identity():
    source = evidence()
    with prepared_calculation(source["snapshot"]) as owner:
        owned = evaluation_basis.build_evaluation_basis(
            {**source, "snapshot": owner.snapshot}, period="full"
        )
        source["snapshot"]["bars"]["AAA"][DAYS[0]].update(open=200, high=200, low=200, close=200)
        with patch.object(
            owner, "evaluation_identities", side_effect=AssertionError("stale reuse")
        ):
            changed = evaluation_basis.build_evaluation_basis(source, period="full")
        assert changed["prices_id"] != owned["prices_id"]
