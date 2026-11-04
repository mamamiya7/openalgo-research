"""Close one bounded remedy loop without changing the frozen search winner."""

from copy import deepcopy

from research.operating_validation import build_plan, descriptor
from research.operating_validation import run as validate
from research.recommendation_plan import challenges
from research.report_contract import fingerprint, settings_identity


def chronological_assessment(reports, plan):
    from research.recommendation_evidence import account_evidence, assess

    def shard(role, period="search", stress=False):
        return account_evidence(reports[role], period=period, binding=plan["id"], stress=stress)

    return assess(
        candidate=shard("full_selected"),
        baseline=shard("full_baseline"),
        stress_candidate=shard("cost_selected", stress=True),
        stress_baseline=shard("cost_baseline", stress=True),
        folds=[
            {
                "candidate": shard(f"window_{i}_normal_selected", f"check{i + 1}"),
                "baseline": shard(f"window_{i}_normal_baseline", f"check{i + 1}"),
                "stress_candidate": shard(f"window_{i}_stress_selected", f"check{i + 1}", True),
                "stress_baseline": shard(f"window_{i}_stress_baseline", f"check{i + 1}", True),
            }
            for i in range(3)
        ],
        sensitivity=[shard(f"neighbor_{i}") for i in range(len(plan["inputs"]["neighbors"]))],
    )


def run(
    result,
    *,
    state,
    recipe,
    baseline,
    baseline_hypothesis,
    available_accounts,
    evaluate,
    audit,
    persist,
    control,
):
    """Use native capacity evidence, then challenge exactly one chosen account.

    An accepted derived row is an explicit historical candidate. Original trial
    rows, trials, objective winner, primary selection and primary verdict survive.
    Rejected/blocked remedies never replace the original financial report.
    """
    if recipe.get("operating_validation") != descriptor():
        raise ValueError("The frozen operating validation policy changed")
    findings = result["automatic_research"]
    planning = findings["capital_planning"]
    point = planning.get("operating_point")
    if point is None:
        value = {
            "version": descriptor()["version"],
            "outcome": "blocked",
            "historically_validated": False,
            "independently_validated": False,
            "reasons": ["No usable capacity-supported operating point was established"],
            "accounts": 0,
        }
        conclusion = {**value, "id": fingerprint(value)}
    else:
        accounts = state["automatic_actions"]["accounts"]
        matched = [r for r in accounts if r["receipt"]["option_index"] == point["option_index"]]
        if len(matched) != 1:
            raise ValueError("Capacity-selected account is missing or ambiguous")
        chosen = matched[0]
        unchanged = (
            settings_identity(point["settings"]) == findings["selected_config_id"]
            and point["capital"] == result["summary"]["initial_capital"]
        )
        if unchanged:
            if chosen["report"]["summary"] != result["summary"] or chosen["report"].get(
                "evaluation_basis"
            ) != result.get("evaluation_basis"):
                raise ValueError("The unchanged operating account differs from its primary report")
            value = {
                "version": descriptor()["version"],
                "outcome": "unchanged",
                "capital": point["capital"],
                "selected_config_id": findings["selected_config_id"],
                "historically_validated": findings["decision"].get("historical_candidate", False),
                "independently_validated": False,
                "accounts": 0,
                "primary_decision_id": findings["decision"]["id"],
                "reasons": deepcopy(findings["decision"]["reasons"]),
                "scope": "Exact primary capital and settings retained; their existing checks reused, with no duplicate account calculations.",
            }
            conclusion = {**value, "id": fingerprint(value)}
        else:
            return _validate_changed(
                result,
                state=state,
                recipe=recipe,
                baseline=baseline,
                baseline_hypothesis=baseline_hypothesis,
                available_accounts=available_accounts,
                evaluate=evaluate,
                audit=audit,
                persist=persist,
                control=control,
                chosen=chosen,
                point=point,
            )
    return _present(result, conclusion, state)


