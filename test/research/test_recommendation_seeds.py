"""Pure qualification contracts; controlled marks are not native-account proof."""

from copy import deepcopy

import pytest
from test_recommendation_sensitivity import account, audit, reseal

from research.development_evidence import build
from research.recommendation_seeds import ACCOUNT_VERSION, assess, policy
from research.report_contract import fingerprint


def fixture(*, edges=(0.001, 0.001, 0.001), support=10, days=80):
    from datetime import date, timedelta

    scenario = {
        "name": "search",
        "from": "2026-01-01",
        "to": (date(2026, 1, 1) + timedelta(days=days - 1)).isoformat(),
        "source_id": fingerprint("source"),
        "id": fingerprint("scenario"),
    }
    baseline = account(scenario, candidate=False)
    runs = []
    for seed, edge in enumerate(edges):
        shard = account(scenario, edge=edge)
        facts = {
            "version": ACCOUNT_VERSION,
            "plan_id": policy()["id"],
            "basis_id": fingerprint(shard["evaluation_basis"]),
            "support": {"closed_trades": support, "entry_dates": support},
            "constraints": audit(),
        }
        reseal(facts)
        shard = build(
            {
                "strategies": shard["settings"],
                "config": {"initial_capital": 1000},
                "evaluation_basis": shard["evaluation_basis"],
                "equity_curve": shard["curve"],
                "search_seed_evidence": facts,
            },
            evaluation_id=shard["evaluation_id"],
            binding=shard["binding"],
            settings=shard["settings"],
            hypothesis=shard["hypothesis"],
            period="search",
            stress=False,
            expected_timeline=shard["timeline"],
        )
        runs.append(
            {
                "seed": seed,
                "proposals": 68,
                "adaptive_proposals": 18,
                "unique_accounts": 60,
                "search_id": fingerprint({"seed": seed}),
                "account": shard,
                "facts": facts,
            }
        )
    return runs, baseline


def test_same_data_stable_edges_pass_without_replacing_primary():
    runs, baseline = fixture(edges=(0.001, 0.0011, 0.0012))
    original = deepcopy(runs)
    result = assess(policy(), runs, baseline)
    assert result["status"] == "supported", result
    assert result["qualified"] and result["min_edge_fraction"] > 0.5
    assert [row["seed"] for row in result["runs"]] == [0, 1, 2]
    assert result["runs"][0]["config_id"] == runs[0]["account"]["config_id"]
    assert runs == original


@pytest.mark.parametrize(
    "edges", [(0.001, 0, 0.001), (0.001, -0.001, 0.001), (0.001, 0.001, 0.004)]
)
def test_weak_or_inconsistent_repeat_vetoes_primary(edges):
    runs, baseline = fixture(edges=edges)
    assert assess(policy(), runs, baseline)["status"] == "not_supported"


@pytest.mark.parametrize("support,days", [(4, 80), (10, 59)])
def test_sparse_support_is_insufficient(support, days):
    runs, baseline = fixture(support=support, days=days)
    assert assess(policy(), runs, baseline)["status"] == "insufficient"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.pop(),
        lambda r: r.reverse(),
        lambda r: r[1].update(proposals=67),
        lambda r: r[1].update(adaptive_proposals=17),
        lambda r: r[1].update(proposals=68.0),
        lambda r: r[1].update(adaptive_proposals=18.0),
        lambda r: r[1].update(search_id="x" * 64),
        lambda r: r.__setitem__(1, None),
        lambda r: r[1].update(seed=True),
        lambda r: r[1]["facts"]["support"].update(entry_dates=9),
        lambda r: r[1]["account"].update(period="final"),
        lambda r: r[1]["account"]["curve"][0].update(equity=1234),
    ],
)
def test_missing_or_changed_evidence_cannot_support_a_choice(mutation):
    runs, baseline = fixture()
    mutation(runs)
    assert assess(policy(), runs, baseline)["status"] == "unsupported"


def test_skipped_checks_and_policy_changes_are_explicit():
    assert assess(policy())["status"] == "not_run"
    changed = policy()
    changed["seeds"] = [0, 4, 5]
    reseal(changed)
    with pytest.raises(ValueError, match="policy changed"):
        assess(changed)


def prepared():
    from test_sensitivity_native import prepared as old_prepared

    from research.automatic_protocol import SEED_VERSION, compile_recipe
    from research.portfolio import execution_versions, normalize

    value = old_prepared()
    value["portfolio"]["automatic_research"] = {"version": SEED_VERSION}
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    value.pop("automatic_recipe")
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_v15_freezes_seed_policy_and_sufficient_bounded_history_budget():
    from research.automatic_hypotheses import policy_for_version
    from research.development_evidence import plan
    from research.research_mandate import build as mandate

    value = prepared()
    recipe = value["automatic_recipe"]
    assert recipe["search_seeds"] == policy(
        screening_proposals=recipe["search_seeds"]["screening_proposals_per_search"]
    )
    assert recipe["search_seeds"]["adaptive_proposals_per_search"] >= 18
    assert recipe["budget"]["proposals"] == 408
    assert recipe["budget"]["max_simulations"] == 512
    assert recipe["budget"]["max_seed_accounts"] == 136
    assert policy_for_version(recipe["version"])["max_simulations"] == 512
    assert plan(value, 512)["status"] == "available"
    receipt = mandate(value, inputs_artifact="a" * 64)
    assert receipt["search"]["search_seeds"] == recipe["search_seeds"]


def test_v14_contract_stays_identical_to_installed_source():
    from test_sensitivity_native import prepared as v14

    value = v14()
    # Generated using unchanged modules from source 3e4fb1fc4 before v15.
    expected = {
        "portfolio": "fcf54ddea56f2268cb1460a209426fe7d17e580ac6799d332cb687d383661240",
        "automatic_recipe": "abe483da96eeede706750d6716a87a1c98e494d4f50ae28a977616e053843421",
        "versions": "194c0a31f51452120c64a76285328cc25a4f977d84df4d9de7bc298e53db32d8",
        "execution_features": "7d6aa6eeb97acd696f6cfa50dd796eccabac91f5a3829b47df9f4a18fbd2af1c",
        "portfolio_execution_features": "475c455076f8277fa23366005ab57a97b55c4ccc3524a055f98f6e748b58b85b",
    }
    assert {key: fingerprint(value[key]) for key in expected} == expected


@pytest.mark.parametrize("fit", ["search_seed1", "search_seed2"])
def test_paused_repeat_inspection_uses_the_original_search_data_period(fit):
    from research.connectors.optuna_portfolio import _fingerprint
    from services.research_partial_study import _search_inputs

    value = prepared()
    state = {
        "version": value["automatic_recipe"]["version"],
        "binding": fingerprint(
            {"recipe": value["automatic_recipe"], "stock": value["automatic_stock_market_context"]}
        ),
        "attempts": 0,
        "simulations": 0,
        "evaluations": {},
        "fits": {},
        "active_fit": fit,
        "folds": [],
    }
    state["checkpoint_id"] = _fingerprint(state)
    scoped, restored, period, automatic, complete = _search_inputs(value, state)
    assert period == "search" and automatic and not complete
    assert (
        scoped["snapshot"]["sessions"][0] == value["automatic_recipe"]["periods"]["search"]["from"]
    )
    assert restored == state
