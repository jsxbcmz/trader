# 模拟交易系统

**文件：** `core/trade/simulator.py` (~161 行)
**数据模型：** `core/models/trade.py`

## TradeSimulator(dataclass)

### 内部状态
- `holdings: dict[str, HoldingPosition]` — 当前持仓，键为 symbol
- `trade_records: list[TradeRecord]` — 全部交易历史

### 方法

| 方法 | 说明 |
|------|------|
| `buy(symbol, name, price, quantity, trade_date)` | 买入，新建或加权平均更新持仓成本 |
| `sell(symbol, name, price, quantity, trade_date)` | 卖出，持仓不足抛 ValueError，清零自动删除 |
| `get_holding(symbol)` | 查询单只持仓 |
| `get_all_holdings()` | 返回全部持仓列表 |
| `update_all_prices(price_map)` | 批量刷新持仓的当前价格和市值 |
| `settle()` | 结算：汇总盈亏，返回 SettlementResult |
| `reset()` | 清空持仓和交易记录 |

## 数据模型（core/models/trade.py）

| 类 | 说明 |
|----|------|
| `TradeAction(Enum)` | BUY / SELL |
| `TradeRecord` | 单笔交易记录（symbol, name, action, price, quantity, trade_date, total_amount） |
| `HoldingPosition` | 持仓（symbol, name, quantity, average_cost, total_cost, current_price, current_value, pnl_amount, pnl_percent） |
| `SettlementResult` | 结算结果（total_cost, total_value, total_pnl_amount, total_pnl_percent, trade_count, trade_records, holdings_at_settle） |

### HoldingPosition 方法
- `update_current_price(price)` — 更新当前价格并重新计算盈亏

## 使用场景

### 1. 选股页模拟交易（ScreeningPage）
- A 股 T+1：当天买入不可当天卖出（在 ScreeningPage._on_sell 中检查）
- 买入方式：开盘价买入 / 收盘价买入
- 卖出方式：按市价卖出
- 结算：计算所有持仓的总成本、总市值、盈亏

### 2. 回测引擎（BacktestEngine）
- 回测引擎有自己的持仓管理（`BacktestHolding`），不直接使用 TradeSimulator
- 但共享 `TradeAction` 枚举和交易概念
