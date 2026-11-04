"""Server-owned boundaries and explanations for the reviewed research journey.

This is a research policy, not model training. Models choose only registered
experiments; the native engine and unchanged validation own their outcomes.
"""

from copy import deepcopy

from research.report_contract import fingerprint

VERSION = "research-execution-playbook-v1"
INTENT_POLICY = "fixed-signals-v1"
OBJECTIVE = "robust_improvement"
HISTORICAL_DISCLOSURE = (
    "A historical candidate passed challenges; independent later evidence is unavailable"
)


def trading_style(settings):
    styles = {
        "intraday" if row["config"].get("trade_horizon") == "intraday" else "positional"
        for row in settings
    }
    if len(styles) != 1:
        raise ValueError("Choose one trading style for this research study")
    return styles.pop()


def intent(portfolio, *, style=None):
    """Opt new reviewed plans into fixed signals without changing legacy inputs."""
    from research.portfolio import normalize

    current = deepcopy(portfolio)
    selected_style = trading_style(current["strategies"])
    if style is not None and style != selected_style:
        raise ValueError("Trading style must match the settings selected by the user")
    if current.get("engine") != "vectorbt":
        raise ValueError("This supported automatic research programme uses VectorBT")
    if current.get("entry_conditions") or any(
        row.get("entry_condition") for row in current["strategies"]
    ):
        raise ValueError("Fixed-signal research starts from the original supplied signals")
    current["automatic_research"] = {
        **current.get("automatic_research", {}),
        "version": "automatic-hypotheses-v16",
        "history_policy": "available_history",
        "design_policy": "automatic_design_v1",
        "decision_policy": "automatic_decision_v1",
        "action_policy": "automatic_actions_v3",
        "intent_policy": INTENT_POLICY,
    }
    current.pop("validation", None)
    return normalize(current)


def describe(portfolio):
    """Freeze the supported objective, one engine, style and hard risk limits."""
    if portfolio.get("automatic_research", {}).get("intent_policy") != INTENT_POLICY:
        raise ValueError("The reviewed research plan needs its fixed-signal intent")
    body = {
        "version": VERSION,
        "objective": OBJECTIVE,
        "objective_label": "Robust improvement after costs and drawdown",
        "objective_reason": "Compare percentage return and drawdown, then require every existing validation gate; larger cash profit alone is insufficient.",
        "engine": portfolio["engine"],
        "style": trading_style(portfolio["strategies"]),
        "signal_policy": "fixed_signals",
        "strategy_ids": [row["id"] for row in portfolio["strategies"]],
        "risk_limits": deepcopy(portfolio["risk_limits"]),
        "capital_policy": "estimate_required_without_user_ceiling",
        "methods": [
            "funding_and_size",
            "lower_turnover",
            "reduce_concentration",
            "temporal_stability",
        ],
    }
    return {**body, "id": fingerprint(body)}


def validate_settings(contract, settings):
    if (
        not isinstance(contract, dict)
        or contract.get("version") != VERSION
        or contract.get("id") != fingerprint({k: v for k, v in contract.items() if k != "id"})
        or contract.get("objective") != OBJECTIVE
        or contract.get("engine") != "vectorbt"
        or contract.get("signal_policy") != "fixed_signals"
        or trading_style(settings) != contract.get("style")
        or [row["id"] for row in settings] != contract.get("strategy_ids")
    ):
        raise ValueError(
            "The reviewed research objective, engine, signals or trading style changed"
        )
    for row in settings:
        if row.get("execution_policy", {}).get("risk_limits") != contract["risk_limits"]:
            raise ValueError("AI cannot change the reviewed hard risk limits")
        # The registered `none` hypothesis removes this marker entirely. A
        # regime-only or malformed marker must not evade the fixed-signal rule.
        if row.get("entry_condition"):
            raise ValueError("AI cannot introduce stock or market filters to the supplied signals")


