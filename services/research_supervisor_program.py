"""Worker-only execution of a frozen, finite historical research remedy."""

import json
from copy import deepcopy

from database.research_db import ResearchExperiment, ResearchRequest, ResearchSupervisor
from research import capital_planning as capital
from research.automatic_actions import entry_prices, request_prices
from research.operating_loop import chronological_assessment
from research.operating_validation import build_plan
from research.operating_validation import run as validate
from research.recommendation_plan import challenges
from research.report_contract import fingerprint, settings_identity
from services import research_supervisor as supervisor
from services import scanner_research_service as service

_ALLOCATION_ANNOTATIONS = frozenset(
    (
        "allocation_active_symbols",
        "allocation_cluster",
        "allocation_cluster_before",
        "allocation_cluster_capacity_shares",
        "allocation_cluster_count",
        "allocation_feature_id",
        "allocation_opening_equity",
        "allocation_symbol_capacity_shares",
        "allocation_weight",
    )
)


def _economic_execution(report):
    """Exact account/trade comparison, excluding named non-economic receipts.

    Equal total profit alone is insufficient: orders, rows, marks, attribution,
    costs, constraints, filter hypothesis and input/window identities must also
    match. Only named allocation annotations and the settings-bound gate receipt
    ID are excluded; unknown fields remain in the comparison.
    """
    keys = (
        "summary",
        "equity_curve",
        "ledger",
        "per_strategy",
        "engine_records",
        "coverage",
        "missing_marks",
        "evaluation_basis",
        "research_opportunity_basis",
        "automatic_stock_condition",
    )
    if not isinstance(report, dict) or any(key not in report for key in keys):
        return None
    if not isinstance(report["ledger"], list) or not report["ledger"]:
        return None
    projection = {key: report[key] for key in keys if key != "ledger"}
    condition = report["automatic_stock_condition"]
    if not isinstance(condition, dict) or "hypothesis" not in condition:
        return None
    # Each gate receipt also binds the settings at which it was applied. Its
    # hash may change with allocation even when the exact filter hypothesis,
    # opportunities and every resulting economic ledger row are identical.
    # Preserve the two receipt identities in the skip proof, but compare their
    # economic output here; unknown condition fields still prevent a skip.
    projection["automatic_stock_condition"] = {
        key: value for key, value in condition.items() if key != "gate_id"
    }
    ledger = []
    for row in report["ledger"]:
        if not isinstance(row, dict):
            return None
        current = dict(row)
        detail = row.get("execution_details")
        if isinstance(detail, dict):
            current["execution_details"] = {
                key: value for key, value in detail.items() if key not in _ALLOCATION_ANNOTATIONS
            }
        ledger.append(current)
    projection["ledger"] = ledger
    return projection


def _no_effect_conclusion(chosen, seed, proposal, recipe):
    """Reject one audited effect probe; never imply challenges were passed.

    Funding repairs and any already-started validation retain the complete
    existing programme. The caller only invokes this before challenge admission.
    """
    if proposal["remedy"] == "funding_and_size" or recipe["validation_mode"] != "exploratory_only":
        return None
    current, previous = _economic_execution(chosen), _economic_execution(seed)
    if current is None or previous is None or current != previous:
        return None
    native_id = fingerprint(chosen)
    effect = {
        "version": "research-remedy-effect-v1",
        "status": "unchanged",
        "seed_job_id": proposal["seed_job_id"],
        "seed_result_artifact": proposal["source_result_artifact"],
        "selected_report_id": native_id,
        "economic_execution_id": fingerprint(current),
        "seed_gate_id": seed["automatic_stock_condition"].get("gate_id"),
        "selected_gate_id": chosen["automatic_stock_condition"].get("gate_id"),
        "probe_accounts": 1,
        "additional_validation_accounts": 0,
    }
    effect["id"] = fingerprint(effect)
    conclusion = {
        "version": "research-operating-validation-v1",
        "outcome": "rejected",
        "historically_validated": False,
        "independently_validated": False,
        "selected_config_id": settings_identity(chosen["strategies"]),
        "capital": chosen["summary"]["initial_capital"],
        "selected_report_id": native_id,
        "selection_changed": False,
        "scope": "Exact no-economic-change rejection; no challenge validation or acceptance",
        "reasons": [
            "Changed settings produced exactly the same native account and economic execution; "
            "skipped remaining challenges and retained the verified development seed"
        ],
        "accounts": 0,
        "diagnostics": [],
        "no_effect": effect,
    }
    return {**conclusion, "id": fingerprint(conclusion)}


