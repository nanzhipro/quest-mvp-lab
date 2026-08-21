//! 主视图: 美股七巨头近一个月走势对比图。

use gpui::{rgb, App, Context, Entity, Hsla, IntoElement, ParentElement, Render, Styled, Window};
use gpui_component::{
    button::{Button, ButtonVariants},
    ActiveTheme, h_flex, v_flex,
};

use crate::data::{self, Series};
use crate::multi_line::{MultiLineChart, PlotSeries};

/// 视图状态机: 加载中 → 成功 (数据) / 失败 (错误信息)。
pub struct M7Chart {
    state: State,
    load_started: bool,
}

enum State {
    Loading,
    Loaded(Vec<Series>),
    Error(String),
}

/// 七巨头品牌色 (与各公司官方品牌色一致)。
fn ticker_color(ticker: &str) -> Hsla {
    let hex = match ticker {
        "AAPL" => 0xA2AAAD,  // Apple 灰
        "MSFT" => 0x00A4EF,  // Microsoft 蓝
        "GOOGL" => 0x4285F4, // Google 蓝
        "AMZN" => 0xFF9900,  // Amazon 橙
        "NVDA" => 0x76B900,  // NVIDIA 绿
        "META" => 0x0866FF,  // Meta 蓝
        "TSLA" => 0xE31937,  // Tesla 红
        _ => 0x888888,
    };
    Hsla::from(rgb(hex))
}

impl M7Chart {
    pub fn new() -> Self {
        Self {
            state: State::Loading,
            load_started: false,
        }
    }

    /// 在后台线程抓取行情, 完成后切回主线程更新状态并重绘。
    fn load(&mut self, cx: &mut Context<Self>) {
        // Context::spawn 的闭包签名: async move |weak_self, cx: &mut AsyncApp|
        eprintln!("[m7] load(): spawning fetch task");
        cx.spawn(async move |this, cx| {
            eprintln!("[m7] spawn task started");
            let result = cx
                .background_executor()
                .spawn(async move {
                    eprintln!("[m7] background fetch start");
                    let r = data::fetch_all();
                    eprintln!("[m7] background fetch done: {:?}", r.as_ref().map(|s| s.len()).map_err(|e| e.len()));
                    r
                })
                .await;
            eprintln!("[m7] fetch task awaited, updating state");
            cx.update(|cx| {
                eprintln!("[m7] cx.update entered");
                if let Some(this) = this.upgrade() {
                    eprintln!("[m7] weak entity upgraded");
                    this.update(cx, |this, cx| {
                        eprintln!("[m7] entity.update entered");
                        this.state = match result {
                            Ok(series) => State::Loaded(series),
                            Err(err) => State::Error(err),
                        };
                        cx.notify();
                        eprintln!("[m7] state set + notified");
                    });
                } else {
                    eprintln!("[m7] WEAK ENTITY DEAD");
                }
            });
            eprintln!("[m7] cx.update returned");
        })
        .detach();
        eprintln!("[m7] load(): task detached");
    }

    /// 构建叠加图: 各系列归一化为相对月初的百分比变化, 共享同一坐标系。
    fn build_chart(&self, series: &[Series]) -> MultiLineChart {
        let mut all_values: Vec<f64> = vec![0.0];
        let plot_series: Vec<PlotSeries> = series
            .iter()
            .map(|s| {
                let first = s.points.first().map(|p| p.close).unwrap_or(1.0);
                let values: Vec<f64> = s
                    .points
                    .iter()
                    .map(|p| (p.close / first - 1.0) * 100.0)
                    .collect();
                all_values.extend(values.iter().copied());
                PlotSeries {
                    name: s.ticker.clone(),
                    color: ticker_color(&s.ticker),
                    values,
                }
            })
            .collect();

        // y 轴范围: 全系列 min..max, 外扩 8% 边距, 且必须包住 0% 基准线
        let y_min = all_values.iter().cloned().fold(f64::INFINITY, f64::min);
        let y_max = all_values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let pad = (y_max - y_min).abs() * 0.08 + 0.01;
        let y_min = (y_min - pad).min(0.0);
        let y_max = (y_max + pad).max(0.0);

        // x 轴刻度: 每 5 个交易日一个标签, 末尾必有一个
        let n = series.iter().map(|s| s.points.len()).max().unwrap_or(0);
        let mut x_labels: Vec<(usize, String)> = (0..n)
            .step_by(5)
            .filter_map(|i| {
                series
                    .first()
                    .and_then(|s| s.points.get(i))
                    .map(|p| (i, p.date.clone()))
            })
            .collect();
        if let Some(last) = x_labels.last() {
            if last.0 != n - 1 {
                if let Some(p) = series.first().and_then(|s| s.points.last()) {
                    x_labels.push((n - 1, p.date.clone()));
                }
            }
        }

        MultiLineChart::new(plot_series, x_labels, y_min, y_max)
    }

