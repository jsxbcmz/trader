# 模板页 TemplatePage

**文件：** `app/pages/template_page.py` (~200 行)

## 页面定位

选股模板的 CRUD 管理页面，左右分栏布局（QSplitter 2:3）。左侧模板列表，右侧详情展示（名称、描述、通达信条件代码）。

## 类结构

### TemplatePage(QtWidgets.QWidget)

**信号：**
- `statusMessageRequested = Signal(str, int)`
- `templatesChanged = Signal()` — 模板增删改后触发，MarketPage、ScreeningPage、BacktestPage 监听此信号刷新下拉框
- `backtestRequested = Signal(str)` — 发射模板 ID，请求跳转回测页

**构造参数：** `root: Path`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `template_service` | TemplateService 实例 |
| `_templates` | 当前加载的模板列表 `list[ScreeningTemplate]` |

## 公开方法

| 方法 | 说明 |
|------|------|
| `refresh_templates(selected_id=None)` | 从服务重新加载模板列表并渲染表格，可保持选中项 |
| `get_selected_template()` | 返回当前表格选中的模板对象 |
| `open_create_dialog()` | 打开新建模板对话框 |
| `open_edit_dialog()` | 打开编辑对话框 |
| `duplicate_selected_template()` | 复制当前模板 |
| `delete_selected_template()` | 删除当前模板（含确认弹窗） |

## 内部方法

- `_on_selection_changed()` — 表格选中变化 → 渲染右侧详情
- `_on_backtest_clicked()` — 回测按钮 → 发射 `backtestRequested` 信号
- `_update_action_states(template)` — 根据是否有选中模板控制按钮可用状态

## UI 结构

1. 操作按钮行：新建、编辑、复制、删除 | 回测 | 刷新
2. QSplitter 左右分栏：
   - 左侧：QTableWidget（2列：名称、更新时间）
   - 右侧：QFormLayout 详情面板（名称 Label、描述 Label、条件代码 PlainTextEdit 只读）

## 跨页通信

```
templatesChanged → MainWindow → marketPage.reload_templates()
templatesChanged → MainWindow → screeningPage.reload_templates()
templatesChanged → MainWindow → backtestPage.reload_templates()
backtestRequested(template_id) → MainWindow._on_backtest_requested() → backtestPage.prefill_template() + switch_page(4)
```

## 模块依赖

- `TemplateService` — CRUD 操作
- `TemplateEditorDialog` (`app/dialogs/template_editor_dialog.py`) — 新建/编辑弹窗
- `ScreeningTemplate` 数据模型