def experiment(remedy, diagnosis, reason):
    """Attach a concrete, testable hypothesis before any experiment is queued."""
    descriptions = {
        "funding_and_size": (
            "Native affordability or sizing constraints prevent requested trades",
            "A feasible capital and ticket combination can admit trades without relaxing risk limits",
            "Change native capital and trade size only; retain the selected trade rules",
        ),
        "lower_turnover": (
            "Recorded transaction-cost checks or cost drag require investigation",
            "A longer legal holding period may improve outcomes after costs",
            "Test the next strictly higher supported holding-period point",
        ),
        "reduce_concentration": (
            "The native design reports concentrated positive profit",
            "Causal supported allocation may reduce concentration",
            "Test the existing causal allocation method with the same signals",
        ),
        "temporal_stability": (
            "Recorded historical prefixes failed the existing stability checks",
            "A shorter legal holding period may improve temporal consistency",
            "Test the next strictly lower supported holding-period point",
        ),
    }
    if remedy not in descriptions:
        raise ValueError("Choose a supported research experiment")
    problem, hypothesis, change = descriptions[remedy]
    return {
        "version": VERSION,
        "problem": problem,
        "hypothesis": hypothesis,
        "change": change,
        "reason": reason[:1000],
        "expected_evidence": "A native account with the exact changed settings, followed by all unchanged cost, neighborhood, sample, distribution, risk and applicable time-window checks",
        "stopping_rule": "Reject an economically unchanged point; never accept failed checks or repeat an exhausted experiment",
        "diagnosis": {
            "missing_evidence": bool(diagnosis.get("missing_evidence")),
            "fixed_limits": deepcopy(diagnosis.get("fixed_limits", {})),
        },
    }


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sealed(value, version):
    _require(
        isinstance(value, dict)
        and value.get("version") == version
        and value.get("id") == fingerprint({k: v for k, v in value.items() if k != "id"}),
        "The saved research acceptance proof changed",
    )


def _checked_decision(decision, config_id):
    from research.automatic_decision import VERSION, descriptor

    _sealed(decision, VERSION)
    _require(
        decision.get("policy_id") == descriptor()["id"]
        and decision.get("historical_candidate") is True
        and decision.get("independently_validated") is False
        and decision.get("selected_config_id") == config_id
        and decision.get("outcome") == "insufficient_evidence"
        and decision.get("reasons") == [HISTORICAL_DISCLOSURE],
        "The selected account did not pass the unchanged historical decision gates",
    )
    checks = decision.get("checks", [])
    _require(
        len(checks) == 2
        and {r.get("kind") for r in checks} == {"higher_costs", "neighboring_settings"}
        and all(r.get("passed") is True for r in checks),
        "All cost and neighborhood checks are required before automatic saving",
    )
    neighbor = next(row for row in checks if row["kind"] == "neighboring_settings")
    tested, passing = neighbor.get("tested"), neighbor.get("passing")
    _require(
        type(tested) is int
        and 2 <= tested <= 4
        and type(passing) is int
        and 0 <= passing <= tested
        and passing / tested >= 0.75,
        "The native neighboring-settings programme is incomplete",
    )
    return tested


