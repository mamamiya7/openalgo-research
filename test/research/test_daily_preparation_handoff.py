"""Daily automatic preparation is complete, fresh and never a reduced search."""

from copy import deepcopy

import pytest
from test_portfolio_allocation_recipe import prepared

from research.automatic_protocol import compile_recipe
from research.portfolio import fingerprint
from research.portfolio_coverage import prepare as prepare_coverage
from services import research_portfolio, research_preparation, scanner_research_service


def complete():
    value = prepared()
    value["frozen_prices"] = True
    value["acquisition_receipts"] = []
    value["snapshot"]["provenance"]["broker_receipts"] = []
    value = prepare_coverage(value)
    value["automatic_recipe"] = compile_recipe(value)
    return value, {"portfolio": value["portfolio"], "versions": value["versions"]}


def test_daily_automatic_handoff_preserves_all_checks_and_full_programme(monkeypatch):
    value, spec = complete()
    raw = {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in ("frozen_prices", "automatic_recipe")
    }
    assert research_preparation._eligible(raw, spec)
    assert research_portfolio.validate_prepared_inputs(object(), value, spec) is value
    assert value["automatic_recipe"]["budget"]["proposals"] == 408
    assert value["automatic_recipe"]["budget"]["max_simulations"] == 512
    monkeypatch.setattr(scanner_research_service, "read_artifact", lambda *_: deepcopy(value))
    monkeypatch.setattr(
        scanner_research_service, "save_artifact", lambda _, item: fingerprint(item)
    )
    monkeypatch.setattr(
        research_portfolio,
        "_prepare_prices",
        lambda *_, **__: pytest.fail("Completed daily preparation must not reacquire"),
    )

    def boundary():
        raise RuntimeError("checked before search")

    with pytest.raises(RuntimeError, match="checked before search"):
        research_portfolio.run(
            object(),
            "test-owner",
            raw,
            spec,
            saved={"phase": "calculation", "inputs_artifact": "a" * 64, "calculation": None},
            checkpoint=lambda *_: None,
            progress=lambda *_: None,
            cancelled=lambda: None,
            boundary=boundary,
        )


@pytest.mark.parametrize("change", ["budget", "baseline", "missing"])
def test_prepared_automatic_recipe_cannot_change_its_search_or_rules(change):
    value, spec = complete()
    if change == "budget":
        value["automatic_recipe"]["budget"]["proposals"] -= 1
    elif change == "baseline":
        value["automatic_recipe"]["baseline"][0]["config"]["target_pct"] += 1
    else:
        value.pop("automatic_recipe")
    with pytest.raises(ValueError, match="automatic programme"):
        research_portfolio.validate_prepared_inputs(object(), value, spec)


def test_minute_and_nonautomatic_search_keep_their_ordinary_preparation():
    value, spec = complete()
    for key in ("frozen_prices", "automatic_recipe"):
        value.pop(key, None)
    other = deepcopy(spec)
    other["portfolio"].pop("automatic_research")
    assert not research_preparation._eligible(value, other)
    invalid = deepcopy(other)
    invalid["portfolio"]["automatic_research"] = None
    assert not research_preparation._eligible(value, invalid)
