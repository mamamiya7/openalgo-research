"""Real constrained VectorBT marks/facts on explicit controlled OHLCV, no broker."""

from copy import deepcopy

from test_recommendation_seeds import prepared
from test_vectorbt_portfolio import assert_reconciles

from research.automatic_protocol import slice_period
from research.connectors.vectorbt_portfolio import evaluate
from research.development_evidence import build, verify_shard
from research.evaluation_basis import build_evaluation_basis
from research.execution_features import attach
from research.portfolio_constraint_evidence import assess as audit
from research.portfolio_execution_features import attach as attach_portfolio
from research.recommendation_seeds import account_facts, assess, policy
from research.report_contract import fingerprint


def test_actual_same_native_account_cannot_be_mistaken_for_an_improvement():
    value = prepared()
    current = attach_portfolio(attach(slice_period(value, "search")))
    reports, shards = [], []
    for index in range(3):
        native = deepcopy(current)
        # Exercise three actual sizes, then compare each account to itself.
        native["strategies"][0]["config"]["order_size_pct"] /= index + 1
        report = evaluate(native["strategies"], native["snapshot"], value["portfolio"]["capital"])
        assert_reconciles(report)
        report["evaluation_basis"] = build_evaluation_basis(native, period="selection")
        report["research_opportunity_basis"] = build_evaluation_basis(current, period="selection")
        report["portfolio_constraint_evidence"] = audit(
            report, native["portfolio_execution_features"]
        )
        assert report["portfolio_constraint_evidence"]["status"] == "supported", report[
            "portfolio_constraint_evidence"
        ]
        report["search_seed_evidence"] = account_facts(report, policy())
        shard = build(
            report,
            evaluation_id=fingerprint({"index": index}),
            binding={
                "inputs_artifact": "native-seed-fixture",
                "search_identity": "controlled-native",
            },
            settings=report["strategies"],
            hypothesis={"id": "none"},
            period="search",
            stress=False,
            expected_timeline=native["snapshot"]["sessions"],
        )
        verify_shard(
            shard,
            descriptor={
                key: shard[key]
                for key in (
                    "id",
                    "evaluation_id",
                    "config_id",
                    "binding",
                    "period",
                    "stress",
                    "evaluation_basis_id",
                )
            },
        )
        reports.append(report)
        shards.append(shard)
    for shard in shards:
        runs = [
            {
                "seed": seed,
                "proposals": 68,
                "adaptive_proposals": 18,
                "unique_accounts": 60,
                "search_id": fingerprint({"seed": seed}),
                "account": shard,
                "facts": shard["search_seed_evidence"],
            }
            for seed in range(3)
        ]
        result = assess(policy(), runs, shard)
        assert result["status"] == "not_supported", result
        assert all(row["edge_pct"] == 0 for row in result["runs"])
    assert all(report["summary"]["closed_trades"] >= 5 for report in reports)
