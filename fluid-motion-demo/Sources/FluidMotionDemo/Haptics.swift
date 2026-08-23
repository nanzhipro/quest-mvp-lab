import AppKit

/// 触觉同步（DESIGN.md 点睛层）。
///
/// macOS 现实（已核验 Apple 文档）：SwiftUI `SensoryFeedback` 官方标注
/// 「Only plays feedback on iOS and watchOS」，在 macOS 上是 no-op。
/// 真正的 macOS 触觉走 `NSHapticFeedbackManager`，在带 Force Touch 触控板的
/// 机型（MacBook）上播放，桌面机型为无操作。视觉与触觉在「同帧」触发（见 ButtonStyle）。
enum Haptics {

    enum Intensity {
        case light, medium, heavy
    }

    static func impact(_ intensity: Intensity) {
        let pattern: NSHapticFeedbackManager.FeedbackPattern
        switch intensity {
        case .light: pattern = .alignment   // 轻：轻微对齐「哒」
        case .medium: pattern = .generic    // 中：通用「咚」
        case .heavy: pattern = .levelChange // 重：层级变化「咔」
        }
        NSHapticFeedbackManager.defaultPerformer.perform(pattern, performanceTime: .now)
    }
}
