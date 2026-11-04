"""Two tiny native accounts, no optimizer or scanner invocation."""

# ruff: noqa: F811 -- preparation-only fixtures
from copy import deepcopy

import pytest
from test_automatic_actions_runtime import acting  # noqa: F401
from test_automatic_decision_runtime import deciding  # noqa: F401
from test_automatic_design_runtime import designed  # noqa: F401
from test_available_history_runtime import available  # noqa: F401

from research.connectors.vectorbt_portfolio import evaluate
from research.report_contract import fingerprint
from research.supervisor_accounts import FrozenAccounts


def test_changed_native_accounts_bind_capital_costs_calendar_and_release_owners(acting):
    source = deepcopy(acting)
    original = fingerprint(source)
    settings = deepcopy(source["automatic_recipe"]["baseline"])
    for row in settings:
        row["config"]["initial_capital"] = 200000
    with FrozenAccounts(source, evaluate, lambda: None) as accounts:
        hypothesis = next(h for h in accounts.context.catalog() if h["id"] == "none")
        reports = []
        for period in ("search", "fit1"):
            report = accounts.calculate(settings, hypothesis, period, True)
            assert report["summary"]["initial_capital"] == report["portfolio"]["capital"] == 200000
            assert (
                accounts.audit(
                    report,
                    {
                        "settings": settings,
                        "hypothesis": hypothesis,
                        "period": period,
                        "stress": True,
                    },
                )
                == report["portfolio_constraint_evidence"]
            )
            assert report["portfolio_constraint_evidence"]["status"] == "supported"
            reports.append(report)
        assert reports[0]["evaluation_basis"]["period"] != reports[1]["evaluation_basis"]["period"]
        changed = deepcopy(reports[0])
        changed["evaluation_basis"]["period"]["observations"] += 1
        with pytest.raises(ValueError, match="evidence changed"):
            accounts.audit(
                changed,
                {
                    "settings": settings,
                    "hypothesis": hypothesis,
                    "period": "search",
                    "stress": True,
                },
            )
    assert accounts.evidence is None and accounts.windows == {} and accounts.gates == {}
    assert fingerprint(source) == original
