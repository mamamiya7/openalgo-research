"""Fixed dispatch of unchanged pinned VectorBT financial statistics.

The native higher-order record kernels compile their outer column-dispatch loops
again for callback specializations. Execute those exact native outer loops as
Python while retaining the unchanged compiled native financial callbacks. This
avoids first-worker specialization cost without disabling or patching native JIT.
Unsupported operations use their original VectorBT paths.
"""

import numpy as np
import vectorbt as vbt
from vectorbt.generic import nb as generic_nb
from vectorbt.generic.drawdowns import Drawdowns
from vectorbt.portfolio import nb as portfolio_nb
from vectorbt.portfolio.base import Portfolio
from vectorbt.portfolio.enums import trade_dt
from vectorbt.portfolio.trades import EntryTrades, ExitTrades, Positions, Trades
from vectorbt.records import nb as records_nb
from vectorbt.records.mapped_array import MappedArray
from vectorbt.utils import checks
from vectorbt.utils.config import merge_dicts


def _mean(values, col_map, fill):
    return records_nb.reduce_mapped_nb.py_func(values, col_map, fill, generic_nb.mean_reduce_nb)


def _min(values, col_map, fill):
    return records_nb.reduce_mapped_nb.py_func(values, col_map, fill, generic_nb.min_reduce_nb)


def _max(values, col_map, fill):
    return records_nb.reduce_mapped_nb.py_func(values, col_map, fill, generic_nb.max_reduce_nb)


def _sum(values, col_map, fill):
    return records_nb.reduce_mapped_nb.py_func(values, col_map, fill, generic_nb.sum_reduce_nb)


def _std(values, col_map, fill, ddof):
    return records_nb.reduce_mapped_nb.py_func(values, col_map, fill, generic_nb.std_reduce_nb, ddof)


def _nth(values, col_map, fill, index):
    return records_nb.reduce_mapped_nb.py_func(values, col_map, fill, generic_nb.nth_reduce_nb, index)


def _winning(records, col_map):
    return records_nb.apply_on_records_nb.py_func(records, col_map, portfolio_nb.trade_winning_streak_nb)


def _losing(records, col_map):
    return records_nb.apply_on_records_nb.py_func(records, col_map, portfolio_nb.trade_losing_streak_nb)


_REDUCERS = (
    (generic_nb.mean_reduce_nb, 0, _mean),
    (generic_nb.min_reduce_nb, 0, _min),
    (generic_nb.max_reduce_nb, 0, _max),
    (generic_nb.sum_reduce_nb, 0, _sum),
    (generic_nb.std_reduce_nb, 1, _std),
    (generic_nb.nth_reduce_nb, 1, _nth),
)


def _fixed_mapping(col_map):
    return (
        type(col_map) is tuple
        and len(col_map) == 2
        and all(
            type(part) is np.ndarray
            and part.ndim == 1
            and part.dtype == np.dtype("int64")
            and part.flags.c_contiguous
            for part in col_map
        )
    )


class CachedMappedArray(MappedArray):
    def reduce(
        self,
        reduce_func_nb,
        *args,
        idx_arr=None,
        returns_array=False,
        returns_idx=False,
        to_index=True,
        fill_value=np.nan,
        group_by=None,
        wrap_kwargs=None,
    ):
        kernel = next(
            (
                function
                for callback, arity, function in _REDUCERS
                if reduce_func_nb is callback and len(args) == arity
            ),
            None,
        )
        if kernel is None or returns_array or returns_idx:
            return super().reduce(
                reduce_func_nb,
                *args,
                idx_arr=idx_arr,
                returns_array=returns_array,
                returns_idx=returns_idx,
                to_index=to_index,
                fill_value=fill_value,
                group_by=group_by,
                wrap_kwargs=wrap_kwargs,
            )
        checks.assert_numba_func(reduce_func_nb)
        col_map = self.col_mapper.get_col_map(group_by=group_by)
        # Dispatch only the finite native scalar signature family used here.
        # Arbitrary external dtypes/layouts retain the original provider path.
        if not (
            type(self.values) is np.ndarray
            and self.values.ndim == 1
            and self.values.dtype in (np.dtype("float64"), np.dtype("int64"))
            and _fixed_mapping(col_map)
            and type(fill_value) in (float, int)
            and all(type(argument) is int for argument in args)
        ):
            return super().reduce(
                reduce_func_nb,
                *args,
                idx_arr=idx_arr,
                returns_array=returns_array,
                returns_idx=returns_idx,
                to_index=to_index,
                fill_value=fill_value,
                group_by=group_by,
                wrap_kwargs=wrap_kwargs,
            )
        out = kernel(self.values, col_map, fill_value, *args)
        # Identical scalar-result wrapping to the pinned native reduce method.
        options = merge_dicts(
            {"name_or_index": "reduce", "to_index": False, "fillna": None, "dtype": None},
            wrap_kwargs,
        )
        return self.wrapper.wrap_reduced(out, group_by=group_by, **options)