    /// 顶部标题区。
    fn header(&self, series: &[Series], theme: &gpui_component::Theme) -> impl IntoElement {
        let range = series
            .first()
            .map(|s| format!("{} ~ {}", s.first_date(), s.last_date()))
            .unwrap_or_default();
        v_flex()
            .gap_1()
            .child(
                gpui::div()
                    .text_size(gpui::px(22.))
                    .font_weight(gpui::FontWeight::BOLD)
                    .text_color(theme.foreground)
                    .child("Magnificent 7 · 近一个月走势"),
            )
            .child(
                gpui::div()
                    .text_size(gpui::px(12.))
                    .text_color(theme.muted_foreground)
                    .child(format!(
                        "数据范围 {range} · 基准 = 月初收盘价 (0%) · 数据源 Yahoo Finance / Stooq"
                    )),
            )
    }

    /// 底部图例: 色块 + 代码 + 最新价 + 涨跌幅 (红涨绿跌)。
    fn legend(&self, series: &[Series], theme: &gpui_component::Theme) -> impl IntoElement {
        let items = series
            .iter()
            .map(|s| {
                let pct = s.change_pct();
                let up = pct >= 0.0;
                let pct_color = if up { rgb(0xE5484D) } else { rgb(0x30A46C) };
                h_flex()
                    .gap_2()
                    .items_center()
                    .child(gpui::div().size_3().rounded_full().bg(ticker_color(&s.ticker)))
                    .child(
                        gpui::div()
                            .text_size(gpui::px(13.))
                            .font_weight(gpui::FontWeight::MEDIUM)
                            .text_color(theme.foreground)
                            .child(format!("{} ${:.2}", s.ticker, s.last_close())),
                    )
                    .child(
                        gpui::div()
                            .text_size(gpui::px(13.))
                            .font_weight(gpui::FontWeight::MEDIUM)
                            .text_color(pct_color)
                            .child(format!("{pct:+.2}%")),
                    )
            })
            .collect::<Vec<_>>();
        h_flex().flex_wrap().gap_x_5().gap_y_2().children(items)
    }
}

impl Render for M7Chart {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        // 首次渲染时触发数据加载
        if !self.load_started {
            self.load_started = true;
            self.load(cx);
        }

        let theme = cx.theme().clone();
        let content: gpui::AnyElement = match &self.state {
            State::Loading => v_flex()
                .size_full()
                .items_center()
                .justify_center()
                .child(
                    gpui::div()
                        .text_size(gpui::px(14.))
                        .text_color(theme.muted_foreground)
                        .child("正在获取行情数据…"),
                )
                .into_any_element(),
            State::Error(err) => {
                let this: Entity<Self> = cx.entity();
                v_flex()
                    .size_full()
                    .items_center()
                    .justify_center()
                    .gap_3()
                    .child(
                        gpui::div()
                            .text_size(gpui::px(14.))
                            .text_color(rgb(0xE5484D))
                            .child(format!("数据加载失败: {err}")),
                    )
                    .child(
                        Button::new("retry")
                            .primary()
                            .label("重试")
                            .on_click(move |_, _, cx| {
                                let _ = this.update(cx, |this, cx| {
                                    this.state = State::Loading;
                                    this.load_started = false;
                                    cx.notify();
                                });
                            }),
                    )
                    .into_any_element()
            }
            State::Loaded(series) => {
                let chart = self.build_chart(series);
                let header = self.header(series, &theme);
                let legend = self.legend(series, &theme);
                v_flex()
                    .size_full()
                    .p_6()
                    .gap_4()
                    .child(header)
                    .child(gpui::div().flex_1().child(chart))
                    .child(legend)
                    .into_any_element()
            }
        };

        v_flex().size_full().bg(theme.background).child(content)
    }
}
