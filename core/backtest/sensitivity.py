"""参数敏感性分析：网格搜索不同卖出策略参数组合，输出参数-收益矩阵。"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from core.backtest.engine import BacktestEngine
from core.backtest.metrics import calculate_metrics
from core.backtest.models import BacktestConfig, BacktestResult


@dataclass(frozen=True, slots=True)
class SensitivityCell:
    """敏感性分析矩阵中的单个单元格"""

    row_value: float          # 行参数值（如止盈阈值）
    col_value: float          # 列参数值（如卖出比例）
    total_return: float       # 总收益率
    annual_return: float      # 年化收益率
    sharpe_ratio: float       # 夏普比率
    max_drawdown: float       # 最大回撤
    win_rate: float           # 胜率
    total_trades: int         # 总交易次数


@dataclass(slots=True)
class SensitivityResult:
    """敏感性分析结果"""

    row_param_name: str                    # 行参数名称
    col_param_name: str                    # 列参数名称
    row_values: list[float]                # 行参数值列表
    col_values: list[float]                # 列参数值列表
    cells: list[list[SensitivityCell]] = field(default_factory=list)
    best_cell: SensitivityCell | None = None


def run_sensitivity_analysis(
    engine: BacktestEngine,
    base_config: BacktestConfig,
    row_param_name: str,
    row_values: list[float],
    col_param_name: str,
    col_values: list[float],
    progress_callback: Callable[[dict], None] | None = None,
    cancelled_fn: Callable[[], bool] | None = None,
) -> SensitivityResult:
    """执行参数敏感性分析

    对行参数和列参数的所有组合进行网格搜索，每组参数运行一次回测。

    Args:
        engine: 回测引擎
        base_config: 基础回测配置（将在此基础上修改参数）
        row_param_name: 行参数名称（sell_strategy_params 中的键名）
        row_values: 行参数值列表
        col_param_name: 列参数名称（sell_strategy_params 中的键名）
        col_values: 列参数值列表
        progress_callback: 进度回调
        cancelled_fn: 取消检查函数

    Returns:
        SensitivityResult: 分析结果矩阵
    """
    total_runs = len(row_values) * len(col_values)
    current_run = 0
    best_cell: SensitivityCell | None = None
    best_sharpe = float("-inf")

    cells: list[list[SensitivityCell]] = []

    for row_val in row_values:
        row_cells: list[SensitivityCell] = []

        for col_val in col_values:
            if cancelled_fn is not None and cancelled_fn():
                return SensitivityResult(
                    row_param_name=row_param_name,
                    col_param_name=col_param_name,
                    row_values=row_values,
                    col_values=col_values,
                    cells=cells,
                    best_cell=best_cell,
                )

            current_run += 1

            # 构建当前参数组合的配置
            config = copy.copy(base_config)
            params = dict(config.sell_strategy_params) if config.sell_strategy_params else {}
            params[row_param_name] = row_val
            params[col_param_name] = col_val
            config.sell_strategy_params = params

            # 运行回测
            try:
                result = engine.run(config)
                result.metrics = calculate_metrics(result)
                metrics = result.metrics
            except Exception:
                metrics = None

            if metrics is not None:
                cell = SensitivityCell(
                    row_value=row_val,
                    col_value=col_val,
                    total_return=metrics.total_return,
                    annual_return=metrics.annual_return,
                    sharpe_ratio=metrics.sharpe_ratio,
                    max_drawdown=metrics.max_drawdown,
                    win_rate=metrics.win_rate,
                    total_trades=metrics.total_trades,
                )
            else:
                cell = SensitivityCell(
                    row_value=row_val,
                    col_value=col_val,
                    total_return=0.0,
                    annual_return=0.0,
                    sharpe_ratio=0.0,
                    max_drawdown=0.0,
                    win_rate=0.0,
                    total_trades=0,
                )

            row_cells.append(cell)

            if cell.sharpe_ratio > best_sharpe:
                best_sharpe = cell.sharpe_ratio
                best_cell = cell

            if progress_callback is not None:
                progress_callback({
                    "current": current_run,
                    "total": total_runs,
                    "row_value": row_val,
                    "col_value": col_val,
                    "total_return": cell.total_return,
                    "sharpe_ratio": cell.sharpe_ratio,
                })

        cells.append(row_cells)

    return SensitivityResult(
        row_param_name=row_param_name,
        col_param_name=col_param_name,
        row_values=row_values,
        col_values=col_values,
        cells=cells,
        best_cell=best_cell,
    )
