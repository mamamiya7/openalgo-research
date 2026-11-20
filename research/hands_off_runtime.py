"""Checkpointed AI refinement inside the existing 408-proposal programme.

The mandatory screens, native accounts and validation remain owned by the
existing walk-forward runner. This module neither simulates nor contacts a model.
"""

from copy import deepcopy

from research.hands_off_plan import build_context, compile_plan
from research.report_contract import fingerprint

POLICY = "hands-off-fixed-signals-v1"
MAX_CALLS = 12


def verify_book(book, recovery=None):
    resolved = {}
    if recovery is not None:
        from research.planning_recovery import verify

        verify(recovery, book)
        resolved = recovery["rejections"]
    if (
        not isinstance(book, dict)
        or book.get("version") != POLICY
        or type(book.get("calls_spent")) is not int
        or not isinstance(book.get("records"), dict)
        or not 0 <= book["calls_spent"] <= MAX_CALLS
        or book["calls_spent"] != len(book["records"])
    ):
        raise ValueError("Hands-off planning checkpoint exceeded its model allowance")
    for key, row in book["records"].items():
        if (
            not isinstance(key, str)
            or len(key) > 64
            or not isinstance(row, dict)
            or row.get("phase") not in ("requesting", "completed")
            or type(row.get("index")) is not int
            or not 0 <= row["index"] < 68
            or row.get("fit")
            not in ("fit1", "fit2", "fit3", "search", "search_seed1", "search_seed2")
            or key != f"{row['fit']}:{row['index']}"
            or not isinstance(row.get("request_id"), str)
            or len(row["request_id"]) != 64
            or not isinstance(row.get("anchor_params"), dict)
            or (row["phase"] == "completed" and not isinstance(row.get("plan"), dict))
            or (row["phase"] == "requesting" and "plan" in row)
        ):
            raise ValueError("Hands-off planning checkpoint contains an invalid request")
    if book.get("active") is not None and (
        not isinstance(book["active"], str) or book["active"] not in book["records"]
    ):
        raise ValueError("Hands-off active model reservation changed")
    requesting = [
        key
        for key, row in book["records"].items()
        if row["phase"] == "requesting" and key not in resolved
    ]
    if len(requesting) > 1 or requesting != (
        [] if book.get("active") is None else [book["active"]]
    ):
        raise ValueError("Hands-off active reservation and request phase disagree")


class HandsOffStopped(Exception):
    def __init__(self, report):
        self.report = report
        super().__init__(report["hands_off_planning"]["stop_reason"])


