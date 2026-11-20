"""Bounded Optuna proposal deduplication; never evaluates or changes validation."""

from copy import deepcopy
from decimal import Decimal, localcontext
from math import gcd, isfinite, prod

from research.connectors.optuna_portfolio import _values
from research.report_contract import fingerprint


class ProposalSpaceExhausted(ValueError):
    pass


def _semantic_numbers(value):
    if type(value) in (int, float):
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("Effective settings identity requires finite numeric controls")
        number = Decimal(str(value))
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits))
            return {"__research_number__": "0" if not number else str(number.normalize())}
    if isinstance(value, dict):
        return {key: _semantic_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_semantic_numbers(item) for item in value]
    return value


def effective_identity(settings):
    rows = deepcopy(settings)
    for row in rows:
        cfg, policy = row["config"], row.get("execution_policy", {})
        if policy.get("exit_mode") == "atr":
            cfg.pop("stop_pct", None)
            cfg.pop("target_pct", None)
        if not cfg.get("trailing_enabled"):
            cfg.pop("trailing_pct", None)
    return fingerprint(_semantic_numbers(rows))


def alternatives(domains, *, fixed=None, seed="", limit=256):
    """Visit a reproducible lattice without constructing its Cartesian product."""
    names = list(domains)
    values = [
        d["choices"] if d.get("type") == "categorical" else _values(d) for d in domains.values()
    ]
    size = prod(len(v) for v in values)
    start = int(fingerprint({"seed": seed, "domains": domains}), 16) % size
    stride = int(fingerprint({"step": seed}), 16) | 1
    while gcd(stride, size) != 1:
        stride += 2
    for index in range(min(limit, size)):
        number = (start + index * stride) % size
        params = dict(fixed or {})
        for name, choices in reversed(list(zip(names, values, strict=True))):
            number, offset = divmod(number, len(choices))
            params[name] = choices[offset]
        yield params


def draw(study, *, sample, build, seen, domains, fixed=None, seed="", broaden=None):
    """Pruned duplicate draws consume neither admitted slots nor financial accounts.

    A tiny AI domain is fully tested before explicitly returning to the already
    reviewed native domains. The caller records that attribution separately.
    """
    import optuna

    rejected = []
    ai_exhausted = None
    for source, sampler, space, anchor in [
        ("ai_domain" if broaden else "approved_domain", sample, domains, fixed),
        *([("approved_domain", *broaden)] if broaden else []),
    ]:
        attribution = (
            {
                "generation_reason": "ai_grid_exhausted"
                if ai_exhausted
                else "ai_generation_bound_reached",
                "ai_domain_exhausted": ai_exhausted,
            }
            if broaden and source == "approved_domain"
            else {}
        )
        for _ in range(8):
            trial = study.ask()
            params = sampler(trial)
            settings = build(params)
            identity = effective_identity(settings)
            if identity not in seen:
                return (
                    trial,
                    params,
                    settings,
                    {"proposal_source": source, "sampler_rejections": rejected, **attribution},
                )
            rejected.append(identity)
            study.tell(trial, state=optuna.trial.TrialState.PRUNED)
        for params in alternatives(space, fixed=anchor, seed=seed):
            settings = build(params)
            if effective_identity(settings) in seen:
                continue
            study.enqueue_trial(params)
            trial = study.ask()
            actual = sampler(trial)
            if actual != params:
                raise ValueError("The queued unique configuration changed")
            return (
                trial,
                actual,
                settings,
                {
                    "proposal_source": source,
                    "sampler_rejections": rejected,
                    "queued_unique": True,
                    **attribution,
                },
            )
        if source == "ai_domain":
            ai_exhausted = (
                prod(
                    len(d["choices"]) if d.get("type") == "categorical" else len(_values(d))
                    for d in space.values()
                )
                <= 256
            )
    raise ProposalSpaceExhausted(
        "No unseen permitted configuration was found within the bounded proposal generator; no duplicate financial test was run"
    )
