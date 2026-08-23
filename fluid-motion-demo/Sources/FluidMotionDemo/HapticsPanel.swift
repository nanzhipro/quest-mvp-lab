import SwiftUI

/// 面板五：触觉同步（DESIGN.md 点睛层）。
/// 三种触觉强度，均与视觉弹簧同帧触发。macOS 触觉走 NSHapticFeedbackManager。
struct HapticsPanel: View {
    var body: some View {
        VStack(spacing: 32) {
            Text("触觉同步")
                .font(.largeTitle.bold())
            Text("视觉弹簧与触觉同帧触发（按下瞬间同步）")
                .font(.callout)
                .foregroundStyle(.secondary)

            HStack(spacing: 20) {
                SpringPressButton(title: "轻 · alignment", color: .gray,
                                  animation: .snappySpring, haptic: .light)
                SpringPressButton(title: "中 · generic", color: .orange,
                                  animation: .snappySpring, haptic: .medium)
                SpringPressButton(title: "重 · levelChange", color: .red,
                                  animation: .snappySpring, haptic: .heavy)
            }

            Text("macOS 触觉依赖 Force Touch 触控板（MacBook）；桌面机型为无操作。iOS/watchOS 对应 API 为 SwiftUI sensoryFeedback。")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
        .padding(40)
    }
}
