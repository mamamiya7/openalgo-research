"""The Python simulation uses exact native empty hook bodies without JIT work."""

import copy

import pytest

from research.connectors import vectorbt_portfolio as adapter
from research.report_contract import fingerprint
from test.research.test_vectorbt_portfolio import DAYS, candle, signal, snapshot, strategy


@pytest.mark.timeout(240)
@pytest.mark.parametrize("minute", [False, True])
def test_native_empty_hook_bodies_preserve_every_account_field(monkeypatch, minute):
    import vectorbt as vbt
    from vectorbt.portfolio import nb

    prices = snapshot(minute=minute)
    if not minute:
        prices["bars"]["AAA"][DAYS[1]] = candle(100, 111, 95, 108)
    rows = [
        strategy("a", 60, target_pct=10, stop_pct=5),
        strategy(
            "b",
            40,
            signals=[signal("BBB", timestamp="09:16" if minute else None)],
            target_pct=20,
            stop_pct=10,
        ),
    ]
    original = vbt.Portfolio.from_order_func
    hooks = {
        "pre_sim_func_nb": nb.no_pre_func_nb,
        "post_sim_func_nb": nb.no_post_func_nb,
        "pre_row_func_nb": nb.no_pre_func_nb,
        "post_row_func_nb": nb.no_post_func_nb,
    }
    observed = []

    def exact_python(*args, **kwargs):
        for key, native in hooks.items():
            assert kwargs[key] is getattr(native, "py_func", native)
        observed.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(vbt.Portfolio, "from_order_func", exact_python)
    with adapter.discard_trial_analysis():
        actual = adapter.evaluate(copy.deepcopy(rows), copy.deepcopy(prices), 1000)
    assert observed

    def compiled_defaults(*args, **kwargs):
        for key in hooks:
            kwargs.pop(key)
        return original(*args, **kwargs)

    monkeypatch.setattr(vbt.Portfolio, "from_order_func", compiled_defaults)
    with adapter.discard_trial_analysis():
        expected = adapter.evaluate(copy.deepcopy(rows), copy.deepcopy(prices), 1000)
    assert fingerprint(actual) == fingerprint(expected)
    assert actual == expected