def _repeat_sources(store, owner, marker, chosen, accounts):
    """Yield bounded owned complete branches with matching normal observations.

    The native checkpoint is replay-verified without any engine calls. Missing
    legacy checkpoints simply cannot prove repetition and keep full validation.
    """
    from research.economic_repetition import observed
    from services.research_validation_scorecard import build as scorecard

    identity = observed(chosen)
    if identity is None:
        return
    with store.sessions() as db:
        row = db.get(ResearchSupervisor, marker["supervisor_id"])
        if row is None or row.owner != owner or row.root_job_id != marker["root_job_id"]:
            raise ValueError("Economic repetition history ownership changed")
        ledger = json.loads(row.state)
    if len(ledger["proposals"]) > supervisor.ADAPTIVE_EXPERIMENT_LIMIT:
        raise ValueError("Economic repetition history exceeds its frozen bound")
    for proposal in ledger["proposals"]:
        if proposal["id"] == marker["proposal_id"] or proposal.get("outcome") != "rejected":
            continue
        if proposal.get("no_effect") or not proposal.get("job_id"):
            continue
        job = service.get_job(store, owner, proposal["job_id"])
        if job.status != "completed" or not job.result_artifact:
            continue
        report = service.read_artifact(store, job.result_artifact)["result"]
        conclusion = supervisor._verified_conclusion(
            report, marker["supervisor_id"], proposal["id"], marker["root_job_id"], store, owner
        )
        if conclusion.get("no_effect") or observed(report) != identity:
            continue
        card = scorecard(report)
        if not card.get("eligible") or "higher_costs" not in card.get("failed_gate_keys", []):
            continue
        with store.sessions() as db:
            experiment = db.get(ResearchExperiment, job.id)
            checkpoint_id = experiment.checkpoint if experiment else None
            checkpoint_identity = experiment.identity if experiment else None
        if not checkpoint_id:
            continue
        checkpoint = service.read_artifact(store, checkpoint_id)
        if (
            checkpoint.get("identity") != checkpoint_identity
            or checkpoint.get("policy_version") != chosen.get("policy_version")
            or checkpoint.get("policy_version") != report.get("policy_version")
            or checkpoint.get("state", {}).get("phase") != "calculation"
        ):
            continue
        saved = checkpoint["state"].get("calculation", {})
        plan, validation = saved.get("validation_plan"), saved.get("validation")
        native = {
            k: v
            for k, v in report.items()
            if k not in ("supervisor_validation", "automatic_research", "experiment")
        }
        if (
            saved.get("proposal_id") != proposal["id"]
            or saved.get("source_result_artifact") != conclusion["source_result_artifact"]
            or not isinstance(plan, dict)
            or not isinstance(validation, dict)
            or validation.get("conclusion") != conclusion["operating_validation"]
            or plan.get("inputs", {}).get("validation_mode") != "exploratory_only"
        ):
            continue

        def forbidden(*args, **kwargs):
            raise ValueError("Incomplete prior validation cannot certify a repeated failed gate")

        verified = validate(
            plan,
            selected_report=native,
            evaluate=forbidden,
            audit=accounts.audit,
            saved=deepcopy(validation),
        )[0]
        if verified != conclusion["operating_validation"]:
            raise ValueError("The prior economic repetition validation changed")
        yield {
            "job_id": job.id,
            "artifact": job.result_artifact,
            "checkpoint": checkpoint_id,
            "report": native,
            "plan": plan,
            "validation": validation,
        }


