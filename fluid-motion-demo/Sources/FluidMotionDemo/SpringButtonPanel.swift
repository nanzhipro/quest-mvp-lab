import SwiftUI

/// 面板一：弹簧按钮（DESIGN.md 结构层 + 触觉）。
/// 三个按钮分别演示 snappy / fluid / playful 三档弹簧 + 同帧触觉。
struct SpringButtonPanel: View {
    var body: some View {
        VStack(spacing: 36) {
            Text("弹簧按钮")
                .font(.largeTitle.bold())
            Text("按下缩放（scale.pressed 0.94），松手弹簧回弹，触觉同帧")
                .font(.callout)
                .foregroundStyle(.secondary)

            HStack(spacing: 24) {
                SpringPressButton(title: "snappy", color: .blue,
                                  animation: .snappySpring, haptic: .light)
                SpringPressButton(title: "fluid", color: .teal,
                                  animation: .fluidSpring, haptic: .medium)
                SpringPressButton(title: "playful", color: .pink,
                                  animation: .playfulSpring, haptic: .medium)
            }

            HStack(spacing: 8) {
                Text("snappy").font(.caption.monospaced()).foregroundStyle(.blue)
                Text("· 0.25 / 0.85").font(.caption).foregroundStyle(.secondary)
                Text("fluid").font(.caption.monospaced()).foregroundStyle(.teal)
                Text("· 0.40 / 0.70").font(.caption).foregroundStyle(.secondary)
                Text("playful").font(.caption.monospaced()).foregroundStyle(.pink)
                Text("· 0.55 / 0.50").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(40)
    }
}

/// 可复用：按压缩放的弹簧按钮（`ButtonStyle` 捕获 isPressed，触觉与视觉同帧）。
struct SpringPressButton: View {
    let title: String
    let color: Color
    let animation: Animation
    let haptic: Haptics.Intensity

    var body: some View {
        Button(title) {}
            .font(.title3.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 30)
            .padding(.vertical, 18)
            .background(color.gradient, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .buttonStyle(SpringPressStyle(animation: animation, haptic: haptic))
    }
}

struct SpringPressStyle: ButtonStyle {
    var animation: Animation
    var haptic: Haptics.Intensity

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? Motion.Scale.pressed : 1.0)
            .animation(animation, value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, pressed in
                // 按下瞬间（与缩放动画同帧）触发触觉
                if pressed { Haptics.impact(haptic) }
            }
    }
}
