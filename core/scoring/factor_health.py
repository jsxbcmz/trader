"""因子健康度报告（P2-1 IC 计算 + P2-2 单调性 + P2-3 月报）。

数据流：
    scoring_daily/{date}.json (全部命中票 + 子项分)
        ↓ 与日线 close 配对
    每只票的 T+1/T+2/T+3 收益（实时算，不依赖 outcomes.csv，覆盖全部命中票）
        ↓ 按日算 Spearman IC、分箱收益
    跨日聚合：IC 均值/标准差/IR/t统计；分箱单调性
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.data.io import load_daily_csv
from core.data.repository import StockRepository
from core.scoring.storage import load_scoring_daily


WINDOWS = ("t1", "t2", "t3")
N_BINS = 5


# ── 工具：从日线算 T+N 收益 ────────────────────────────────


def _compute_t_returns(
    repository: StockRepository,
    symbol: str,
    score_date: str,
) -> dict[str, Optional[float]]:
    """返回 {t1: ret, t2: ret, t3: ret} — **当日涨跌幅**（不再是 score_date 累计）。

    t1 = T+1 当日相对 T 收盘的涨跌；
    t2 = T+2 当日相对 T+1 收盘的涨跌；
    t3 = T+3 当日相对 T+2 收盘的涨跌。
    """
    df = repository.get_daily_frame(symbol)
    out: dict[str, Optional[float]] = {"t1": None, "t2": None, "t3": None}
    if df.empty:
        return out
    df = df.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(df["date"])
    score_ts = pd.Timestamp(score_date)
    idx = df.index[dates == score_ts]
    if len(idx) == 0:
        return out
    i = int(idx[0])
    for n in (1, 2, 3):
        j = i + n
        if j >= len(df):
            break
        c_prev = float(df.loc[j - 1, "close"])  # T+(n-1) 收盘
        c_n = float(df.loc[j, "close"])         # T+n 收盘
        if c_prev > 0:
            out[f"t{n}"] = (c_n - c_prev) / c_prev
    return out


# ── 单日 IC：每个子项 与 T+N 收益的 Spearman ─────────────────


def compute_daily_ic(
    repository: StockRepository,
    date: str,
    target: str = "t1",
) -> dict[str, float]:
    """单日 IC：返回 {子项名称: ic_value}。

    `target` ∈ {'t1', 't2', 't3'}。
    """
    if target not in WINDOWS:
        raise ValueError(f"target 必须是 {WINDOWS} 之一")

    records = load_scoring_daily(repository.root, date)
    if len(records) < 5:
        return {}

    # 收集每只票的子项分 + T+N 收益
    rows: list[dict] = []
    for rec in records:
        rets = _compute_t_returns(repository, rec.symbol, date)
        ret = rets[target]
        if ret is None:
            continue
        row = dict(rec.items)
        row["__ret__"] = ret
        rows.append(row)

    if len(rows) < 5:
        return {}

    df = pd.DataFrame(rows).fillna(0.0)
    ret_rank = df["__ret__"].rank()

    ics: dict[str, float] = {}
    for col in df.columns:
        if col == "__ret__":
            continue
        # 避免常量列（rank 后全部相同 → 相关无定义）
        if df[col].nunique() < 2:
            continue
        ic = df[col].rank().corr(ret_rank)
        if pd.notna(ic):
            ics[col] = float(ic)
    return ics


# ── 单日分箱（5 档）平均收益 ─────────────────────────────


def compute_daily_bins(
    repository: StockRepository,
    date: str,
    target: str = "t1",
) -> dict[str, dict[int, float]]:
    """单日分箱：返回 {子项名称: {bin_index: avg_return}}。

    bin_index: 0 ~ N_BINS-1（按子项分数等频分箱）。
    """
    records = load_scoring_daily(repository.root, date)
    rows: list[dict] = []
    for rec in records:
        rets = _compute_t_returns(repository, rec.symbol, date)
        ret = rets[target]
        if ret is None:
            continue
        row = dict(rec.items)
        row["__ret__"] = ret
        rows.append(row)

    if len(rows) < N_BINS * 2:
        return {}

    df = pd.DataFrame(rows).fillna(0.0)
    result: dict[str, dict[int, float]] = {}
    for col in df.columns:
        if col == "__ret__":
            continue
        if df[col].nunique() < N_BINS:
            continue
        try:
            df["__bin__"] = pd.qcut(df[col], q=N_BINS, labels=False, duplicates="drop")
        except ValueError:
            continue
        bins = df.groupby("__bin__")["__ret__"].mean().to_dict()
        result[col] = {int(k): float(v) for k, v in bins.items()}
    return result


# ── 跨日聚合 IC ─────────────────────────────────────────


@dataclass
class FactorIc:
    factor: str
    ic_mean: float
    ic_std: float
    ic_ir: float          # ic_mean / ic_std
    t_stat: float         # ic_mean * sqrt(n) / ic_std
    n_days: int
    is_monotonic: Optional[bool] = None  # P2-2 补


def aggregate_ic(daily_ics: list[dict[str, float]]) -> list[FactorIc]:
    """跨天聚合：每个子项的 ic_mean / ic_std / ic_ir / t_stat / n_days。"""
    factor_series: dict[str, list[float]] = {}
    for day_ics in daily_ics:
        for factor, ic in day_ics.items():
            factor_series.setdefault(factor, []).append(ic)

    out: list[FactorIc] = []
    for factor, series in factor_series.items():
        arr = np.array(series, dtype=float)
        n = len(arr)
        ic_mean = float(np.mean(arr))
        ic_std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        ic_ir = ic_mean / ic_std if ic_std > 1e-9 else 0.0
        t_stat = ic_mean * math.sqrt(n) / ic_std if ic_std > 1e-9 else 0.0
        out.append(FactorIc(
            factor=factor, ic_mean=ic_mean, ic_std=ic_std,
            ic_ir=ic_ir, t_stat=t_stat, n_days=n,
        ))
    out.sort(key=lambda x: abs(x.ic_mean), reverse=True)
    return out


# ── 跨日聚合分箱单调性（P2-2）────────────────────────────


def aggregate_monotonicity(daily_bins: list[dict[str, dict[int, float]]]) -> dict[str, dict]:
    """每个子项跨天平均的分箱收益曲线 + 单调性判定。

    Returns: {factor: {bins: {0: avg_ret, ..., N-1: avg_ret}, is_monotonic: bool, spread: float}}
    """
    # 累积每个 factor 的分箱
    factor_bins: dict[str, dict[int, list[float]]] = {}
    for day_bins in daily_bins:
        for factor, bins in day_bins.items():
            slot = factor_bins.setdefault(factor, {})
            for bin_idx, ret in bins.items():
                slot.setdefault(bin_idx, []).append(ret)

    out: dict[str, dict] = {}
    for factor, slot in factor_bins.items():
        avg_bins = {bi: float(np.mean(rets)) for bi, rets in slot.items() if rets}
        if len(avg_bins) < 3:
            continue
        sorted_bins = sorted(avg_bins.items())
        rets = [r for _, r in sorted_bins]
        # 单调递增（高分箱收益更高）
        is_monotonic = all(rets[i] <= rets[i + 1] for i in range(len(rets) - 1))
        spread = rets[-1] - rets[0]
        out[factor] = {
            "bins": avg_bins,
            "is_monotonic": is_monotonic,
            "spread": float(spread),
        }
    return out


# ── 顶层入口 ────────────────────────────────────────────


@dataclass
class FactorHealth:
    repository: StockRepository
    _calendar: list[pd.Timestamp] = field(default_factory=list, init=False, repr=False)

    @classmethod
    def from_root(cls, root: Path) -> "FactorHealth":
        return cls(repository=StockRepository(root=root))

    def _list_dates(self, start: str, end: str) -> list[str]:
        """用 scoring_daily 目录里实际存在的日期作为范围（避免计算节假日）。"""
        daily_dir = self.repository.root / "output" / "scoring_daily"
        if not daily_dir.exists():
            return []
        all_dates = sorted(p.stem for p in daily_dir.glob("*.json"))
        return [d for d in all_dates if start <= d <= end]

    def compute_ic(
        self,
        start_date: str,
        end_date: str,
        target: str = "t1",
    ) -> pd.DataFrame:
        """跨日 IC 聚合表。

        Returns DataFrame indexed by factor:
            ic_mean, ic_std, ic_ir, t_stat, n_days
        """
        dates = self._list_dates(start_date, end_date)
        daily_ics = [compute_daily_ic(self.repository, d, target) for d in dates]
        ic_list = aggregate_ic(daily_ics)
        return pd.DataFrame([
            {"factor": f.factor, "ic_mean": f.ic_mean, "ic_std": f.ic_std,
             "ic_ir": f.ic_ir, "t_stat": f.t_stat, "n_days": f.n_days}
            for f in ic_list
        ])

    def compute_monotonicity(
        self,
        start_date: str,
        end_date: str,
        target: str = "t1",
    ) -> dict[str, dict]:
        """跨日分箱单调性表。"""
        dates = self._list_dates(start_date, end_date)
        daily_bins = [compute_daily_bins(self.repository, d, target) for d in dates]
        return aggregate_monotonicity(daily_bins)

    def compute_topk_summary(
        self,
        start_date: str,
        end_date: str,
        k: int = 20,
    ) -> dict[str, dict]:
        """Top K 每天/三窗口的平均收益 + 胜率。

        Returns: {target: {avg_return, win_rate, n_samples}}
        """
        dates = self._list_dates(start_date, end_date)
        out: dict[str, dict] = {}
        for target in WINDOWS:
            rets: list[float] = []
            wins = 0
            for d in dates:
                records = load_scoring_daily(self.repository.root, d)
                top = sorted(records, key=lambda r: r.total_score, reverse=True)[:k]
                for rec in top:
                    r = _compute_t_returns(self.repository, rec.symbol, d).get(target)
                    if r is None:
                        continue
                    rets.append(r)
                    if r > 0:
                        wins += 1
            out[target] = {
                "avg_return": float(np.mean(rets)) if rets else 0.0,
                "win_rate": (wins / len(rets)) if rets else 0.0,
                "n_samples": len(rets),
            }
        return out

    def generate_monthly_report(
        self,
        year_month: str,
        k: int = 20,
    ) -> tuple[dict, Path]:
        """P2-3：生成月度因子健康度报告并落盘。

        Args:
            year_month: "YYYY-MM"
            k: TopK 的 K

        Returns: (报告 dict, 落盘路径)
        """
        start = f"{year_month}-01"
        end = f"{year_month}-31"  # 字符串比较安全
        dates = self._list_dates(start, end)

        report: dict = {
            "year_month": year_month,
            "date_range": [dates[0] if dates else "", dates[-1] if dates else ""],
            "n_days": len(dates),
            "k": k,
            "topk_summary": self.compute_topk_summary(start, end, k=k),
            "factor_ic": {},
            "monotonicity": {},
            "alerts": [],
        }

        for target in WINDOWS:
            ic_df = self.compute_ic(start, end, target=target)
            mono = self.compute_monotonicity(start, end, target=target)
            report["factor_ic"][target] = ic_df.to_dict(orient="records")
            report["monotonicity"][target] = mono

        # 异常因子告警（基于 t1 IC）
        for row in report["factor_ic"]["t1"]:
            if row["n_days"] < 3:
                continue
            ic_mean = row["ic_mean"]
            ic_ir = row["ic_ir"]
            if abs(ic_mean) < 0.02:
                report["alerts"].append({
                    "factor": row["factor"],
                    "type": "无效因子",
                    "detail": f"|IC_mean|={abs(ic_mean):.4f} < 0.02",
                })
            elif abs(ic_ir) < 0.3 and abs(ic_mean) > 0.02:
                report["alerts"].append({
                    "factor": row["factor"],
                    "type": "IC 不稳",
                    "detail": f"IR={ic_ir:.3f} < 0.3",
                })

        out_dir = self.repository.root / "output" / "scoring_factor_health"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{year_month}.json"
        import json
        with path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report, path


def load_monthly_report(root: Path, year_month: str) -> dict:
    """加载已生成的月度报告（供 UI 用）。"""
    path = root / "output" / "scoring_factor_health" / f"{year_month}.json"
    if not path.exists():
        return {}
    import json
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
