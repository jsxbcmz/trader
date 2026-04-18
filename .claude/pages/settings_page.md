# 设置页 SettingsPage

**文件：** `app/pages/settings_page.py` (~85 行)

## 页面定位

全局配置页面，直接内联表单布局。配置项包括 Tushare Token、图表最小/最大可视天数、批量数据更新入口。

## 类结构

### SettingsPage(QtWidgets.QWidget)

**信号：**
- `statusMessageRequested = Signal(str, int)`
- `settingsSaveRequested = Signal(object)` — 携带 `AppSettings` 对象，由 MainWindow 接收并执行保存
- `updateAllRequested = Signal()` — 触发全量数据更新

**构造参数：** `settings_service: SettingsService`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `settings_service` | 设置服务实例 |
| `tokenEdit` | Token 输入框 |
| `minDaysSpin / maxDaysSpin` | 图表天数 SpinBox |
| `updateAllBtn` | 更新全部股票按钮 |

## 公开方法

| 方法 | 说明 |
|------|------|
| `set_settings(app_settings)` | 将 AppSettings 值写入表单 |
| `set_update_enabled(enabled)` | 控制"更新全部股票"按钮的可用状态 |

## UI 结构

1. 页面描述文字
2. 数据接口 GroupBox：Tushare Token 输入
3. 图表显示 GroupBox：最小/最大可见天数 SpinBox
4. 数据更新 GroupBox：提示文字 + 更新全部股票按钮
5. 保存设置按钮

## 信号流

```
用户点击保存 → _emit_save_request() → settingsSaveRequested(AppSettings)
  → MainWindow._save_settings_from_page()
    → SettingsService.save()
    → MarketPage.apply_settings()

用户点击更新 → updateAllRequested
  → MainWindow._request_update_all()
    → MarketPage.start_update_all()
```

## 模块依赖

- `SettingsService` — 读取初始值、`normalize_settings()` 校验
- `AppSettings` 数据类
