# 应用框架

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `app/main_window.py` | ~150 | 主窗口，4页面切换+信号中枢 |
| `app/services/settings_service.py` | ~96 | QSettings 持久化 |
| `app/components/settings_form.py` | ~41 | 设置表单复用组件 |
| `app/utils/thread_manager.py` | ~47 | 后台线程启动工具 |
| `app/main.py` | - | 应用入口 |

---

## main_window.py — MainWindow(QMainWindow)

### 页面管理
- `pageStack: QStackedWidget` 管理 4 个页面
- Index 0: MarketPage, 1: TemplatePage, 2: SettingsPage, 3: ScreeningPage

### 菜单结构
- 文件：退出
- 视图：打开各页面
- 数据：更新全部股票
- 工具：新建模板
- 帮助：关于

### 信号连接链（核心跨页通信）

```
templatePage.templatesChanged → marketPage.reload_templates()
templatePage.templatesChanged → screeningPage.reload_templates()

settingsPage.settingsSaveRequested → _save_settings_from_page()
  → SettingsService.save() → MarketPage.apply_settings()

settingsPage.updateAllRequested → _request_update_all()
  → MarketPage.start_update_all()

marketPage.updateRunningChanged → _on_update_running_changed()
  → settingsPage.set_update_enabled()

各页面.statusMessageRequested → statusBar
```

### 关键方法

| 方法 | 说明 |
|------|------|
| `switch_page(index)` | 切换页面 |
| `_save_settings_from_page(app_settings)` | 保存设置并同步 |
| `_request_update_all()` | 转发更新请求 |
| `closeEvent(event)` | 关闭时持久化状态 |

---

## settings_service.py — SettingsService

### AppSettings(frozen dataclass, slots=True)

| 字段 | 说明 |
|------|------|
| `min_visible_days` | 图表最小可见天数 |
| `max_visible_days` | 图表最大可见天数 |
| `last_selected_symbol` | 上次选中股票代码 |

### SettingsService

基于 `QSettings("StockViewer", "StockViewer")` 持久化。

| 方法 | 说明 |
|------|------|
| `load() -> AppSettings` | 读取（含边界修正） |
| `save(settings) -> AppSettings` | 正规化后写入 |
| `validate_settings(min, max)` | 校验规则 |
| `normalize_settings(...)` | 类型转换+校验+构造 |
| `get_last_selected_symbol()` | 读取上次选中代码 |
| `save_last_selected_symbol(symbol)` | 持久化代码（补零6位） |
| `get_chart_limits()` | 返回 (min, max) |

**存储 Key：** `chart/min_visible_days`, `chart/max_visible_days`, `last_selected_symbol`

---

## settings_form.py — SettingsFormWidget(QWidget)

纯 UI 表单，无信号无状态，可复用于 MarketPage 和 SettingsPage。

**控件：** `min_days_spin` (1~10000), `max_days_spin` (2~10000)
**方法：** `get_min_days()`, `get_max_days()`, `set_values(min, max)`

---

## thread_manager.py — start_worker

```python
start_worker(parent, worker, *,
    on_progress, on_finished, on_error, on_cleanup) -> QThread
```

- 创建 QThread 并 moveToThread
- 自动连接 started → worker.run
- 按需连接 progressChanged/finished/errorOccurred
- 自动清理（quit → deleteLater）

**Worker 协议：** 必须提供 `progressChanged(dict)`, `finished(dict)`, `errorOccurred(str)` 信号和 `run()` slot