def _repeated_cost_conclusion(chosen, plan, validation, previous, proposal, audit):
    """A witnessed unchanged *failed mandatory gate*, not full equivalence."""
    from research.automatic_decision import support_reasons
    from research.economic_repetition import observed
    from research.operating_validation import _row

    old_plan, old_state = previous["plan"], previous["validation"]
    if plan["inputs"]["validation_mode"] != "exploratory_only" or plan["blocked_reasons"]:
        return None
    if plan["policy"] != old_plan["policy"]:
        return None
    current_requests = {row["role"]: row for row in plan["requests"]}
    old_requests = {row["role"]: row for row in old_plan["requests"]}
    # Whole baseline requests bind capital, cost/slippage, hypothesis, dates and
    # all sizing/risk controls. Different capital cannot pass this comparison.
    for role in ("full_baseline", "cost_baseline"):
        if current_requests[role] != old_requests[role]:
            return None
    if plan["inputs"]["hypothesis"] != old_plan["inputs"]["hypothesis"]:
        return None
    baseline_request, cost_request = (
        current_requests[k] for k in ("full_baseline", "cost_selected")
    )
    full = validation["reports"][baseline_request["id"]]["report"]
    cost = validation["reports"][cost_request["id"]]["report"]
    old_full = old_state["reports"][old_requests["full_baseline"]["id"]]["report"]
    old_cost = old_state["reports"][old_requests["cost_selected"]["id"]]["report"]
    old_baseline = old_state["reports"][old_requests["cost_baseline"]["id"]]["report"]
    if (
        observed(chosen) != observed(previous["report"])
        or observed(full) != observed(old_full)
        or observed(cost) != observed(old_cost)
    ):
        return None
    fresh = _row(cost, cost_request, audit)
    baseline = _row(old_baseline, old_requests["cost_baseline"], audit)
    if any(
        row["portfolio_constraint_evidence"].get("status") != "supported"
        for row in (fresh, baseline)
    ):
        return None
    if fresh.get("automatic_design", {}).get("qualified") is not True:
        return None
    failures = support_reasons(fresh["summary"], baseline["summary"], dates=fresh["entry_dates"])
    old_check = next(
        (
            row
            for row in old_state["conclusion"]["decision"]["checks"]
            if row["kind"] == "higher_costs"
        ),
        None,
    )
    if not failures or not old_check or old_check["passed"] is not False:
        return None
    effect = {
        "version": "research-repeated-failed-gate-v1",
        "status": "unchanged",
        "kind": "repeated_failed_gate",
        "check": "higher_costs",
        "seed_job_id": proposal["seed_job_id"],
        "seed_result_artifact": proposal["source_result_artifact"],
        "matched_job_id": previous["job_id"],
        "matched_result_artifact": previous["artifact"],
        "matched_checkpoint": previous["checkpoint"],
        "matched_validation_id": old_state["conclusion"]["id"],
        "selected_report_id": fingerprint(chosen),
        "observed_execution_id": observed(chosen),
        "cost_report_id": fingerprint(cost),
        "cost_baseline_report_id": fingerprint(old_baseline),
        "baseline_request_id": current_requests["cost_baseline"]["id"],
        "probe_accounts": 3,
        "additional_validation_accounts": 2,
        "skipped_validation_accounts": len(plan["requests"]) - 2,
        "full_validation_completed": False,
        "all_scenario_equivalence_proven": False,
        "failed_check_reasons": failures,
    }
    effect["id"] = fingerprint(effect)
    value = {
        "version": "research-operating-validation-v1",
        "plan_id": plan["id"],
        "outcome": "rejected",
        "historically_validated": False,
        "independently_validated": False,
        "selected_config_id": settings_identity(chosen["strategies"]),
        "capital": plan["capital"],
        "selected_report_id": fingerprint(chosen),
        "selection_changed": False,
        "scope": "Repeated failed mandatory cost gate; remaining checks were not run; no full-equivalence or acceptance claim",
        "reasons": [
            "A charged native cost probe reproduced an already failed mandatory comparison against the identical frozen baseline; remaining challenges were not run"
        ],
        "accounts": 2,
        "diagnostics": [],
        "no_effect": effect,
    }
    return {**value, "id": fingerprint(value)}


