"""Historical research tools for the built-in Agent.

The confirmed ``research_run_automatic`` or ``research_open_supervisor`` call
grants a *bounded historical research plan*, not general write access. Its continuations are the deliberate
exception to per-write confirmation: every one must present the durable
owner-and-conversation grant to ``research_supervisor``, which admits only its
fixed remedies, experiment budget and exact-result saving. No generic tool,
trading capability, broker setting or validation threshold is changed here.

The app supplies its existing ResearchStore in trusted context extras; neither
the model nor persisted Agno session state chooses a path, owner or credential.
All service work crosses the normal eventlet hub boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from services.agent.prompts import wrap_tool_result
from services.agent.tools.base import OpenAlgoToolkit

if TYPE_CHECKING:
    from services.agent.tools import ToolContext


_READS = frozenset({"capabilities", "list_runs", "get_run"})
_SUPERVISOR = frozenset(
    {
        "open_supervisor",
        "start_automatic",
        "status",
        "propose",
        "advance",
        "save_result",
        "reopen_result",
    }
)

_INSTRUCTIONS = """Historical research only. When the user supplies the completed
run ID and asks to run/fix/improve it, CALL research_run_automatic DIRECTLY as
the first operation. This command itself inspects the saved native evidence
before admitting anything; a separate research_get_run call is unnecessary.
If the ID is missing, find the exact requested saved run, then call this command.
Its tool confirmation card requests the bounded authorization; do not stop for
a separate prose explanation or a second conversational permission question.
That one confirmed operation starts the actual bounded decision loop;
saying 'I will open/run/test' without calling the tool does not execute anything.
Explain its returned actual status. Do not promise work that was not submitted.
The native worker chooses executable registered remedies, applies native funding
and size rules, and uses a capped configured-model JSON decision for ambiguous
choices. It validates changed accounts, saves the first accepted result and
reopens it. Continuation survives the initiating chat turn. No new user message
is needed between eligible experiments within the original confirmed allowance.
The advanced manual surface may queue proposals BEFORE the first advance;
later-period plans may permit only one. Continue only within the returned grant.
Never invent settings, prices, successful validation or independent evidence.
When a child is queued/running, report its actual status and stop this turn
instead of polling in a tight loop. Automatic plans continue in the worker;
manual awaiting_plan means another manual action is required. Never confuse
an automatic plan with unrestricted background LLM reasoning.
A rejected or insufficient-evidence result is a valid stopping outcome, not
permission to weaken limits, reuse final validation for selection or declare new
experiments after viewing held-back outcomes. The grant never permits changing
validation dates, thresholds, risk limits or the original search/account budget.
Only save the exact result the supervisor accepts; reopen it to verify identity.
These tools cannot place orders, change broker settings or deploy a strategy.
Use the compact evidence returned here; never upload raw research artifacts or
credentials through web search or unrelated external tools.
"""


def _dispatch(store, api_key, user_id, conversation_id, operation, arguments):
    """Resolve trusted account identity and dispatch on the service's own thread."""
    from services import research_mcp

    if store is None:
        raise ValueError("Research is not available in this Agent surface")
    owner = research_mcp.owner_from_api_key(api_key)
    if not isinstance(user_id, str) or owner != user_id:
        raise PermissionError("Research requires the signed-in OpenAlgo account")
    if operation in _READS:
        return getattr(research_mcp, operation)(store, owner, **arguments)
    if operation not in _SUPERVISOR:
        raise ValueError("Unknown historical research action")
    if (
        isinstance(conversation_id, bool)
        or not isinstance(conversation_id, (str, int))
        or not str(conversation_id).strip()
        or len(str(conversation_id)) > 128
    ):
        raise PermissionError("Research automation requires its signed-in conversation")
    from services import research_supervisor

    return getattr(research_supervisor, operation)(
        store, owner, conversation_id=conversation_id, **arguments
    )


