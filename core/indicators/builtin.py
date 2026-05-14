from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators.algorithms import ema as _ema
from core.indicators.algorithms import moving_average as _moving_average
from core.indicators.algorithms import rolling_max as _rolling_max
from core.indicators.algorithms import rolling_min as _rolling_min
from core.indicators.algorithms import tdx_sma as _tdx_sma


def _to_float_array(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def _to_bool_array(values) -> np.ndarray:
    """将值转换为布尔数组"""
    arr = np.asarray(values)
    if arr.dtype == bool:
        return arr
    # 非零且非NaN视为True
    return np.isfinite(arr) & (arr != 0)


def ma(values, period: int) -> np.ndarray:
    return _moving_average(_to_float_array(values), int(period))


def ema(values, period: int) -> np.ndarray:
    return _ema(_to_float_array(values), int(period))


def sma(values, n: int, m: int = 1) -> np.ndarray:
    return _tdx_sma(_to_float_array(values), int(n), int(m))


def hhv(values, period: int) -> np.ndarray:
    return _rolling_max(_to_float_array(values), int(period))


def llv(values, period: int) -> np.ndarray:
    return _rolling_min(_to_float_array(values), int(period))


def ref(values, period: int = 1) -> np.ndarray:
    source = _to_float_array(values)
    offset = int(period)
    out = np.full(source.shape, np.nan, dtype=float)
    if offset < 0:
        raise ValueError("REF 的 period 不能为负数")
    if offset == 0:
        out[:] = source
        return out
    if offset < len(source):
        out[offset:] = source[:-offset]
    return out


def max_series(left, right) -> np.ndarray:
    return np.maximum(_to_float_array(left), _to_float_array(right))


def min_series(left, right) -> np.ndarray:
    return np.minimum(_to_float_array(left), _to_float_array(right))


def abs_series(values) -> np.ndarray:
    return np.abs(_to_float_array(values))


# ==================== 新增通达信兼容函数 ====================


def if_series(condition, true_value, false_value) -> np.ndarray:
    """条件判断函数

    通达信语法: IF(条件, 真值, 假值)

    Args:
        condition: 条件数组
        true_value: 条件为真时的值
        false_value: 条件为假时的值

    Returns:
        根据条件选择的结果数组
    """
    cond = _to_bool_array(condition)
    true_arr = _to_float_array(true_value)
    false_arr = _to_float_array(false_value)

    # 广播处理
    if cond.shape != true_arr.shape:
        cond = np.broadcast_to(cond, true_arr.shape)
    if cond.shape != false_arr.shape:
        false_arr = np.broadcast_to(false_arr, cond.shape)

    return np.where(cond, true_arr, false_arr)


def cross(line1, line2) -> np.ndarray:
    """上穿判断函数

    通达信语法: CROSS(线1, 线2)
    含义: 线1上穿线2（前一天线1<=线2，当天线1>线2）

    Args:
        line1: 第一条线
        line2: 第二条线

    Returns:
        布尔数组，True表示发生上穿
    """
    l1 = _to_float_array(line1)
    l2 = _to_float_array(line2)

    result = np.zeros(len(l1), dtype=bool)

    if len(l1) < 2:
        return result

    # 前一天 l1 <= l2，当天 l1 > l2
    prev_le = l1[:-1] <= l2[:-1]
    curr_gt = l1[1:] > l2[1:]
    result[1:] = prev_le & curr_gt

    return result


def count(condition, period: int) -> np.ndarray:
    """统计满足条件的次数

    通达信语法: COUNT(条件, 周期)
    含义: 统计最近N周期内满足条件的次数

    Args:
        condition: 条件数组
        period: 统计周期

    Returns:
        满足条件的次数数组
    """
    arr = _to_bool_array(condition).astype(float)
    period = int(period)

    if period <= 0 or period > len(arr):
        period = len(arr)

    # 使用pandas的rolling进行滚动求和
    result = pd.Series(arr).rolling(window=period, min_periods=1).sum().to_numpy()
    return result


def sum_series(values, period: int) -> np.ndarray:
    """周期求和

    通达信语法: SUM(X, N)
    含义: 统计N周期内X的总和

    Args:
        values: 数值数组
        period: 统计周期，如果为0则对所有数据求和

    Returns:
        求和结果数组
    """
    arr = _to_float_array(values)
    period = int(period)

    if period == 0:
        # 累计求和
        return np.cumsum(np.where(np.isfinite(arr), arr, 0))

    if period <= 0 or period > len(arr):
        period = len(arr)

    result = pd.Series(arr).rolling(window=period, min_periods=1).sum().to_numpy()
    return result


def between(value, low, high) -> np.ndarray:
    """区间判断（闭区间）

    通达信语法: BETWEEN(X, A, B)
    含义: A <= X <= B

    Args:
        value: 待判断的值
        low: 下限
        high: 上限

    Returns:
        布尔数组
    """
    v = _to_float_array(value)
    l = _to_float_array(low)
    h = _to_float_array(high)

    return (v >= l) & (v <= h)


def range_series(value, low, high) -> np.ndarray:
    """区间判断（开区间）

    通达信语法: RANGE(X, A, B)
    含义: A < X < B

    Args:
        value: 待判断的值
        low: 下限
        high: 上限

    Returns:
        布尔数组
    """
    v = _to_float_array(value)
    l = _to_float_array(low)
    h = _to_float_array(high)

    return (v > l) & (v < h)


def every(condition, period: int) -> np.ndarray:
    """判断是否全部满足

    通达信语法: EVERY(条件, N)
    含义: 最近N周期内全部满足条件

    Args:
        condition: 条件数组
        period: 周期数

    Returns:
        布尔数组
    """
    arr = _to_bool_array(condition)
    period = int(period)

    if period <= 0 or period > len(arr):
        period = len(arr)

    # 使用rolling统计满足条件的次数，等于周期数则全部满足
    count_arr = pd.Series(arr.astype(int)).rolling(window=period, min_periods=period).sum().to_numpy()
    return count_arr == period


def exist(condition, period: int) -> np.ndarray:
    """判断是否存在满足

    通达信语法: EXIST(条件, N)
    含义: 最近N周期内存在满足条件

    Args:
        condition: 条件数组
        period: 周期数

    Returns:
        布尔数组
    """
    arr = _to_bool_array(condition)
    period = int(period)

    if period <= 0 or period > len(arr):
        period = len(arr)

    # 使用rolling统计满足条件的次数，大于0则存在
    count_arr = pd.Series(arr.astype(int)).rolling(window=period, min_periods=1).sum().to_numpy()
    return count_arr > 0


def barslast(condition) -> np.ndarray:
    """距离上次满足条件的周期数

    通达信语法: BARSLAST(条件)
    含义: 距离上次满足条件到当前的周期数

    Args:
        condition: 条件数组

    Returns:
        周期数数组
    """
    arr = _to_bool_array(condition)
    result = np.full(len(arr), np.nan, dtype=float)

    last_true_idx = -1
    for i in range(len(arr)):
        if arr[i]:
            last_true_idx = i
            result[i] = 0
        elif last_true_idx >= 0:
            result[i] = i - last_true_idx

    return result


def std_series(values, period: int) -> np.ndarray:
    """计算标准差

    通达信语法: STD(X, N)
    含义: 计算N周期内X的标准差

    Args:
        values: 数值数组
        period: 周期数

    Returns:
        标准差数组
    """
    arr = _to_float_array(values)
    period = int(period)

    if period <= 1:
        return np.zeros_like(arr)

    result = pd.Series(arr).rolling(window=period, min_periods=2).std().to_numpy()
    return result