def _validated_report(chosen, conclusion, recipe, marker, root_artifact, *, feasibility_plan=None):
    reasons = list(conclusion["reasons"])
    if recipe["validation_mode"] == "exploratory_only" and any(
        not d.get("passed") for d in conclusion.get("diagnostics", [])
    ):
        reasons.append(
            "A declared historical prefix failed; supervisor retains the original result"
        )
    accepted = conclusion["outcome"] == "validated" and not reasons
    value = {
        "version": "research-supervisor-validation-v1",
        "outcome": "validated"
        if accepted
        else "rejected"
        if conclusion["outcome"] == "validated"
        else conclusion["outcome"],
        "historically_validated": accepted,
        "independently_validated": False,
        "selected_config_id": settings_identity(chosen["strategies"]),
        "capital": chosen["summary"]["initial_capital"],
        "native_report_id": fingerprint(chosen),
        "operating_validation": conclusion,
        "supervisor_id": marker["supervisor_id"],
        "proposal_id": marker["proposal_id"],
        "source_result_artifact": root_artifact,
        "scope": "Historical research only",
        "reasons": reasons,
        **({"no_effect": conclusion["no_effect"]} if conclusion.get("no_effect") else {}),
    }
    if conclusion["outcome"] == "blocked" and feasibility_plan is not None:
        if (
            feasibility_plan.get("selected_config_id") != value["selected_config_id"]
            or feasibility_plan.get("capital") != value["capital"]
            or feasibility_plan.get("id") != conclusion.get("plan_id")
            or feasibility_plan.get("id")
            != fingerprint({k: v for k, v in feasibility_plan.items() if k != "id"})
            or (
                feasibility_plan.get("inputs", {}).get("capacity_counts") is not None
                and not isinstance(feasibility_plan["inputs"]["capacity_counts"], dict)
            )
            or not isinstance(feasibility_plan.get("unresolved_constraints"), dict)
        ):
            raise ValueError(
                "Blocked feasibility metadata does not match the native operating plan"
            )
        value["feasibility"] = {
            "version": "research-feasibility-v1",
            "source_settings_id": value["selected_config_id"],
            "counts": deepcopy(feasibility_plan["inputs"]["capacity_counts"]),
            "unresolved_constraints": deepcopy(feasibility_plan["unresolved_constraints"]),
            "scope": "feasibility_only",
        }
    result = deepcopy(chosen)
    result["supervisor_validation"] = {**value, "id": fingerprint(value)}
    return result


def _funding_seed_capital(report, original, *, seed_id, root_id):
    """A checked funded branch keeps its own capital floor on further repair."""
    return (
        original["portfolio"]["capital"]
        if seed_id == root_id
        else report["summary"]["initial_capital"]
    )


def _requires_feasibility_seed(previous, proposal, recipe):
    """Blocked accounts may inform capacity repair, never performance selection."""
    if recipe.get("validation_mode") != "exploratory_only":
        raise ValueError("Only exploratory development evidence may seed a remedy")
    if previous.get("outcome") == "rejected":
        return False
    if previous.get("outcome") == "blocked" and proposal.get("remedy") == "funding_and_size":
        return True
    raise ValueError("Blocked development evidence may seed only a funding and sizing repair")