def _checked_native(report, native, contract):
    from research.automatic_design import descriptor as design_policy
    from research.automatic_design import reasons as design_reasons
    from research.automatic_research import _entry_dates, _score
    from research.report_contract import settings_identity

    _require(isinstance(native, dict), "The exact selected native account is unavailable")
    validate_settings(contract, native["strategies"])
    required = {
        "strategies",
        "summary",
        "ledger",
        "equity_curve",
        "execution",
        "engine_records",
        "evaluation_basis",
        "automatic_stock_condition",
        "automatic_design",
        "portfolio_constraint_evidence",
    }
    _require(required <= native.keys(), "The selected native account is incomplete")
    # Native presentation adds report metadata/charts after sealing an account.
    # Every retained account field except its derived analysis must still match.
    for key, value in native.items():
        if key not in ("automatic_research", "analysis"):
            _require(
                report.get(key) == value,
                "The displayed account differs from validated native evidence",
            )
    _require(
        native["automatic_stock_condition"].get("hypothesis", {}).get("id") == "none",
        "Automatic saving cannot change the supplied scanner signals",
    )
    _score(native["summary"])
    _require(native["summary"]["net_return_pct"] > 0, "The selected net return must be positive")
    audit = native["portfolio_constraint_evidence"]
    _sealed(audit, "research-portfolio-constraint-evidence-v1")
    projection = {
        "policy_version": native.get("policy_version"),
        "ledger": native["ledger"],
        "strategies": native["strategies"],
        "execution": native["execution"],
        "orders": native["engine_records"]["orders"],
    }
    _require(
        audit.get("status") == "supported"
        and audit.get("reasons") == []
        and audit.get("risk_limits") == contract["risk_limits"]
        and audit.get("report_evidence_id") == fingerprint(projection),
        "The native capital, fill or hard-risk proof is incomplete",
    )
    design = native["automatic_design"]
    row = {
        "summary": native["summary"],
        "entry_dates": _entry_dates(native),
        "automatic_design": design,
    }
    _require(
        not design_reasons(row, design_policy()), "The native sample or distribution checks failed"
    )
    ledger = [row for row in native["ledger"] if row.get("status") == "closed"]
    _require(
        len(ledger) == native["summary"]["closed_trades"]
        and design["support_ledger_id"]
        == fingerprint(
            [
                {
                    key: row[key]
                    for key in (
                        "strategy_id",
                        "symbol",
                        "entry_date",
                        "entry_timestamp",
                        "exit_date",
                        "exit_timestamp",
                        "quantity",
                        "pnl",
                    )
                    if key in row
                }
                for row in ledger
            ]
        ),
        "The native sample proof no longer matches the saved ledger",
    )
    _require(
        all(
            row["config"]["initial_capital"] == native["summary"]["initial_capital"]
            for row in native["strategies"]
        ),
        "The selected capital does not match every trading configuration",
    )
    return settings_identity(native["strategies"])


def validate_full_search_result(report, contract):
    """Admit one exact completed native search account before Keep/choose.

    This reads existing evidence only: no backtest, new decision, substituted
    settings or relaxed financial gate. The returned receipt retains the honest
    historical disclosure. The caller must load the owner-bound immutable job
    and use its exact result artifact in native Keep/choose revision guards.
    """
    try:
        return _validate_full_search_result(report, contract)
    except (KeyError, TypeError, IndexError, StopIteration, AttributeError) as error:
        raise ValueError("The complete native search acceptance evidence is unavailable") from error


