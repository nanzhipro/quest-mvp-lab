import SwiftUI

/// 弹簧动画别名（对应 DESIGN.md `motion.spring`）。
extension Animation {
    /// `spring.snappy` — 按钮、开关、即时反馈
    static var snappySpring: Animation {
        .spring(response: Motion.Spring.snappy.response,
                dampingFraction: Motion.Spring.snappy.dampingFraction)
    }
    /// `spring.fluid` — 卡片、弹层、通用 UI
    static var fluidSpring: Animation {
        .spring(response: Motion.Spring.fluid.response,
                dampingFraction: Motion.Spring.fluid.dampingFraction)
    }
    /// `spring.playful` — 点赞、成就、空状态
    static var playfulSpring: Animation {
        .spring(response: Motion.Spring.playful.response,
                dampingFraction: Motion.Spring.playful.dampingFraction)
    }
    /// `spring.gentle` — 大面板、氛围背景
    static var gentleSpring: Animation {
        .spring(response: Motion.Spring.gentle.response,
                dampingFraction: Motion.Spring.gentle.dampingFraction)
    }
}
