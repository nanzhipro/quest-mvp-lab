import SwiftUI

/// 面板六：Liquid Glass 质感（DESIGN.md 质感层 · macOS 26+）。
/// 鲜艳渐变底之上浮起玻璃卡片，展现玻璃折射 + 随内容变化的质感。
@available(macOS 26.0, *)
struct GlassEffectPanel: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [.pink, .orange, .yellow, .green, .blue, .purple],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
                .ignoresSafeArea()

            Circle().fill(.white.opacity(0.3)).frame(width: 120).offset(x: -180, y: -140).blur(radius: 2)
            Circle().fill(.cyan.opacity(0.4)).frame(width: 90).offset(x: 190, y: 150).blur(radius: 2)

            VStack(spacing: 28) {
                Text("圆角矩形玻璃")
                    .font(.title2.weight(.semibold))
                    .padding(.horizontal, 44)
                    .padding(.vertical, 26)
                    .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 18, style: .continuous))

                Text("胶囊玻璃")
                    .font(.title2.weight(.semibold))
                    .padding(.horizontal, 52)
                    .padding(.vertical, 26)
                    .glassEffect(.regular, in: Capsule())
            }
        }
    }
}