def _validate_full_search_result(report, contract):
    from research.automatic_decision import descriptor as decision_policy
    from research.automatic_design import descriptor as design_policy
    from research.automatic_research import _score
    from research.report_contract import settings_identity

    findings, study = report["automatic_research"], report["experiment"]
    recipe, verdict = findings["recipe"], findings["operating_validation"]
    _sealed(recipe, "automatic-hypotheses-v16")
    _sealed(verdict, "research-operating-validation-v1")
    validate_settings(contract, report["strategies"])
    _require(
        recipe.get("execution_intent")
        == {
            "version": INTENT_POLICY,
            "hypothesis_ids": ["none"],
            "style": contract["style"],
            "engine": contract["engine"],
            "objective": contract["objective"],
            "risk_limits_id": fingerprint(contract["risk_limits"]),
        }
        and recipe.get("automatic_decision") == decision_policy()
        and recipe.get("automatic_design") == design_policy()
        and recipe["budget"]["proposals"] == 408
        and recipe["budget"]["max_simulations"] == 512
        and recipe["budget"]["fits"] == 6,
        "The reviewed search or financial validation policy changed",
    )
    counts = findings["counts"]
    _require(
        counts.get("proposals") == 408
        and type(counts.get("simulations")) is int
        and type(counts.get("attempts_reserved")) is int
        and 0 <= counts["simulations"] <= counts["attempts_reserved"] <= 512
        and counts["attempts_reserved"] - counts["simulations"] <= 64,
        "The complete original search and attempt accounting are required",
    )
    config_id = settings_identity(report["strategies"])
    capital = report["summary"]["initial_capital"]
    _require(
        findings.get("selected_config_id") == study.get("recommendation_id") == config_id
        and study.get("selected_strategies") == report["strategies"]
        and verdict.get("selected_config_id") == config_id
        and verdict.get("capital") == capital
        and verdict.get("historically_validated") is True
        and verdict.get("independently_validated") is False,
        "The accepted result does not match its exact capital, configuration or historical proof",
    )
    embedded = study["selected_reports"][config_id]
    native = {key: value for key, value in embedded.items() if key != "automatic_research"}
    _checked_native(report, native, contract)
    mode = recipe["validation_mode"]
    _require(
        mode in ("exploratory_only", "historical_only", "chronological"),
        "Unknown frozen validation mode",
    )
    decision = findings["decision"]
    tested = _checked_decision(decision, config_id)
    outcome = verdict.get("outcome")
    if outcome == "validated":
        _require(
            verdict.get("reasons") == []
            and verdict.get("selection_changed") is False
            and verdict.get("selected_report_id") == fingerprint(native)
            and verdict.get("reused_selected_report_id") == verdict["selected_report_id"]
            and verdict.get("decision") == decision,
            "The changed operating account or its validation seal changed",
        )
        strict = mode != "exploratory_only"
        periods = ["check1", "check2", "check3"] if strict else ["fit1", "fit2", "fit3"]
        expected = {
            (period, stress)
            for period in periods
            for stress in ((False, True) if strict else (False,))
        }
        diagnostics = verdict.get("diagnostics", [])
        _require(
            len(diagnostics) == len(expected)
            and {(row.get("period"), row.get("stress")) for row in diagnostics} == expected
            and all(
                row.get("passed") is True
                and row.get("reasons") == []
                and row.get("independently_validated") is False
                for row in diagnostics
            )
            and verdict.get("accounts") == 3 + tested + (14 if strict else 6),
            "All frozen time-window and higher-cost diagnostics must pass",
        )
        if strict:
            chronological = verdict.get("chronological", {})
            _require(
                chronological.get("status") == "supported"
                and chronological.get("qualified") is True
                and chronological.get("reasons") == [],
                "The unchanged chronological checks failed",
            )
    elif outcome == "unchanged":
        _require(
            verdict.get("accounts") == 0
            and verdict.get("primary_decision_id") == decision["id"]
            and verdict.get("reasons") == decision["reasons"],
            "The unchanged account is not bound to its original native decision",
        )
        if mode == "exploratory_only":
            prefixes = decision.get("prefix_diagnostics", [])
            _require(
                len(prefixes) == 3
                and {row.get("period") for row in prefixes} == {"fit1", "fit2", "fit3"}
                and all(row.get("independently_validated") is False for row in prefixes),
                "The original frozen prefix diagnostics are incomplete",
            )
            for row in prefixes:
                _require(
                    row.get("passed") is not False and not row.get("reasons"),
                    "A recorded original prefix diagnostic failed",
                )
                _score(row["summary"])
        else:
            folds = findings.get("walk_forward", {}).get("folds", [])
            final = findings.get("final", {})
            recommendation = findings.get("recommendation_checks", {})
            _require(
                len(folds) == 3
                and all(row.get("eligible") is True and row.get("reasons") == [] for row in folds)
                and recommendation.get("status") == "supported"
                and recommendation.get("qualified") is True
                and (
                    final.get("supports") is True
                    or (mode == "historical_only" and final.get("historical_checks_passed") is True)
                ),
                "The original later-window, final or recommendation checks failed",
            )
    else:
        raise ValueError("No native search account passed the complete validation")
    body = {
        "version": "research-full-search-acceptance-v1",
        "config_id": config_id,
        "capital": capital,
        "outcome": outcome,
        "native_report_id": fingerprint(native),
        "validation_id": verdict["id"],
        "decision_id": decision["id"],
        "historically_validated": True,
        "independently_validated": False,
        "disclosures": [HISTORICAL_DISCLOSURE],
    }
    return {**body, "id": fingerprint(body)}
