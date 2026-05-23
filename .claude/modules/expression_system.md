# 通达信表达式系统

**目录：** `core/expression/`

将通达信（TDX）选股条件语言编译为内部 AST，再在 DataFrame 上求值。

## 编译流水线

```
TDX 源码
  → TdxLexer (词法分析, Token 序列)       core/expression/parser/lexer.py
  → TdxParser (语法分析, TDX AST)          core/expression/parser/parser.py
  → TdxTranspiler (转换, ExpressionNode)   core/expression/parser/transpiler.py
  → evaluate_expression (求值, ndarray)    core/expression/evaluator.py
```

---

## nodes.py (~84 行) — AST 节点定义

所有节点均为 `frozen=True, slots=True` 的 dataclass。

| 类名 | 说明 |
|------|------|
| `ExpressionNode` | 基类，携带 `kind` 字段 |
| `ConstantNode` | 常量值（数字/字符串） |
| `FieldNode` | 字段引用（CLOSE/OPEN 等），支持 `offset` 时间偏移 |
| `FunctionNode` | 函数调用，`name` + `args: tuple[ExpressionNode, ...]` |
| `ComparisonNode` | 比较运算（`>/<=/!=` 等） |
| `LogicalNode` | 逻辑运算（`and/or/not`） |
| `MathNode` | 数学运算（`+/-/*/`） |

---

## lexer.py (~305 行) — 词法分析

### TdxTokenKind(Enum)
NUMBER, STRING, IDENTIFIER, 算术/比较/逻辑运算符, 括号/分隔符, COMMENT, EOF

### TdxLexer
- `tokenize()` — 源码 → Token 列表（带缓存）
- 支持中文标识符（`\u4e00-\u9fff`）
- 支持通达信 `AND#`/`OR#` 变体
- `{...}` 注释块跳过

---

## parser.py (~380 行) — 语法分析

### TDX AST 节点（中间表示）

| 类名 | 说明 |
|------|------|
| `TdxNumber` | 数字字面量 |
| `TdxString` | 字符串字面量 |
| `TdxIdentifier` | 变量引用 |
| `TdxField` | 字段引用 |
| `TdxFunctionCall` | 函数调用 |
| `TdxUnaryOp` | 一元运算（`-`/`NOT`） |
| `TdxBinaryOp` | 二元运算 |
| `TdxAssignment` | 赋值语句 `name := expr;` |
| `TdxOutput` | 输出语句 `name: expr, style;` |
| `TdxProgram` | 完整程序（含 `variables` 和 `outputs` 字典） |

### TdxParser — 递归下降解析
优先级从低到高：`logical → comparison → additive → multiplicative → unary → primary`

`TdxProgram.get_output_expression(name)` — 取出输出变量表达式（默认 `"选股"`）

---

## transpiler.py (~179 行) — AST 转换

### TdxTranspiler
将 TDX AST → 内部 ExpressionNode，同时处理变量内联展开和循环依赖检测。

| 方法 | 说明 |
|------|------|
| `transpile(output_name="选股")` | 入口 |
| `_convert_expression(expr)` | 递归按类型分发 |
| `_expand_variable(name)` | 变量内联（`_expanding` 集合防循环） |

**便捷函数：** `transpile_tdx_source(source, output_name)` — Lexer → Parser → Transpiler 一步完成

---

## evaluator.py (~146 行) — 求值引擎

### EvaluationContext(dataclass)
- `df: pd.DataFrame` — 待求值的数据
- `target_index: int | None` — 选股时的目标行索引
- `cache: dict` — 计算缓存
- `get_field_values(field_name, offset)` — 处理字段别名和时间偏移

### 核心函数

| 函数 | 说明 |
|------|------|
| `evaluate_expression(node, context)` | 递归求值，返回 ndarray 或标量 |
| `evaluate_at_index(node, context, index)` | 求值后取目标位置的值 |

**字段别名：** `OPEN/HIGH/LOW/CLOSE/VOLUME/VOL/AMOUNT/DATE` → DataFrame 列名

**依赖：** `core.indicators.registry.get_function_spec`（函数节点求值时查找注册表）

---

## 关键设计

- 表达式在主进程只解析一次，pickle 序列化后传入子进程，避免重复解析
- `ExpressionNode` 是不可变 frozen dataclass，可安全序列化
- 变量展开在转换阶段完成，求值阶段无需变量查找
