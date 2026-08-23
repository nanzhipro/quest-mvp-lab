import SwiftUI

/// 面板三：MeshGradient 活背景（DESIGN.md 天花板层）。
/// 4×4 网格渐变的顶点持续漂移（gentle 4s 循环），形成「活」的液态渐变。
struct MeshGradientPanel: View {
    @State private var points: [SIMD2<Float>] = MeshGradientPanel.base

    var body: some View {
        MeshGradient(width: 4, height: 4,
                     points: points,
                     colors: MeshGradientPanel.colors)
            .ignoresSafeArea()
            .overlay(alignment: .bottom) {
                Text("MeshGradient · gentle 4s 循环")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(12)
                    .background(.black.opacity(0.25), in: Capsule())
                    .padding(20)
            }
            .onAppear(perform: animate)
    }

    private func animate() {
        withAnimation(.easeInOut(duration: 4).repeatForever(autoreverses: true)) {
            points = MeshGradientPanel.drifted
        }
    }

    // MARK: - 网格顶点（0…1 归一化，4×4 = 16 点）

    static let base: [SIMD2<Float>] = {
        var pts: [SIMD2<Float>] = []
        for y in 0..<4 {
            for x in 0..<4 {
                pts.append(SIMD2(Float(x) / 3.0, Float(y) / 3.0))
            }
        }
        return pts
    }()

    /// 只扰动内部顶点（角点保持 0/1，避免边缘留缝）。
    static let drifted: [SIMD2<Float>] = {
        let offsets: [SIMD2<Float>] = [
            SIMD2(0.00, 0.00), SIMD2(0.10, -0.06), SIMD2(-0.08, 0.12), SIMD2(0.00, 0.00),
            SIMD2(-0.12, 0.08), SIMD2(0.14, 0.10), SIMD2(0.10, -0.14), SIMD2(0.12, 0.06),
            SIMD2(0.08, -0.10), SIMD2(-0.10, 0.12), SIMD2(0.12, 0.10), SIMD2(-0.08, -0.12),
            SIMD2(0.00, 0.00), SIMD2(-0.10, 0.08), SIMD2(0.10, 0.10), SIMD2(0.00, 0.00),
        ]
        return zip(base, offsets).map { $0 + $1 }
    }()

    static let colors: [Color] = [
        .blue, .cyan, .teal, .mint,
        .indigo, .purple, .pink, .orange,
        .cyan, .mint, .yellow, .pink,
        .purple, .orange, .red, .indigo,
    ]
}