def run(store, owner, evidence, spec, *, saved, checkpoint, progress, cancelled, job_id, **unused):
    from research.connectors.vectorbt_portfolio import evaluate
    from research.supervisor_accounts import FrozenAccounts
    from services.research_portfolio import _prepare_inputs, validate_submission

    validate_submission(evidence, "portfolio_backtest", spec)
    marker = evidence["supervisor_program"]
    if marker.get("version") != supervisor.VERSION or not job_id:
        raise ValueError("Invalid saved supervisor programme")
    with store.sessions.begin() as db:
        service.write_guard(db)
        row = db.get(ResearchSupervisor, marker["supervisor_id"])
        if row is None or row.owner != owner or row.root_job_id != marker["root_job_id"]:
            raise ValueError("Supervisor account authorization changed")
        state = json.loads(row.state)
        reviewed_contract = state.get("research_context", {}).get("contract")
        proposal = next(p for p in state["proposals"] if p["id"] == marker["proposal_id"])
        request = db.get(ResearchRequest, (owner, "supervisor_" + proposal["id"]))
        if request is None or request.job_id != job_id or proposal["job_id"] not in (None, job_id):
            raise ValueError("Supervisor experiment job binding changed")
        proposal["job_id"] = job_id
        supervisor._put(row, state)
        root_id, root_artifact = row.root_job_id, row.root_artifact
    parent, bundle, report, recipe = supervisor._parent(store, owner, root_id)
    if parent.result_artifact != root_artifact:
        raise ValueError("Supervisor saved source changed")
    seed_id = proposal.get("seed_job_id", root_id)
    feasibility_seed = False
    if seed_id != root_id:
        seed = service.get_job(store, owner, seed_id)
        if seed.status != "completed" or seed.result_artifact != proposal["source_result_artifact"]:
            raise ValueError("Supervisor development seed changed")
        with store.sessions() as db:
            ledger = json.loads(db.get(ResearchSupervisor, marker["supervisor_id"]).state)
            previous = next(p for p in ledger["proposals"] if p.get("job_id") == seed_id)
        feasibility_seed = _requires_feasibility_seed(previous, proposal, recipe)
        report = service.read_artifact(store, seed.result_artifact)["result"]
        verified = supervisor._verified_conclusion(
            report, marker["supervisor_id"], previous["id"], root_id, store, owner
        )
        if feasibility_seed and verified["outcome"] != "blocked":
            raise ValueError("The blocked funding seed changed its native conclusion")
    elif proposal.get("source_result_artifact") != root_artifact:
        raise ValueError("Supervisor original seed changed")
    planning_report = (
        supervisor.planning_report(store, owner, seed, report) if feasibility_seed else report
    )
    if proposal.get("experiment_policy") == supervisor.EXPERIMENT_POLICY:
        # Reconstruct the registered concrete option before any debit/engine
        # call. A stored prose choice cannot authorize changed settings.
        context = {**state, "source_result_artifact": proposal["source_result_artifact"]}
        catalog = supervisor._experiment_catalog(planning_report, recipe, context)
        option = supervisor._concrete_option(
            {"experiment_options": catalog["options"]},
            proposal["remedy"],
            proposal.get("candidate", {}).get("id"),
        )
        if option["settings"] != proposal["settings"]:
            raise ValueError("The worker's concrete experiment settings changed")
    original = service.read_artifact(store, bundle["inputs_artifact"])
    if original["portfolio"]["engine"] != "vectorbt":
        raise ValueError("This supervisor requires the existing native VectorBT account")
    if reviewed_contract:
        from services.research_playbook import describe, validate_settings

        if describe(original["portfolio"]) != reviewed_contract:
            raise ValueError("The worker's reviewed research contract changed")
        validate_settings(reviewed_contract, proposal["settings"])
    original, _, _ = _prepare_inputs(
        store,
        owner,
        original,
        {"portfolio": original["portfolio"], "versions": original["versions"]},
        saved=None,
        checkpoint=lambda *_: None,
        progress=lambda *_: None,
        cancelled=cancelled,
        activity=None,
    )
    state = deepcopy(
        (saved or {}).get("calculation")
        or {
            "version": supervisor.VERSION,
            "proposal_id": proposal["id"],
            "source_result_artifact": root_artifact,
            "capacity": [],
            "selected_report": None,
            "validation": None,
            "validation_plan": None,
        }
    )
    if (
        state.get("version") != supervisor.VERSION
        or state.get("proposal_id") != proposal["id"]
        or state.get("source_result_artifact") != root_artifact
    ):
        raise ValueError("Supervisor checkpoint version changed")
    inputs_id = service.save_artifact(store, evidence)

    def persist():
        checkpoint(
            {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": state},
            {
                "completed": min(99, 100 * spent() / proposal["reserved_accounts"]),
                "total": 100,
                "stage": "validation",
            },
        )
        # Publication is synchronous: count only accounts whose checkpoint has
        # been durably stored. A crash between computation and publication keeps
        # its debit as a lost attempt, including across restarts and LLM turns.
        completed = (
            len(state["capacity"])
            + int(state["selected_report"] is not None)
            + len((state["validation"] or {}).get("reports", {}))
        )
        with store.sessions.begin() as db:
            service.write_guard(db)
            row = db.get(ResearchSupervisor, marker["supervisor_id"])
            ledger = json.loads(row.state)
            item = next(p for p in ledger["proposals"] if p["id"] == proposal["id"])
            if not item.get("completed", 0) <= completed <= item["spent"]:
                raise ValueError("Supervisor durable account accounting changed")
            item["completed"] = completed
            supervisor._put(row, ledger)

    def spent():
        with store.sessions() as db:
            row = db.get(ResearchSupervisor, marker["supervisor_id"])
            return next(
                p["spent"] for p in json.loads(row.state)["proposals"] if p["id"] == proposal["id"]
            )

    def reserve():
        cancelled()
        supervisor.reserve_account(store, owner, marker["supervisor_id"], proposal["id"])

    hypothesis = report.get("automatic_stock_condition", {}).get("hypothesis")
    if hypothesis is None:
        hypothesis = (
            report["strategies"][0].get("entry_condition", {}).get("hypothesis", {"id": "none"})
        )
    with FrozenAccounts(original, evaluate, cancelled) as accounts:
        baseline_hypothesis = next(h for h in accounts.context.catalog() if h["id"] == "none")

        def calculate(settings, hypothesis, period, stress=False):
            if reviewed_contract:
                validate_settings(reviewed_contract, settings)
                if hypothesis.get("id") != "none":
                    raise ValueError("Reviewed research cannot alter supplied scanner signals")
            reserve()
            value = accounts.calculate(settings, hypothesis, period, stress)
            progress(spent(), proposal["reserved_accounts"])
            return value

        if proposal["remedy"] == "funding_and_size":
            frozen = capital.selected_account_plan(
                report["strategies"],
                _funding_seed_capital(report, original, seed_id=seed_id, root_id=root_id),
                entry_prices(report, report["strategies"], accounts.window("search")["snapshot"]),
                request_prices=request_prices(
                    report, report["strategies"], accounts.window("search")["snapshot"]
                ),
                max_accounts=4 if recipe["validation_mode"] == "exploratory_only" else 3,
                version=proposal.get("capital_policy_version", capital.CLOSED_LOOP_VERSION),
                **(
                    {
                        "permissions": {
                            key: key in state["research_context"]["flow"]["adjustable"]
                            for key in ("capital", "order_size_pct")
                        }
                    }
                    if state.get("research_context", {}).get("flow")
                    and not {"capital", "order_size_pct"}.issubset(
                        state["research_context"]["flow"]["adjustable"]
                    )
                    else {}
                ),
                **(
                    {"request_risks": capital.selected_account_risks(report, report["strategies"])}
                    if proposal.get("capital_policy_version") == capital.ATR_VERSION
                    else {}
                ),
            )
            receipts = []
            while (index := capital.selected_account_next(frozen, receipts)) is not None:
                settings = capital.selected_account_settings(frozen, index)
                offset = len(receipts)
                if offset < len(state["capacity"]):
                    value = state["capacity"][offset]
                    if (
                        accounts.audit(
                            value,
                            {
                                "settings": settings,
                                "hypothesis": hypothesis,
                                "period": "search",
                                "stress": False,
                            },
                        )
                        != value["portfolio_constraint_evidence"]
                    ):
                        raise ValueError("Saved supervisor capacity evidence changed")
                else:
                    value = calculate(settings, hypothesis, "search")
                    state["capacity"].append(value)
                    persist()
                receipts.append(capital.selected_account_receipt(value, frozen, index))
            if len(state["capacity"]) != len(receipts):
                raise ValueError(
                    "Saved supervisor capacity sequence continued past its frozen stop"
                )
            planning = capital.selected_account_finish(frozen, receipts)
            point = planning["operating_point"]
            if point is None:
                raise ValueError("No native capacity-supported remedy was found")
            chosen = next(
                v
                for v, receipt in zip(state["capacity"], receipts, strict=True)
                if receipt["option_index"] == point["option_index"]
            )
            counts = next(
                r["counts"] for r in receipts if r["option_index"] == point["option_index"]
            )
            # Native normalization stores capital as float. Freeze that exact
            # verified account for all later identities, rather than the
            # planner's economically equal integer representation.
            point = {
                **point,
                "capital": chosen["summary"]["initial_capital"],
                "settings": deepcopy(chosen["strategies"]),
            }
        else:
            settings = proposal["settings"]
            if state["selected_report"] is None:
                state["selected_report"] = calculate(settings, hypothesis, "search")
                persist()
            chosen = state["selected_report"]
            request = {
                "settings": settings,
                "hypothesis": hypothesis,
                "period": "search",
                "stress": False,
            }
            if accounts.audit(chosen, request) != chosen[
                "portfolio_constraint_evidence"
            ] or settings_identity(chosen["strategies"]) != settings_identity(settings):
                raise ValueError("Saved supervisor selected account changed")
            # One charged native probe can prove a remedy has no economic
            # effect. Reject it rather than spend thirteen more accounts on a
            # point that cannot strictly improve. Never truncate an already
            # admitted validation, funding repair or effectful point.
            if state["validation_plan"] is None and state["validation"] is None:
                effect = _no_effect_conclusion(chosen, report, proposal, recipe)
                if effect is not None:
                    if state.get("effect_conclusion") not in (None, effect):
                        raise ValueError("Saved supervisor effect comparison changed")
                    state["effect_conclusion"] = effect
                    persist()
                    return _validated_report(
                        chosen, effect, recipe, marker, root_artifact
                    ), evidence
            frozen = capital.selected_account_plan(
                settings,
                settings[0]["config"]["initial_capital"],
                entry_prices(chosen, settings, accounts.window("search")["snapshot"]),
                max_accounts=4,
                request_prices=request_prices(
                    chosen, settings, accounts.window("search")["snapshot"]
                ),
                version=proposal.get("capital_policy_version", capital.CLOSED_LOOP_VERSION),
                **(
                    {"request_risks": capital.selected_account_risks(chosen, settings)}
                    if proposal.get("capital_policy_version") == capital.ATR_VERSION
                    else {}
                ),
            )
            counts = capital.selected_account_receipt(chosen, frozen, 0)["counts"]
            point = {
                "capital": chosen["summary"]["initial_capital"],
                "settings": chosen["strategies"],
            }
        mode = recipe["validation_mode"]
        plan_inputs = {
            "operating_point": point,
            "baseline_settings": recipe["baseline"],
            "hypothesis": hypothesis,
            "baseline_hypothesis": baseline_hypothesis,
            "neighbors": challenges(point["settings"], recipe["ranges"])["neighbors"],
            "periods": {
                "search": "search",
                **(
                    {"prefixes": ["fit1", "fit2", "fit3"]}
                    if mode == "exploratory_only"
                    else {"later": ["check1", "check2", "check3"], "final": "final"}
                ),
            },
            "validation_mode": mode,
            "remaining_accounts": proposal["reserved_accounts"] - spent(),
            "capacity_counts": counts,
        }
        if state["validation_plan"]:
            plan_inputs["remaining_accounts"] = state["validation_plan"]["inputs"][
                "remaining_accounts"
            ]
        plan = build_plan(**plan_inputs)
        if state["validation_plan"] is not None and state["validation_plan"] != plan:
            raise ValueError("The supervisor frozen validation programme changed")
        state["validation_plan"] = plan
        persist()

        # A normal match is only a lead. Probe the existing fixed request
        # prefix and reject early only when a mandatory failed cost gate is
        # reproduced under the exact same audited baseline. If it differs,
        # validate() resumes this prefix and still runs every original check.
        prefix = state.get("validation")
        if (
            proposal["remedy"] != "funding_and_size"
            and mode == "exploratory_only"
            and not plan["blocked_reasons"]
            and (prefix is None or len(prefix.get("reports", {})) <= 2)
            and not (prefix or {}).get("conclusion")
        ):
            sources = _repeat_sources(store, owner, marker, chosen, accounts)
            previous = next(sources, None)
            if previous is not None:
                from research.operating_validation import VERSION as VALIDATION_VERSION
                from research.operating_validation import _row

                if prefix is None:
                    prefix = {
                        "version": VALIDATION_VERSION,
                        "plan_id": plan["id"],
                        "selected_report_id": fingerprint(
                            {k: v for k, v in chosen.items() if k != "automatic_research"}
                        ),
                        "reports": {},
                        "active_request": None,
                        "conclusion": None,
                    }
                    state["validation"] = prefix
                for request in plan["requests"][:2]:
                    item = prefix["reports"].get(request["id"])
                    if item is None:
                        prefix["active_request"] = request["id"]
                        persist()
                        value = calculate(
                            request["settings"],
                            request["hypothesis"],
                            request["period"],
                            request["stress"],
                        )
                        _row(value, request, accounts.audit)
                        prefix["reports"][request["id"]] = {
                            "report": value,
                            "report_id": fingerprint(value),
                        }
                        prefix["active_request"] = None
                        persist()
                    else:
                        if set(item) != {"report", "report_id"} or item["report_id"] != fingerprint(
                            item["report"]
                        ):
                            raise ValueError("Saved economic repetition prefix changed")
                        _row(item["report"], request, accounts.audit)
                effect = None
                while previous is not None:
                    effect = _repeated_cost_conclusion(
                        chosen, plan, prefix, previous, proposal, accounts.audit
                    )
                    if effect is not None:
                        break
                    previous = next(sources, None)
                if effect is not None:
                    if state.get("effect_conclusion") not in (None, effect):
                        raise ValueError("Saved repeated failed-gate conclusion changed")
                    state["effect_conclusion"] = effect
                    persist()
                    return _validated_report(
                        chosen, effect, recipe, marker, root_artifact
                    ), evidence

        def publish(value):
            state["validation"] = value
            persist()

        conclusion, _ = validate(
            plan,
            selected_report=chosen,
            evaluate=calculate,
            audit=accounts.audit,
            saved=state["validation"],
            checkpoint=publish,
            strict_assessment=chronological_assessment,
            cancelled=cancelled,
            available_accounts=lambda: proposal["reserved_accounts"] - spent(),
        )
    return _validated_report(
        chosen, conclusion, recipe, marker, root_artifact, feasibility_plan=plan
    ), evidence
