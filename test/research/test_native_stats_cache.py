"""Application-owned outer dispatch preserves pinned compiled native statistics."""

import gc
import inspect
import weakref
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import vectorbt as vbt
from numba.core.registry import CPUDispatcher
from vectorbt.generic import nb as generic
from vectorbt.portfolio import nb as portfolio_nb
from vectorbt.records import nb as records
from vectorbt.records.mapped_array import MappedArray

from research import analytics
from research import native_stats_cache as cached

pytestmark = pytest.mark.timeout(240)


@pytest.mark.parametrize("name,callback,args", [
    ("_mean", generic.mean_reduce_nb, ()),
    ("_min", generic.min_reduce_nb, ()),
    ("_max", generic.max_reduce_nb, ()),
    ("_sum", generic.sum_reduce_nb, ()),
    ("_std", generic.std_reduce_nb, (1,)),
    ("_nth", generic.nth_reduce_nb, (0,)),
    ("_winning", portfolio_nb.trade_winning_streak_nb, ()),
    ("_losing", portfolio_nb.trade_losing_streak_nb, ()),
])
def test_only_native_outer_dispatch_is_python_financial_callbacks_stay_compiled(
    name, callback, args, monkeypatch,
):
    calls, sentinel = [], object()
    def dispatch(*arguments):
        calls.append(arguments)
        return sentinel
    # Replace only this application's provider reference. The dependency's
    # dispatchers, implementations and global runtime settings remain intact.
    monkeypatch.setattr(cached, "records_nb", SimpleNamespace(
        reduce_mapped_nb=SimpleNamespace(py_func=dispatch),
        apply_on_records_nb=SimpleNamespace(py_func=dispatch),
    ))
    wrapper = getattr(cached, name)
    assert inspect.isfunction(wrapper)
    assert isinstance(callback, CPUDispatcher)
    values, mapping = object(), object()
    if name in ("_winning", "_losing"):
        assert wrapper(values, mapping) is sentinel
        assert calls == [(values, mapping, callback)]
    else:
        assert wrapper(values, mapping, 0.0, *args) is sentinel
        assert calls == [(values, mapping, 0.0, callback, *args)]


