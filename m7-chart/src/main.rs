//! 美股七巨头 (Magnificent 7) 近一个月走势图。
//!
//! 基于 GPUI Component 0.5.1 (gpui 0.2.2) 的桌面图表应用:
//! - `gpui_component::init` 初始化组件库, `Root` 包裹首视图
//! - 行情数据由后台线程抓取 (Yahoo Finance / Stooq), 不阻塞 UI
//! - 七条折线共享坐标系, 归一化为相对月初的百分比变化

mod app;
mod data;
mod multi_line;

use app::M7Chart;
use gpui::{px, size, AppContext, Application, Bounds, WindowBounds, WindowOptions};
use gpui_component::{Root, WindowExt};

fn main() {
    Application::new().run(|cx| {
        // 必须先于任何 GPUI Component 组件使用
        gpui_component::init(cx);

        let bounds = Bounds::centered(None, size(px(1180.), px(800.)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                ..Default::default()
            },
            |window, cx| {
                let view = cx.new(|_| M7Chart::new());
                // 窗口第一层必须是 Root
                cx.new(|cx| Root::new(view, window, cx))
            },
        )
        .expect("Failed to open window");
    });
}