def run_guard(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HandsOffStopped as stopped:
        return stopped.report


def boundaries(screening, total):
    remaining = total - screening
    return [screening] + ([screening + remaining // 2] if remaining >= 4 else [])


def refinement(
    state,
    current,
    *,
    fit_name,
    index,
    screening,
    total,
    axes,
    baseline,
    reference,
    signal_id,
    style,
    risk_limits,
    callback,
    persist,
    cancelled,
    source_facts=None,
    user_flow=None,
):
    """Return admitted domains and the exact anchor params for one refinement block."""
    points = boundaries(screening, total)
    if index < screening:
        return None
    start = max(p for p in points if p <= index)
    book = state.setdefault(
        "hands_off_planning", {"version": POLICY, "calls_spent": 0, "records": {}, "active": None}
    )
    key = f"{fit_name}:{start}"
    authority = getattr(callback, "controller_update", None)
    annex = state.get("planning_recovery")
    legacy = (authority or {}).get("known_rejection")
    if legacy and key == legacy["boundary_key"] and annex is None:
        from research import planning_recovery as recovery

        annex = recovery.authorize(
            book,
            checkpoint_id=legacy["checkpoint_id"],
            request_id=legacy["request_id"],
            authority_id=authority["id"],
            job_id=authority["job_id"],
        )
        receipt = recovery.legacy_rejection(
            annex,
            checkpoint_id=legacy["checkpoint_id"],
            request_id=legacy["request_id"],
            authority_id=authority["id"],
            error=legacy["error"],
        )
        annex = recovery.attest_rejection(book, annex, receipt, boundary_key=key)
        state["planning_recovery"] = annex
        book["active"] = None
        persist()
    verify_book(book, annex)

    def resolved_record(name, row):
        if annex:
            correction = next(
                (
                    c
                    for c in reversed(annex["corrections"])
                    if c["boundary_key"] == name and c["phase"] == "completed"
                ),
                None,
            )
            if correction:
                return {**row, "phase": "completed", "plan": correction["plan"], "recovered": True}
        return row

    prior = [
        resolved_record(name, r)
        for name, r in book["records"].items()
        if r.get("fit") == fit_name and r.get("index", total) < start
    ]
    candidates = [
        r
        for r in current["candidates"].values()
        if r.get("trial_number") is not None and r["trial_number"] < start
    ]
    candidates.sort(
        key=lambda r: (
            not r.get("automatic_design", {}).get("qualified", False),
            r.get("summary", {}).get("closed_trades", 0) <= 0,
            -r["score"],
            r["config_id"],
        )
    )
    if not candidates:
        raise ValueError("Hands-off planning requires actual completed screening accounts")

    originals = {row["id"]: row for row in baseline}

    def native_locks(rows):
        if len(rows) != len(originals) or {r["id"] for r in rows} != set(originals):
            return False
        return all(
            row.get("execution_policy", {}).get("risk_limits") == risk_limits
            and all(
                row["config"].get(k) == originals[row["id"]]["config"].get(k)
                for k in ("initial_capital", "cost_bps", "slippage_bps")
            )
            and ("intraday" if row["config"].get("trade_horizon") == "intraday" else "positional")
            == style
            and "entry_condition" not in row
            for row in rows
        )

    def preferred_style(rows):
        # Native screens legitimately select different allocation, risk-sizing,
        # clocks and trailing distances. Prefer the original execution/trailing
        # style without demanding an exact policy that no screened row may have.
        return sum(
            row.get("execution_policy", {}).get("exit_mode")
            == originals[row["id"]].get("execution_policy", {}).get("exit_mode")
            for row in rows
        ) + sum(
            row["config"].get("trailing_enabled")
            == originals[row["id"]]["config"].get("trailing_enabled")
            for row in rows
        )

    def active_domains(rows):
        result = {}
        definitions = {r["id"]: r for r in rows}
        for name, domain in axes.items():
            sid, field = name.rsplit(".", 1)
            if (
                user_flow
                and ("hold_sessions" if field == "hold_minutes" else field)
                not in user_flow["adjustable"]
            ):
                continue
            if sid not in definitions or domain.get("type") == "categorical":
                continue
            row = definitions[sid]
            if (
                field in ("stop_pct", "target_pct")
                and row.get("execution_policy", {}).get("exit_mode") == "atr"
            ):
                continue
            if field == "trailing_pct" and not row["config"].get("trailing_enabled"):
                continue
            if field == "hold_sessions" and (
                style == "intraday" or row["config"].get("hold_minutes") is not None
            ):
                continue
            if field == "hold_minutes" and row["config"].get("hold_minutes") is None:
                continue
            if domain["min"] == domain["max"]:
                continue
            result.setdefault(sid, {})[field] = deepcopy(domain)
        return result

    anchors = [
        (candidate, active_domains(candidate["strategies"]))
        for candidate in candidates
        if native_locks(candidate["strategies"])
    ]
    if not anchors:
        raise ValueError(
            "Hands-off planning has no screened configuration preserving source, style, costs and risk"
        )
    anchors.sort(
        key=lambda pair: (
            not bool(pair[1]),
            pair[0].get("summary", {}).get("closed_trades", 0) <= 0,
            not pair[0].get("automatic_design", {}).get("qualified", False),
            -preferred_style(pair[0]["strategies"]),
            -sum(len(fields) for fields in pair[1].values()),
            -pair[0]["score"],
            pair[0]["config_id"],
        )
    )
    anchor, domains = anchors[0]
    settings = deepcopy(anchor["strategies"])
    from research.user_flow import locked_axes

    context = build_context(
        settings,
        domains,
        signal_id=signal_id,
        style=style,
        risk_limits=risk_limits,
        prior_domains=[r["plan"] for r in prior],
        locked_fields=locked_axes(settings, user_flow) if user_flow else None,
    )
    evidence = {
        "planning_context": context,
        "phase": "training_prefix_refinement",
        "fit": fit_name,
        "scope": "Only this fit's measured training accounts; no later or final outcomes",
        "full_source_facts": deepcopy(source_facts or {}),
        "programme": {
            "proposals": 408,
            "max_simulations": 512,
            "fit_proposals": total,
            "mandatory_screening": screening,
            "next_refinement_index": start,
        },
        "reference": deepcopy(reference),
        "observed_candidates": [
            {
                k: deepcopy(r[k])
                for k in (
                    "config_id",
                    "strategies",
                    "summary",
                    "score",
                    "automatic_design",
                    "research_diagnostics",
                )
                if k in r
            }
            for r in [*candidates[:3], anchor]
        ],
        "feedback": [
            {
                "plan": r["plan"],
                "completed_proposals": sum(
                    start0 <= t["number"] < start for t in current["trials"]
                ),
            }
            for r in prior
            for start0 in [r["index"]]
        ],
        "evidence_limit": "Prefix sample checks are development diagnostics; a proposed range is never a validated recommendation. Structural source limitations or no useful new test justify stop.",
    }
    request_id = fingerprint(evidence)
    record = book["records"].get(key)
    revision = bool(
        authority
        and (
            record is None
            or record.get("evidence_revision") == 2
            or annex
            and key in annex["rejections"]
        )
    )
    if revision:
        from research.hands_off_feedback import build_feedback

        # Publish the prefix before attaching its exact retrievable reference.
        reference_fn = getattr(callback, "feedback_reference", None)
        references = state.setdefault("planning_feedback_refs", {})
        if key not in references:
            persist()
            references[key] = reference_fn() if reference_fn else None
            persist()
        feedback_input = {
            **current,
            **({"feedback_evidence_ref": references[key]} if references[key] else {}),
        }
        evidence["feedback"] = build_feedback(feedback_input, prior, start)
        fields = (
            "config_id",
            "strategies",
            "summary",
            "score",
            "automatic_design",
            "research_diagnostics",
        )
        evidence["reference"] = {k: deepcopy(reference[k]) for k in fields if k in reference}
        evidence["observed_candidates"] = [
            {k: deepcopy(r[k]) for k in fields if k in r} for r in [candidates[0], anchor]
        ]
        request_id = fingerprint(evidence)
    if record is not None:
        if record["request_id"] != request_id and not (annex and key in annex["rejections"]):
            raise ValueError("Hands-off checkpoint planning evidence changed")
        if annex and key in annex["rejections"]:
            return _corrected(
                state, book, key, record, evidence, context, callback, persist, cancelled
            )
        if record.get("phase") != "completed":
            raise ValueError(
                "An interrupted AI planning call consumed its allowance; no blind provider retry"
            )
        plan = record["plan"]
        verified = compile_plan(
            {
                k: plan[k]
                for k in (
                    "version",
                    "action",
                    "method",
                    "hypothesis",
                    "reason",
                    "expected_evidence",
                    "stop_rule",
                    "domains",
                )
            },
            context,
        )
        if any(plan.get(k) != v for k, v in verified.items()):
            raise ValueError("Saved AI refinement plan failed compilation")
    else:
        if book["calls_spent"] >= MAX_CALLS:
            raise ValueError("Hands-off planning exhausted its reserved model allowance")
        book["calls_spent"] += 1
        record = {
            "fit": fit_name,
            "index": start,
            "request_id": request_id,
            "phase": "requesting",
            "anchor_params": deepcopy(current["trials"][anchor["trial_number"]]["params"]),
            **({"evidence_revision": 2} if revision else {}),
        }
        book["records"][key] = record
        book["active"] = key
        persist()
        cancelled()
        try:
            plan = callback(evidence)
        except Exception as error:
            from services.agent.research_planning import CompletedPlanRejection

            if not authority or not isinstance(error, CompletedPlanRejection):
                raise
            from research import planning_recovery as recovery

            if annex is None:
                reference_fn = getattr(callback, "feedback_reference", None)
                checkpoint = reference_fn() if reference_fn else None
                if not checkpoint:
                    raise ValueError(
                        "Completed rejection needs its published checkpoint before correction"
                    ) from error
                annex = recovery.authorize(
                    book,
                    checkpoint_id=checkpoint["checkpoint"],
                    request_id=record["request_id"],
                    authority_id=authority["id"],
                    job_id=authority.get("job_id"),
                )
            annex = recovery.attest_rejection(book, annex, error.receipt, boundary_key=key)
            state["planning_recovery"] = annex
            book["active"] = None
            persist()
            return _corrected(
                state, book, key, record, evidence, context, callback, persist, cancelled
            )
        verified = compile_plan(
            {
                k: plan[k]
                for k in (
                    "version",
                    "action",
                    "method",
                    "hypothesis",
                    "reason",
                    "expected_evidence",
                    "stop_rule",
                    "domains",
                )
            },
            context,
        )
        if any(plan.get(k) != v for k, v in verified.items()):
            raise ValueError("The model's compiled research plan changed")
        record.update(phase="completed", plan=deepcopy(plan))
        book["active"] = None
        persist()
    gate = getattr(callback, "approval_gate", None)
    if gate is not None:
        decision = gate(record)
        if decision == "reject":
            return {**record, "user_rejected": True}
        cancelled()
        if decision != "approve":
            raise ValueError("Research must pause for the pending guided approval")
    return record


def _corrected(state, book, key, record, evidence, context, callback, persist, cancelled):
    from research import planning_recovery as recovery
    from services.agent.research_planning import CompletedPlanRejection

    def verify_corrected(plan):
        verified = compile_plan(
            {
                k: plan[k]
                for k in (
                    "version",
                    "action",
                    "method",
                    "hypothesis",
                    "reason",
                    "expected_evidence",
                    "stop_rule",
                    "domains",
                )
            },
            context,
        )
        if any(plan.get(k) != v for k, v in verified.items()):
            raise ValueError("The corrected plan failed frozen-domain validation")

    while True:
        annex = state["planning_recovery"]
        previous = [r for r in annex["corrections"] if r["boundary_key"] == key]
        if previous and previous[-1]["phase"] == "requesting":
            raise ValueError(
                "Correction was interrupted; its charged request cannot be blindly retried"
            )
        rejections = [
            annex["rejections"][key]["receipt"],
            *[r["rejection"] for r in previous if r["phase"] == "rejected"],
        ]
        rejection = rejections[-1]
        correction_evidence = {**evidence, "rejected_proposals": [rejection]}
        request_id = fingerprint(correction_evidence)
        if previous and previous[-1]["phase"] == "completed":
            saved = previous[-1]
            if saved["request_id"] != request_id:
                raise ValueError("The saved correction evidence changed")
            plan = saved["plan"]
            break
        annex = recovery.reserve_correction(book, annex, boundary_key=key, request_id=request_id)
        state["planning_recovery"] = annex
        correction_id = annex["corrections"][-1]["id"]
        persist()
        cancelled()
        observer = getattr(callback, "report_controller_event", None)
        if observer:
            observer(
                correction_id,
                "The completed AI proposal failed local grid validation. Its call remains charged; requesting a bounded correction within the existing model allowance. No invalid settings will be tested.",
            )
        try:
            plan = callback(correction_evidence)
        except CompletedPlanRejection as error:
            state["planning_recovery"] = recovery.complete_correction(
                annex, correction_id, rejection=error.receipt
            )
            persist()
            continue
        # A reservation remains uncertain/charged until its exact compiled
        # settings pass admission. Never persist an unchecked completed plan.
        verify_corrected(plan)
        state["planning_recovery"] = recovery.complete_correction(annex, correction_id, plan=plan)
        persist()
        break
    verify_corrected(plan)
    result = {**record, "phase": "completed", "plan": plan, "recovered": True}
    gate = getattr(callback, "approval_gate", None)
    if gate:
        decision = gate(result)
        if decision == "reject":
            return {**result, "user_rejected": True}
        cancelled()
        if decision != "approve":
            raise ValueError("Research must pause for the corrected guided approval")
    return result


def trace(state, *, stopped=None):
    book = state.get("hands_off_planning", {})
    records = deepcopy(list(book.get("records", {}).values()))
    annex = state.get("planning_recovery")
    if annex:
        for row in records:
            correction = next(
                (
                    c
                    for c in reversed(annex["corrections"])
                    if c["boundary_key"] == f"{row['fit']}:{row['index']}"
                    and c["phase"] == "completed"
                ),
                None,
            )
            if correction:
                row.update(plan=deepcopy(correction["plan"]), phase="completed", recovered=True)
    for row in records:
        finish = min(
            (r["index"] for r in records if r["fit"] == row["fit"] and r["index"] > row["index"]),
            default=68,
        )
        row["tested_proposals"] = sum(
            row["index"] <= t["number"] < finish
            for t in state["fits"].get(row["fit"], {}).get("trials", [])
        )
    return {
        "version": POLICY,
        "calls_spent": book.get("calls_spent", 0) + len((annex or {}).get("corrections", [])),
        "normal_calls": book.get("calls_spent", 0),
        **({"recovery": deepcopy(annex)} if annex else {}),
        "records": records,
        "attempts": state["attempts"],
        "simulations": state["simulations"],
        "proposals": sum(len(f["trials"]) for f in state["fits"].values()),
        "allowance": {"proposals": 408, "max_simulations": 512},
        **({"stop_reason": stopped, "validation_completed": False} if stopped else {}),
    }


def attach_result(report, state):
    if state.get("hands_off_planning"):
        report["hands_off_planning"] = trace(state)
    return report