def same(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(actual, pd.Series):
        pd.testing.assert_series_equal(actual, expected, check_exact=True)
    elif isinstance(actual, pd.DataFrame):
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    else:
        np.testing.assert_equal(actual, expected)


@pytest.mark.parametrize("group_by", [False, True])
def test_scalar_reducers_keep_native_nan_empty_group_and_wrapper_semantics(group_by):
    wrapper = vbt.ArrayWrapper.from_obj(pd.DataFrame(np.zeros((4, 3)), columns=["a", "b", "empty"]))
    original = MappedArray(
        wrapper,
        np.array([1.0, np.nan, -2.0, 0.0]),
        np.array([0, 0, 1, 1]),
        id_arr=np.arange(4),
        idx_arr=np.arange(4),
    )
    adapted = original.replace(cls_=cached.CachedMappedArray)
    for name, args in (
        ("mean", ()),
        ("min", ()),
        ("max", ()),
        ("sum", ()),
        ("std", (1,)),
        ("nth", (0,)),
    ):
        same(
            getattr(adapted, name)(*args, group_by=group_by),
            getattr(original, name)(*args, group_by=group_by),
        )
    # A callback outside the fixed whitelist retains the native execution path.
    same(
        adapted.reduce(generic.median_reduce_nb, group_by=group_by),
        original.reduce(generic.median_reduce_nb, group_by=group_by),
    )
    same(adapted.describe(group_by=group_by), original.describe(group_by=group_by))
    same(
        adapted.reduce(generic.nth_index_reduce_nb, 0, returns_idx=True, group_by=group_by),
        original.reduce(generic.nth_index_reduce_nb, 0, returns_idx=True, group_by=group_by),
    )


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_complete_native_analysis_and_report_remain_exact(minute, empty, monkeypatch):
    from test_vectorbt_portfolio import DAYS, adapter, candle, snapshot, strategy

    prices = snapshot(minute=minute)
    for key in prices["bars"]["AAA"]:
        if key.startswith(DAYS[1]):
            prices["bars"]["AAA"][key] = candle(100, 111, 99, 108)
    native = []
    report_fn = adapter._report

    def report(pf, *args, **kwargs):
        native.append(pf)
        return report_fn(pf, *args, **kwargs)

    monkeypatch.setattr(adapter, "_report", report)
    before_reduce, before_apply = records.reduce_mapped_nb, records.apply_on_records_nb
    result = adapter.evaluate(
        [
            strategy("a", target_pct=10, stop_pct=20, order_size_pct=0.01 if empty else 100),
            strategy("b", target_pct=30, stop_pct=20, order_size_pct=0.01 if empty else 100),
        ],
        prices,
        1000,
    )
    assert len(result["analysis"]["metrics"]) == 121
    original = deepcopy(result)
    with monkeypatch.context() as baseline:
        # Only our application seam changes. No native object or library global
        # is patched to obtain this complete original-provider comparison.
        baseline.setattr(cached, "portfolio_for_stats", lambda value: value)
        baseline.setattr(cached, "CachedTrades", vbt.Trades)
        baseline.setattr(cached, "CachedDrawdowns", vbt.Drawdowns)
        expected = analytics.build_analysis(result, portfolio=native[0])
    assert result["analysis"] == expected, {
        section: {
            key: (result["analysis"][section].get(key), expected[section].get(key))
            for key in result["analysis"][section].keys() | expected[section].keys()
            if result["analysis"][section].get(key) != expected[section].get(key)
        }
        for section in ("metrics", "unavailable")
    }
    assert result == original
    assert records.reduce_mapped_nb is before_reduce
    assert records.apply_on_records_nb is before_apply
    assert type(native[0]) is vbt.Portfolio
    assert type(cached.portfolio_for_stats(native[0])) is cached.CachedPortfolio


@pytest.mark.parametrize(
    "sizes", [[0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, -1.0]]
)
def test_empty_open_closed_native_record_views_and_lifetime(sizes):
    original = vbt.Portfolio.from_orders(
        pd.Series([100.0, 110.0, 90.0, 105.0], index=pd.date_range("2026-01-01", periods=4)),
        size=sizes,
        init_cash=1000,
        freq="1D",
    )
    owner_ref = weakref.ref(original)
    refs = []
    for _ in range(3):
        adapted = cached.portfolio_for_stats(original)
        refs.append(weakref.ref(adapted))
        for getter in (
            "get_entry_trades",
            "get_exit_trades",
            "get_positions",
            "get_trades",
            "get_drawdowns",
        ):
            left, right = getattr(adapted, getter)(), getattr(original, getter)()
            pd.testing.assert_series_equal(
                left.stats(silence_warnings=True),
                right.stats(silence_warnings=True),
                check_exact=True,
            )
            assert type(right).__module__.startswith("vectorbt.")
        del left, right, adapted
    del original
    gc.collect()
    assert owner_ref() is None
    assert all(ref() is None for ref in refs)


def test_custom_native_subclasses_and_runtime_pin_keep_original_behavior(monkeypatch):
    class CustomPortfolio(vbt.Portfolio):
        pass

    original = vbt.Portfolio.from_orders(
        pd.Series([100.0, 101.0]), size=pd.Series([1.0, -1.0]), init_cash=1000
    )
    custom = original.replace(cls_=CustomPortfolio)
    assert cached.portfolio_for_stats(custom) is custom
    assert cached.portfolio_for_stats(None) is None
    monkeypatch.setattr(vbt, "__version__", "unsupported")
    with pytest.raises(ValueError, match="pinned VectorBT"):
        cached.portfolio_for_stats(original)
