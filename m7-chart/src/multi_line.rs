//! 多系列折线图元素: 在同一个坐标系中叠加绘制多条归一化折线。
//!
//! gpui-component 自带的 LineChart 每个实例都会绘制自己的坐标轴,
//! 多个叠加会互相覆盖。因此这里直接用其公开的底层 plot 原语
//! (`Line` / `PlotAxis` / `Grid` / `ScaleLinear` / `ScalePoint`)
//! 自绘一个共享坐标系的叠加图, 并通过 `IntoPlot` 派生宏接入元素系统。

use gpui::{point, px, App, Bounds, Hsla, PathBuilder, Pixels, TextAlign, Window};
use gpui_component::plot::{
    scale::{Scale, ScaleLinear, ScalePoint},
    shape::Line,
    AxisText, Grid, IntoPlot, Plot, PlotAxis, StrokeStyle, AXIS_GAP,
};
use gpui_component::{ActiveTheme, PixelsExt};

/// 一条待绘制的序列。
pub struct PlotSeries {
    /// 系列名 (图例用)。
    pub name: String,
    /// 折线颜色。
    pub color: Hsla,
    /// 归一化后的数据点 (相对月初的百分比变化, 如 2.5 表示 +2.5%)。
    /// 注意: gpui-component 的 scale 只为 f64 实现 Sealed, 故用 f64。
    pub values: Vec<f64>,
}

/// 单坐标系多折线图。
#[derive(IntoPlot)]
pub struct MultiLineChart {
    series: Vec<PlotSeries>,
    /// x 轴刻度标签: (数据索引, 标签文本)。
    x_labels: Vec<(usize, String)>,
    /// y 轴取值范围 (相对月初百分比)。
    y_min: f64,
    y_max: f64,
}

impl MultiLineChart {
    pub fn new(
        series: Vec<PlotSeries>,
        x_labels: Vec<(usize, String)>,
        y_min: f64,
        y_max: f64,
    ) -> Self {
        Self {
            series,
            x_labels,
            y_min,
            y_max,
        }
    }
}

impl Plot for MultiLineChart {
    fn paint(&mut self, bounds: Bounds<Pixels>, window: &mut Window, cx: &mut App) {
        let width = bounds.size.width.as_f32();
        let height = bounds.size.height.as_f32() - AXIS_GAP;

        // x: 数据索引 → 像素 (等距点位)
        let x = ScalePoint::new(
            (0..self.series.len().max(1)).map(|i| i as f64).collect(),
            vec![0., width],
        );
        // y: 百分比变化 → 像素 (底部小、顶部大)
        let y = ScaleLinear::new(vec![self.y_min, self.y_max], vec![height, 10.]);

        // 横向网格 (4 等分)
        Grid::new()
            .y((0..=4).map(|i| height * i as f32 / 4.0).collect())
            .stroke(cx.theme().border)
            .dash_array(&[px(4.), px(2.)])
            .paint(&bounds, window);

        // 0% 参考线 (涨跌分界)
        if self.y_min < 0.0 && self.y_max > 0.0 {
            if let Some(y0) = y.tick(&0.0) {
                let mut builder = PathBuilder::stroke(px(1.));
                builder.move_to(point(px(0.), px(y0)));
                builder.line_to(point(px(width), px(y0)));
                if let Ok(path) = builder.build() {
                    window.paint_path(path, cx.theme().muted_foreground.opacity(0.5));
                }
            }
        }

        // 各系列折线 (相对坐标, Line 内部会加上 bounds.origin)
        for s in &self.series {
            let data: Vec<(usize, f64)> =
                s.values.iter().enumerate().map(|(i, v)| (i, *v)).collect();
            // ScaleLinear/ScalePoint 非 Copy, 闭包按值捕获, 每轮 clone
            let x = x.clone();
            let y = y.clone();
            Line::new()
                .data(&data)
                .x(move |(i, _)| x.tick(&(*i as f64)))
                .y(move |(_, v)| y.tick(v))
                .stroke(s.color)
                .stroke_style(StrokeStyle::Linear)
                .stroke_width(1.8)
                .paint(&bounds, window);
        }

        // x 轴 + 日期刻度
        let x_labels = self
            .x_labels
            .iter()
            .filter_map(|(i, text)| {
                x.tick(&(*i as f64)).map(|tick| {
                    AxisText::new(text.clone(), tick, cx.theme().muted_foreground)
                        .align(TextAlign::Center)
                })
            })
            .collect::<Vec<_>>();
        PlotAxis::new()
            .x(height)
            .x_label(x_labels)
            .stroke(cx.theme().border)
            .paint(&bounds, window, cx);

        // y 轴 + 百分比刻度 (只画标签, 轴线下移到边界)
        let y_labels = (0..=4)
            .filter_map(|i| {
                let v = self.y_min + (self.y_max - self.y_min) * i as f64 / 4.0;
                y.tick(&v).map(|tick| {
                    AxisText::new(format!("{v:+.1}%"), tick, cx.theme().muted_foreground)
                })
            })
            .collect::<Vec<_>>();
        PlotAxis::new()
            .y(px(0.))
            .y_label(y_labels)
            .hide_x_axis()
            .stroke(cx.theme().border)
            .paint(&bounds, window, cx);
    }
}