class _RecordMethods:
    def map_array(self, *args, **kwargs):
        return super().map_array(*args, **kwargs).replace(cls_=CachedMappedArray)

    def apply(
        self,
        apply_func_nb,
        *args,
        group_by=None,
        apply_per_group=False,
        dtype=None,
        **kwargs,
    ):
        kernel = (
            _winning
            if apply_func_nb is portfolio_nb.trade_winning_streak_nb
            else _losing
            if apply_func_nb is portfolio_nb.trade_losing_streak_nb
            else None
        )
        if kernel is None or args:
            return super().apply(
                apply_func_nb,
                *args,
                group_by=group_by,
                apply_per_group=apply_per_group,
                dtype=dtype,
                **kwargs,
            )
        checks.assert_numba_func(apply_func_nb)
        col_map = self.col_mapper.get_col_map(group_by=group_by if apply_per_group else False)
        if not (
            type(self.values) is np.ndarray
            and self.values.ndim == 1
            and self.values.dtype == trade_dt
            and self.values.flags.c_contiguous
            and _fixed_mapping(col_map)
        ):
            return super().apply(
                apply_func_nb,
                *args,
                group_by=group_by,
                apply_per_group=apply_per_group,
                dtype=dtype,
                **kwargs,
            )
        mapped = np.asarray(kernel(self.values, col_map), dtype=dtype)
        return self.map_array(mapped, group_by=group_by, **kwargs)


class CachedTrades(_RecordMethods, Trades):
    pass


class CachedEntryTrades(_RecordMethods, EntryTrades):
    pass


class CachedExitTrades(_RecordMethods, ExitTrades):
    pass


class CachedPositions(_RecordMethods, Positions):
    pass


class CachedDrawdowns(_RecordMethods, Drawdowns):
    pass


def _records(value):
    if isinstance(value, _RecordMethods):
        return value
    for original, replacement in (
        (EntryTrades, CachedEntryTrades),
        (ExitTrades, CachedExitTrades),
        (Positions, CachedPositions),
        (Trades, CachedTrades),
        (Drawdowns, CachedDrawdowns),
    ):
        if type(value) is original:
            return value.replace(cls_=replacement)
    return value


class CachedPortfolio(Portfolio):
    def get_entry_trades(self, group_by=None, **kwargs):
        return _records(super().get_entry_trades(group_by=group_by, **kwargs))

    def get_exit_trades(self, group_by=None, **kwargs):
        return _records(super().get_exit_trades(group_by=group_by, **kwargs))

    def get_positions(self, group_by=None, **kwargs):
        return _records(super().get_positions(group_by=group_by, **kwargs))

    def get_trades(self, group_by=None, **kwargs):
        return _records(super().get_trades(group_by=group_by, **kwargs))

    def get_drawdowns(self, group_by=None, wrap_kwargs=None, wrapper_kwargs=None, **kwargs):
        return _records(
            super().get_drawdowns(
                group_by=group_by,
                wrap_kwargs=wrap_kwargs,
                wrapper_kwargs=wrapper_kwargs,
                **kwargs,
            )
        )


def portfolio_for_stats(value):
    """Create an application-owned native view, without changing its source."""
    if vbt.__version__ != "0.28.5":
        raise ValueError("Analysis requires the pinned VectorBT 0.28.5 runtime")
    if type(value) is Portfolio:
        return value.replace(cls_=CachedPortfolio)
    return value