def _bounded(value: Any, depth: int = 0) -> Any:
    """Keep whole evidence fields rather than returning a cut-off JSON fragment."""
    if depth > 10:
        return {"omitted": "Nested detail; open the saved research page"}
    if isinstance(value, Mapping):
        return {str(key): _bounded(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        rows = [_bounded(item, depth + 1) for item in value[:8]]
        if len(value) > 8:
            rows.append({"omitted_items": len(value) - 8})
        return rows
    if isinstance(value, str) and len(value) > 700:
        return value[:700] + "… [open saved page for remaining text]"
    return value


class ResearchToolkit(OpenAlgoToolkit):
    """Inspect saved research and supervise bounded, validated historical remedies."""

    inject_api_key = False

    def __init__(self, context: ToolContext) -> None:
        # Extras is server-owned; never read a store from Agno session state.
        extras = getattr(context, "extras", None)
        self._store = extras.get("research_store") if isinstance(extras, Mapping) else None
        super().__init__(
            context,
            name="research",
            tools=[
                self.research_capabilities,
                self.research_list_runs,
                self.research_get_run,
                self.research_run_automatic,
                self.research_open_supervisor,
                self.research_supervisor_status,
                self.research_propose_experiment,
                self.research_advance_supervisor,
                self.research_save_supervisor,
                self.research_reopen_supervisor,
            ],
            requires_confirmation_tools=["research_run_automatic", "research_open_supervisor"],
            instructions=_INSTRUCTIONS,
            add_instructions=True,
        )

    def _call(self, tool: str, operation: str, arguments: dict[str, Any], *, write=False) -> str:
        def invoke():
            return self.service_call(
                _dispatch,
                self._store,
                self.api_key,
                self.user_id,
                self.conversation_id,
                operation,
                arguments,
            )

        if write:
            with self.audited(tool, arguments, risk_verdict="bounded_historical_research") as audit:
                result = invoke()
                audit.record(ok=True, response=result)
        else:
            result = invoke()
        return self._result(tool, result)

    def _result(self, tool: str, result: Any) -> str:
        result = _bounded(result)
        # Verdict and stopping evidence must survive a large optional report.
        if len(json.dumps(result, default=str, ensure_ascii=False)) > 10500:
            preferred = (
                "id",
                "supervisor_id",
                "job_id",
                "status",
                "research_evidence",
                "automation",
                "automatic_phase",
                "decision",
                "decision_evidence",
                "validation",
                "stop_reason",
                "reason",
                "grant",
                "budget",
                "remaining_accounts",
                "allowed_remedies",
                "diagnosis",
                "next_action",
                "objective",
                "goal",
                "saved",
                "reopened",
                "result_id",
                "path",
                "summary",
                "automatic_findings",
                "message",
                "state",
            )
            compact: dict[str, Any] = {}
            if isinstance(result, Mapping):
                for key in preferred:
                    if key in result:
                        trial = {**compact, key: result[key]}
                        if len(json.dumps(trial, default=str, ensure_ascii=False)) <= 10000:
                            compact[key] = result[key]
                compact["omitted_fields"] = sorted(set(result) - set(compact))
            else:
                compact["message"] = "Open the saved research page for this large result"
            result = compact
        return wrap_tool_result(tool, self.to_json(result))

    def research_capabilities(self) -> str:
        """Read supported native historical research methods and constraints.

        Returns:
            JSON with research capabilities. This does not run a backtest.
        """
        return self._call("research_capabilities", "capabilities", {})

    def research_list_runs(self, limit: int = 10, cursor: str | None = None) -> str:
        """List this account's saved research runs, without recalculating them.

        Args:
            limit: Number of runs, from 1 to 100; prefer 10 for concise evidence.
            cursor: Exact next_cursor from the previous response, or null for page one.

        Returns:
            JSON with saved run identifiers, statuses, page links and next_cursor.
        """
        return self._call("research_list_runs", "list_runs", {"limit": limit, "cursor": cursor})

    def research_get_run(self, job_id: str) -> str:
        """Read a saved run's result, decision, funding and validation evidence.

        Args:
            job_id: Exact 32-character hexadecimal saved run id from research_list_runs.

        Returns:
            JSON with native evidence and limitations. No calculations are performed.
        """
        return self._call("research_get_run", "get_run", {"job_id": job_id, "curve_points": 10})

    def research_open_supervisor(
        self, job_id: str, request_id: str, max_experiments: int = 3
    ) -> str:
        """Confirm and start a bounded automatic plan for a completed saved run.

        Approval permits only the server's registered remedies, fixed validation
        and experiment allowance in this conversation. It permits no trading or
        unrestricted portfolio runs. The command inspects the run internally.
        Invoke it now; the tool confirmation card explains the bounded plan.

        Args:
            job_id: Exact 32-character hexadecimal completed saved run id.
            request_id: Stable unique submission id, 8-128 characters; reuse on retry.
            max_experiments: Maximum historical remedy experiments, from 1 to 3.

        Returns:
            JSON with supervisor_id, actual status, diagnosis, budget and goal.
            The durable worker handles decisions, execution, validation and save/reopen.
        """
        return self._start_automatic(
            "research_open_supervisor", job_id, request_id, max_experiments
        )

    def research_run_automatic(self, job_id: str, request_id: str, max_experiments: int = 3) -> str:
        """Confirm and START the complete bounded historical decision loop now.

        Use this single operation when the user asks to improve a saved run.
        With a supplied run ID, call it directly: it reads and verifies that
        run itself before admitting work. Approval is collected by the tool's
        confirmation card, not a separate prose-only response.
        It submits work; a promise in chat does not. The durable worker chooses
        executable remedies, computes native accounts with unchanged validation,
        and saves/reopens the first accepted result or records an explicit stop.
        It cannot trade, edit risk limits, invent prices or reset the search budget.

        Args:
            job_id: Exact 32-character hexadecimal completed saved run id.
            request_id: Stable unique submission id, 8-128 characters; reuse on retry.
            max_experiments: Maximum native remedy experiments, from 1 to 3.

        Returns:
            JSON with supervisor_id, durable actual state, allowance and next action.
            Queued work continues without another chat message.
        """
        return self._start_automatic("research_run_automatic", job_id, request_id, max_experiments)

    def _start_automatic(self, tool, job_id, request_id, max_experiments):
        extras = getattr(self.context, "extras", None)
        model_id = extras.get("research_model_id") if isinstance(extras, Mapping) else None
        return self._call(
            tool,
            "start_automatic",
            {
                "job_id": job_id,
                "request_id": request_id,
                "max_experiments": max_experiments,
                # These are server-owned settings, never model tool arguments.
                "model_id": model_id,
                "max_model_calls": 3,
            },
            write=True,
        )

    def research_supervisor_status(self, supervisor_id: str) -> str:
        """Read a granted supervisor's exact state, evidence and stopping decision.

        Args:
            supervisor_id: Exact supervisor_id returned by research_open_supervisor.

        Returns:
            JSON with bounded evidence, remaining allowance and the permitted next action.
        """
        return self._call("research_supervisor_status", "status", {"supervisor_id": supervisor_id})

    def research_propose_experiment(self, supervisor_id: str, remedy: str, rationale: str) -> str:
        """Propose one permitted remedy under this conversation's confirmed plan.

        Queue the chosen proposals before the first advance so the native worker
        can continue the batch automatically. The server checks the diagnosis,
        duplicate attempts, remaining budget and frozen validation rules. A
        proposal cannot supply arbitrary settings or change validation dates.

        Args:
            supervisor_id: Exact supervisor_id of this conversation's confirmed plan.
            remedy: Exact identifier from status diagnosis.permitted_remedies.
            rationale: Concise evidence-based reason, at most 1000 characters.

        Returns:
            JSON with the frozen proposal or a specific refusal; no live orders.
        """
        return self._call(
            "research_propose_experiment",
            "propose",
            {"supervisor_id": supervisor_id, "remedy": remedy, "rationale": rationale},
            write=True,
        )

    def research_advance_supervisor(self, supervisor_id: str) -> str:
        """Start the predeclared historical batch or inspect its completed checks.

        Queue the chosen permitted proposals first. The native completion hook
        runs remaining queued remedies and saves the first historically accepted
        result. It cannot request background LLM replanning. If work is pending,
        stop this turn and report status; do not repeatedly poll or launch extras.
        awaiting_plan requires another Agent turn and is not background work.

        Args:
            supervisor_id: Exact supervisor_id of this conversation's confirmed plan.

        Returns:
            JSON with native outcome, validation, remaining budget and next action.
        """
        return self._call(
            "research_advance_supervisor", "advance", {"supervisor_id": supervisor_id}, write=True
        )

    def research_save_supervisor(self, supervisor_id: str, name: str) -> str:
        """Save only the exact native result accepted by this confirmed research plan.

        Args:
            supervisor_id: Exact supervisor_id of this conversation's confirmed plan.
            name: Human-readable saved result name, at most 120 characters.

        Returns:
            JSON identifying the saved exact settings, capital and evidence.
        """
        return self._call(
            "research_save_supervisor",
            "save_result",
            {"supervisor_id": supervisor_id, "name": name},
            write=True,
        )

    def research_reopen_supervisor(self, supervisor_id: str) -> str:
        """Reopen the plan's saved result and verify its native identity.

        Args:
            supervisor_id: Exact supervisor_id of this conversation's confirmed plan.

        Returns:
            JSON with saved settings, evidence and exact-result verification.
        """
        return self._call(
            "research_reopen_supervisor", "reopen_result", {"supervisor_id": supervisor_id}
        )
