# 模拟交易系统

**文件：** `core/trade/simulator.py` (~161 行)
**数据模型：** `core/models/trade.py`

## TradeSimulator(dataclass, slots=True)

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
| `TradeRecord` | 单笔交易记录（symbol, name, action, price, quantity, trade_date） |
| `HoldingPosition` | 持仓（symbol, name, cost_price, quantity, current_price, market_value） |
| `SettlementResult` | 结算结果（total_cost, total_market_value, profit, profit_pct） |

## 交易规则（由 ScreeningPage 实现）

- A 股 T+1：当天买入不可当天卖出（在 ScreeningPage._on_sell 中检查）
- 买入方式：开盘价买入 / 收盘价买入
- 卖出方式：按市价卖出
- 结算：计算所有持仓的总成本、总市值、盈亏
