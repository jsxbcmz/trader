from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.expression.evaluator import EvaluationContext, evaluate_at_index
from core.expression.parser import TdxLexer, TdxParser, TdxTranspiler, TdxTranspileError
from core.models.screening import ScreeningError, ScreeningRequest, ScreeningResult
from core.screening.error_policy import DEFAULT_ERROR_POLICY, normalize_error_policy
from core.screening.result_models import SingleRunResult, build_debug_payload, stock_name_of
from core.stock_pool.manager import StockPoolManager


@dataclass(slots=True)
class ScreeningEngine:
    repository: StockRepository
    stock_pool_manager: StockPoolManager
    error_policy: str = DEFAULT_ERROR_POLICY

    @classmethod
    def from_root(cls, root: Path) -> "ScreeningEngine":
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        return cls(repository=repository, stock_pool_manager=stock_pool_manager)

    def run(self, request: ScreeningRequest) -> ScreeningResult:
        policy = normalize_error_policy(self.error_policy)

        # 解析通达信条件代码
        tdx_source = request.tdx_source
        if not tdx_source or not tdx_source.strip():
            raise ValueError("通达信条件代码不能为空")

        try:
            expression = self._parse_tdx_source(tdx_source)
        except Exception as exc:
            raise ValueError(f"通达信条件解析失败: {exc}") from exc

        pool = (
            self.stock_pool_manager.get_pool_by_symbols(request.symbols, request.stock_pool_name)
            if request.symbols
            else self.stock_pool_manager.get_default_pool(request.stock_pool_name)
        )

        stock_map = {stock.symbol: stock for stock in pool.stocks}
        matches = []
        errors = []

        for symbol in pool.symbols:
            stock = stock_map.get(symbol)
            try:
                run_result = self._run_single(symbol, stock, expression, request)
                matches.append(run_result.to_match())
            except Exception as exc:
                if policy == "raise":
                    raise
                errors.append(ScreeningError(symbol=symbol, stage="engine", message=str(exc)))

        matched_count = sum(1 for item in matches if item.matched)
        return ScreeningResult(
            request=request,
            matches=tuple(matches),
            errors=tuple(errors),
            total=len(pool.symbols),
            matched_count=matched_count,
        )

    def _parse_tdx_source(self, source: str):
        """解析通达信条件代码为内部表达式"""
        lexer = TdxLexer(source)
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()
        transpiler = TdxTranspiler(program)
        return transpiler.transpile()

    def _run_single(self, symbol, stock, expression, request: ScreeningRequest) -> SingleRunResult:
        """执行单只股票的选股判断"""
        df = self.repository.get_daily_frame(symbol)
        time_result = locate_time_index(df, request.target_date, request.time_mode)

        if not time_result.matched or time_result.index is None:
            return SingleRunResult(
                symbol=symbol,
                name=stock_name_of(stock, symbol),
                requested_date=time_result.requested_date,
                actual_date=time_result.actual_date or "",
                matched=False,
                reason=time_result.reason,
                debug={},
            )

        context = EvaluationContext(df=df, target_index=time_result.index)
        value = evaluate_at_index(expression, context)
        matched = bool(value)
        reason = "命中" if matched else "条件不满足"
        if time_result.fallback_used and time_result.reason:
            reason = f"{reason}（{time_result.reason}）"

        return SingleRunResult(
            symbol=symbol,
            name=stock_name_of(stock, symbol),
            requested_date=time_result.requested_date,
            actual_date=time_result.actual_date or "",
            matched=matched,
            reason=reason,
            value=value,
            debug=build_debug_payload(value, request.include_debug),
        )
