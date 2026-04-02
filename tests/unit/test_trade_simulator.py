"""TradeSimulator 单元测试"""

from __future__ import annotations

import pytest

from core.models.trade import TradeAction
from core.trade.simulator import TradeSimulator


class TestBuy:
    """买入操作测试"""

    def test_single_buy_creates_holding(self):
        sim = TradeSimulator()
        record = sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")

        assert record.symbol == "000001"
        assert record.action == TradeAction.BUY
        assert record.price == 10.0
        assert record.quantity == 100
        assert record.total_amount == 1000.0

        holding = sim.get_holding("000001")
        assert holding is not None
        assert holding.quantity == 100
        assert holding.average_cost == 10.0
        assert holding.total_cost == 1000.0
        assert holding.current_price == 10.0
        assert holding.pnl_amount == 0.0

    def test_multiple_buys_weighted_average_cost(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.buy("000001", "平安银行", 12.0, 200, "2026-03-28")

        holding = sim.get_holding("000001")
        assert holding.quantity == 300
        expected_cost = (10.0 * 100 + 12.0 * 200)
        expected_avg = expected_cost / 300
        assert holding.average_cost == pytest.approx(expected_avg)
        assert holding.total_cost == pytest.approx(expected_cost)

    def test_buy_records_trade(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")

        assert len(sim.trade_records) == 1
        assert sim.trade_records[0].trade_date == "2026-03-27"


class TestSell:
    """卖出操作测试"""

    def test_sell_partial_reduces_holding(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 300, "2026-03-27")
        record = sim.sell("000001", "平安银行", 12.0, 100, "2026-03-28")

        assert record.action == TradeAction.SELL
        assert record.total_amount == 1200.0

        holding = sim.get_holding("000001")
        assert holding.quantity == 200
        assert holding.average_cost == 10.0
        assert holding.total_cost == pytest.approx(2000.0)

    def test_sell_all_removes_holding(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.sell("000001", "平安银行", 12.0, 100, "2026-03-28")

        assert sim.get_holding("000001") is None
        assert "000001" not in sim.holdings

    def test_sell_exceeds_holding_raises(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")

        with pytest.raises(ValueError, match="持仓不足"):
            sim.sell("000001", "平安银行", 12.0, 200, "2026-03-28")

    def test_sell_unheld_stock_raises(self):
        sim = TradeSimulator()

        with pytest.raises(ValueError, match="未持有"):
            sim.sell("000001", "平安银行", 12.0, 100, "2026-03-28")

    def test_sell_records_trade(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.sell("000001", "平安银行", 12.0, 100, "2026-03-28")

        assert len(sim.trade_records) == 2
        assert sim.trade_records[1].action == TradeAction.SELL


class TestUpdatePrices:
    """价格更新测试"""

    def test_update_prices_recalculates_pnl(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.update_all_prices({"000001": 12.0})

        holding = sim.get_holding("000001")
        assert holding.current_price == 12.0
        assert holding.current_value == 1200.0
        assert holding.pnl_amount == pytest.approx(200.0)
        assert holding.pnl_percent == pytest.approx(20.0)

    def test_update_prices_ignores_unknown_symbols(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.update_all_prices({"999999": 50.0})

        holding = sim.get_holding("000001")
        assert holding.current_price == 10.0


class TestSettle:
    """结算测试"""

    def test_settle_summarizes_pnl(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.buy("000002", "万科A", 8.0, 200, "2026-03-27")
        sim.update_all_prices({"000001": 12.0, "000002": 7.0})

        result = sim.settle()

        expected_cost = 10.0 * 100 + 8.0 * 200  # 2600
        expected_value = 12.0 * 100 + 7.0 * 200  # 2600
        assert result.total_cost == pytest.approx(expected_cost)
        assert result.total_value == pytest.approx(expected_value)
        assert result.total_pnl_amount == pytest.approx(expected_value - expected_cost)
        assert result.trade_count == 2
        assert len(result.holdings_at_settle) == 2

    def test_settle_empty_holdings(self):
        sim = TradeSimulator()
        result = sim.settle()

        assert result.total_cost == 0.0
        assert result.total_value == 0.0
        assert result.total_pnl_percent == 0.0
        assert result.trade_count == 0


class TestReset:
    """重置测试"""

    def test_reset_clears_all(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.buy("000002", "万科A", 8.0, 200, "2026-03-27")

        sim.reset()

        assert len(sim.holdings) == 0
        assert len(sim.trade_records) == 0
        assert sim.get_all_holdings() == []


class TestGetAllHoldings:
    """获取持仓列表测试"""

    def test_returns_all_holdings(self):
        sim = TradeSimulator()
        sim.buy("000001", "平安银行", 10.0, 100, "2026-03-27")
        sim.buy("000002", "万科A", 8.0, 200, "2026-03-27")

        holdings = sim.get_all_holdings()
        assert len(holdings) == 2
        symbols = {h.symbol for h in holdings}
        assert symbols == {"000001", "000002"}
