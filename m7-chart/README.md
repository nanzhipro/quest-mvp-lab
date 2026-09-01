# m7-chart — Magnificent 7 近一个月走势图（GPUI Component）

> **纯 Rust 桌面图表 Demo**：用 GPUI Component 的底层 plot 原语自绘共享坐标系的多折线叠加图，
> 实时抓取美股七巨头（AAPL / MSFT / GOOGL / AMZN / NVDA / META / TSLA）近一个月日线，
> 归一化为相对月初的百分比变化对比展示。

![Rust](https://img.shields.io/badge/Rust-2024-orange.svg)
![GPUI](https://img.shields.io/badge/GPUI-0.2.2-blue.svg)
![gpui--component](https://img.shields.io/badge/gpui--component-0.5.1-blue.svg)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)

## 背景

验证 **GPUI Component 的图表能力边界**：`gpui-component` 自带的 `LineChart` 每个实例都会绘制自己的坐标轴，
多个图表叠加时坐标轴互相覆盖，无法直接实现「七条折线共享同一坐标系」的对比图。
本 Demo 的结论是：绕开封装好的 chart 组件，直接用其公开的底层 plot 原语即可干净地实现。

## 核心技术方案

**一句话**：行情抓取放后台线程（不阻塞 UI），绘图层用 `gpui_component::plot` 的
`Line` / `PlotAxis` / `Grid` / `ScaleLinear` / `ScalePoint` 原语自绘单坐标系多折线图，
通过 `IntoPlot` 派生宏接入 GPUI 元素系统。

### 模块结构

| 文件 | 职责 |
| --- | --- |
| `src/main.rs` | 应用入口：`gpui_component::init` 初始化组件库，`Root` 包裹首视图，打开 1180×800 窗口 |
| `src/app.rs` | 主视图状态机（加载中 → 成功 / 失败 + 重试按钮）、数据归一化、标题与图例（色块 + 最新价 + 红涨绿跌涨跌幅） |
| `src/data.rs` | 行情抓取：Yahoo Finance chart API 为主、Stooq CSV 兜底，均公开免 Key；单只失败跳过，全部失败才报错 |
| `src/multi_line.rs` | `MultiLineChart`：共享坐标系的多折线 plot 元素（网格、0% 参考线、双轴刻度） |

### 关键工程难点（为什么这么做）

1. **共享坐标系必须自绘**：`LineChart` 的轴跟随实例走，叠加即互相覆盖；改为单个 `MultiLineChart`
   内部循环绘制多条 `Line`，坐标轴、网格只画一份。
2. **scale 只为 `f64` 实现（Sealed）**：`ScaleLinear` / `ScalePoint` 的 tick 只接受 `f64`，
   数据点统一用 `f64`；且两个 scale 均非 `Copy`，闭包按值捕获时每轮 `clone`。
3. **后台抓取不阻塞 UI**：`cx.spawn` + `background_executor()` 在后台线程跑同步 HTTP（`ureq`），
   完成后 `cx.update` 切回主线程更新状态并 `cx.notify()` 重绘。
4. **归一化对比**：各股价量级差异大（TSLA 与 AAPL 差一个数量级），统一换算为
   `(close / 首日收盘 - 1) × 100%`；y 轴外扩 8% 边距且必须包住 0% 基准线。
5. **x 轴刻度稀疏化**：每 5 个交易日一个日期标签，末尾交易日必有一个。

### 数据源与容错

- **Yahoo Finance** `v8/finance/chart`（`range=1mo&interval=1d`）为主，**Stooq** CSV 为兜底，均无需 API Key。
- 单只股票两个源都失败时跳过该系列；七只全部失败才进入错误态（界面提供「重试」按钮）。
- 折线颜色取各公司官方品牌色（Apple 灰 / Microsoft 蓝 / Google 蓝 / Amazon 橙 / NVIDIA 绿 / Meta 蓝 / Tesla 红）。

## 构建 / 运行 / 验证

```bash
cargo run          # 调试构建并运行（需要联网抓取行情）
cargo run --release
cargo check        # 类型检查
```

运行后窗口展示：标题与数据范围 → 七条归一化折线（横向网格 + 0% 涨跌分界线 + 双轴刻度）→
底部图例（色块、代码、最新价、涨跌幅，红涨绿跌）。

## 结论

- GPUI Component 0.5.1（gpui 0.2.2）的底层 plot 原语足以自绘专业级的共享坐标系多折线图，
  封装好的 `LineChart` 不适合多系列叠加场景。
- 「后台线程抓数据 + 主线程状态机重绘」的模式在 GPUI 下工作良好，适合作为后续 GPUI 数据类应用的模板。