def _validate_changed(
    result,
    *,
    state,
    recipe,
    baseline,
    baseline_hypothesis,
    available_accounts,
    evaluate,
    audit,
    persist,
    control,
    chosen,
    point,
):
    mode = recipe["validation_mode"]
    inputs = {
        "operating_point": point,
        "baseline_settings": baseline,
        "hypothesis": state["selection"]["hypothesis"],
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
        "remaining_accounts": available_accounts(),
        "capacity_counts": chosen["receipt"]["counts"],
    }
    saved_plan = state.get("operating_validation_plan")
    if saved_plan is not None:
        # Available allowance decreases while the plan runs. It is not a
        # reason to reseal a different plan after seeing partial outcomes.
        inputs["remaining_accounts"] = saved_plan["inputs"]["remaining_accounts"]
    plan = build_plan(**inputs)
    if saved_plan is not None and saved_plan != plan:
        raise ValueError("The saved operating point or validation programme changed")
    if saved_plan is None:
        state["operating_validation_plan"] = plan
        persist()

    def checkpoint(value):
        state["operating_validation"] = value
        persist()

    conclusion, _ = validate(
        plan,
        selected_report=chosen["report"],
        evaluate=evaluate,
        audit=audit,
        saved=state.get("operating_validation"),
        checkpoint=checkpoint,
        strict_assessment=chronological_assessment,
        cancelled=control,
        available_accounts=available_accounts,
    )
    return _present(result, conclusion, state, chosen)


def _present(result, conclusion, state, chosen=None):
    findings = result["automatic_research"]
    planning = findings["capital_planning"]
    findings["operating_validation"] = deepcopy(conclusion)
    planning["validation"] = deepcopy(conclusion)
    planning["id"] = fingerprint({k: v for k, v in planning.items() if k != "id"})
    findings["counts"].update(simulations=state["simulations"], attempts_reserved=state["attempts"])
    if conclusion["outcome"] == "validated":
        report = deepcopy(chosen["report"])
        config_id = settings_identity(report["strategies"])
        if config_id != conclusion["selected_config_id"]:
            raise ValueError("Validated operating account identity changed")
        experiment = result["experiment"]
        # Identical settings can reuse a primary row, but a changed capital is
        # included in settings identity and must get a separately bound row.
        row = {
            "config_id": config_id,
            "trial_number": None,
            "strategies": deepcopy(report["strategies"]),
            "summary": deepcopy(report["summary"]),
            "evaluation_basis": deepcopy(report["evaluation_basis"]),
            "hypothesis": deepcopy(state["selection"]["hypothesis"]),
            "provenance": "automatic_operating_validation",
            "operating_validation_id": conclusion["id"],
            "account_capital": conclusion["capital"],
        }
        # Derived capital is not an Optuna trial. Keep every original trial row
        # and expose the separate saveable candidate through the existing
        # automatic-selection fallback in research_candidates.
        experiment["operating_candidate"] = row
        experiment["primary_recommendation_id"] = experiment["recommendation_id"]
        experiment["recommendation_id"] = config_id
        experiment["selected_strategies"] = deepcopy(report["strategies"])
        experiment["evaluation_basis_id"] = report["evaluation_basis"]["evidence_id"]
        experiment["next_action"] = (
            "Review or save this historically checked capital and trade size; independent evidence is still required"
        )
        findings["primary_decision"] = deepcopy(findings["decision"])
        findings["primary_selected_config_id"] = findings["selected_config_id"]
        findings["decision"] = deepcopy(conclusion["decision"])
        findings["selected_config_id"] = config_id
        findings["independently_validated"] = False
        findings["status"] = "inconclusive"
        findings["headline"] = "Capital and trade size checked on historical data"
        findings["selection_basis"] = (
            "One remedy chosen by native capacity constraints, then checked at its exact capital and size; no reranking after validation."
        )
        if "automatic_design" in findings:
            findings["automatic_design"]["selected"] = deepcopy(report["automatic_design"])
        report["automatic_research"] = deepcopy(findings)
        experiment["selected_reports"][config_id] = deepcopy(report)
        report["experiment"] = experiment
        result = report
    elif conclusion["outcome"] != "unchanged":
        result["experiment"]["next_action"] = (
            "Retain the original result; review why the proposed capital and size could not pass all checks"
        )
    # All saved/exported standalone reports retain the exact verdict and remedy
    # receipt, even when the proposed point is rejected rather than selected.
    selected_id = result["experiment"]["recommendation_id"]
    standalone = result["experiment"].get("selected_reports", {}).get(selected_id)
    if standalone is not None:
        standalone["automatic_research"] = deepcopy(findings)
    return result
