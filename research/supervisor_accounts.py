"""Finite native operating-account evaluator over one frozen research study."""

from collections import OrderedDict
from copy import deepcopy

from research.automatic_hypotheses import _gate, policy_for_version
from research.automatic_protocol import slice_period
from research.automatic_walk_forward import _context, prepare_method_gate
from research.available_history_protocol import entry_bins, opportunity_calendar
from research.evaluation_basis import build_evaluation_basis
from research.execution_features import _PreparedExecutionFeatures
from research.portfolio_execution_features import _PreparedPortfolioFeatures
from research.report_contract import settings_identity


class FrozenAccounts:
    def __init__(self, evidence, evaluate, cancelled):
        self.evidence, self.evaluate, self.cancelled = evidence, evaluate, cancelled
        self.windows, self.gates = OrderedDict(), OrderedDict()
        self.context = _context(evidence["automatic_recipe"]["version"])
        self.gate = self.execution = self.portfolio = None

    def __enter__(self):
        from contextlib import ExitStack

        self.stack = ExitStack()
        try:
            self.gate = self.stack.enter_context(
                prepare_method_gate(self.evidence["automatic_stock_market_context"])
            )
            self.execution = self.stack.enter_context(
                _PreparedExecutionFeatures(self.evidence["execution_features"])
            )
            self.portfolio = self.stack.enter_context(
                _PreparedPortfolioFeatures(self.evidence["portfolio_execution_features"])
            )
            return self
        except BaseException:
            self.stack.close()
            raise

    def window(self, period):
        if period not in self.windows:
            if len(self.windows) >= 2:
                self.windows.popitem(last=False)
            self.windows[period] = slice_period(self.evidence, period)
        return self.windows[period]

    def inputs(self, settings, hypothesis, period, stress=False):
        from research.connectors.vectorbt_portfolio import validate_config

        current = dict(self.window(period))
        chosen = {r["id"]: r for r in settings}
        current["strategies"] = [
            {**r, **deepcopy(chosen[r["id"]]), "search": {}} for r in current["strategies"]
        ]
        capital = float(settings[0]["config"]["initial_capital"])
        current["portfolio"] = {**current["portfolio"], "capital": capital}
        for row in current["strategies"]:
            row["config"] = validate_config(row["config"])
            row["config"]["initial_capital"] = capital
        if stress:
            for row in current["strategies"]:
                cost = row["config"]["cost_bps"]
                row["config"]["cost_bps"] = max(2 * cost, cost + 5)
        current = self.portfolio.attach(self.execution.attach(current))
        version = self.evidence["automatic_recipe"]["version"]
        current["strategies"], receipt = _gate(
            current,
            self.evidence["automatic_stock_market_context"],
            hypothesis,
            self.gates,
            period,
            apply_fn=self.gate.apply,
            markers_fn=self.context.markers,
            calculation_version=policy_for_version(version)["calculation_version"],
            trailing_activation=True,
        )
        return current, receipt

    def design(self, report, period):
        from research.automatic_design import assess

        dates = opportunity_calendar(self.window(period))
        return assess(
            report,
            entry_bins(dates),
            self.evidence["automatic_recipe"]["automatic_design"],
            opportunity_dates=dates,
        )

    def calculate(self, settings, hypothesis, period, stress=False):
        self.cancelled()
        current, receipt = self.inputs(settings, hypothesis, period, stress)
        report = self.evaluate(
            current["strategies"],
            current["snapshot"],
            current["portfolio"]["capital"],
            progress=lambda *_: self.cancelled(),
        )
        report["strategies"] = self.context.markers(report["strategies"], hypothesis)
        expected = self.context.markers(current["strategies"], hypothesis)
        if settings_identity(report["strategies"]) != settings_identity(expected):
            raise ValueError("The native supervisor returned different settings")
        report["automatic_stock_condition"] = {
            "hypothesis": deepcopy(hypothesis),
            "gate_id": receipt["id"],
        }
        report["evaluation_basis"] = build_evaluation_basis(
            current, period="evaluation" if period == "final" else "selection"
        )
        if period != "final":
            report["research_opportunity_basis"] = build_evaluation_basis(
                self.window(period), period="selection"
            )
        report["portfolio_constraint_evidence"] = self.portfolio.assess(
            report, current["portfolio_execution_features"]
        )
        if report["portfolio_constraint_evidence"]["status"] != "supported":
            raise ValueError("The native supervisor financial audit is unsupported")
        report["automatic_design"] = self.design(report, period)
        # The report must save the account actually calculated, including its
        # capital, rather than copying the parent account's portfolio envelope.
        p = deepcopy(current["portfolio"])
        for key in ("automatic_research", "optimization", "validation", "entry_conditions"):
            p.pop(key, None)
        owners = {r["id"]: r for r in report["strategies"]}
        p["strategies"] = [
            {**r, **deepcopy(owners[r["id"]]), "search": {}} for r in p["strategies"]
        ]
        marker = report["strategies"][0].get("entry_condition")
        if marker:
            p["entry_conditions"] = deepcopy(marker)
        report["portfolio"] = p
        return report

    def audit(self, report, request):
        current, receipt = self.inputs(
            request["settings"], request["hypothesis"], request["period"], request["stress"]
        )
        expected = build_evaluation_basis(
            current, period="evaluation" if request["period"] == "final" else "selection"
        )
        if (
            settings_identity(report.get("strategies"))
            != settings_identity(self.context.markers(current["strategies"], request["hypothesis"]))
            or report["summary"]["initial_capital"] != current["portfolio"]["capital"]
            or report.get("automatic_stock_condition")
            != {"hypothesis": request["hypothesis"], "gate_id": receipt["id"]}
            or report["evaluation_basis"] != expected
            or report["automatic_design"] != self.design(report, request["period"])
        ):
            raise ValueError("Saved supervisor account evidence changed")
        return self.portfolio.assess(report, current["portfolio_execution_features"])

    def __exit__(self, *error):
        self.stack.close()
        self.windows.clear()
        self.gates.clear()
        self.evidence = self.evaluate = self.cancelled = None
        self.gate = self.execution = self.portfolio = None
