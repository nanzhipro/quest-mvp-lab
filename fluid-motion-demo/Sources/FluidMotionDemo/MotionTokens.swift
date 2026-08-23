import CoreGraphics

/// `DESIGN.md`「灵动动效设计语言」token 的 Swift 常量映射。
/// 所有动效参数必须从这里取，禁止在视图里硬编码魔法数字。

/// 弹簧物理参数（response 响应时间 + dampingFraction 阻尼比）。
struct SpringParameters: Sendable {
    let response: Double
    let dampingFraction: Double
    init(response: Double, dampingFraction: Double) {
        self.response = response
        self.dampingFraction = dampingFraction
    }
}

enum Motion {

    enum Duration {
        static let instant: Double = 0.10
        static let quick: Double = 0.20
        static let standard: Double = 0.30
        static let expressive: Double = 0.50
        static let hero: Double = 0.70
    }

    enum Spring {
        static let snappy = SpringParameters(response: 0.25, dampingFraction: 0.85)
        static let fluid = SpringParameters(response: 0.40, dampingFraction: 0.70)
        static let playful = SpringParameters(response: 0.55, dampingFraction: 0.50)
        static let gentle = SpringParameters(response: 0.70, dampingFraction: 0.80)
    }

    enum Scale {
        static let pressed: CGFloat = 0.94
        static let hovered: CGFloat = 1.02
        static let emphasis: CGFloat = 1.08
        static let exit: CGFloat = 0.80
    }

    enum Distance {
        static let micro: CGFloat = 4
        static let small: CGFloat = 8
        static let medium: CGFloat = 16
        static let large: CGFloat = 48
    }

    enum Opacity {
        static let hidden: Double = 0.0
        static let scrim: Double = 0.4
        static let dimmed: Double = 0.6
        static let visible: Double = 1.0
    }
}
