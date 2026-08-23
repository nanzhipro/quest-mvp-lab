import SwiftUI

/// 面板四：Metal 液体波纹（DESIGN.md 天花板层 · layerEffect）。
/// 渐变 + 装饰图形之上叠加真·液体折射着色器；点击任意处，波纹从点击点向外扩散。
struct LiquidShaderPanel: View {
    @State private var origin = CGPoint(x: 300, y: 240)
    @State private var t0: TimeInterval = 0

    var body: some View {
        TimelineView(.animation) { timeline in
            let elapsed = timeline.date.timeIntervalSinceReferenceDate - t0
            if let shader = MetalShaders.refractionShader(origin: origin, time: Float(elapsed)) {
                content.layerEffect(shader, maxSampleOffset: CGSize(width: 24, height: 24))
            } else {
                content
            }
        }
        .ignoresSafeArea()
        .gesture(
            SpatialTapGesture().onEnded { value in
                origin = value.location
                t0 = Date().timeIntervalSinceReferenceDate
            }
        )
        .onAppear { t0 = Date().timeIntervalSinceReferenceDate }
    }

    private var content: some View {
        ZStack {
            LinearGradient(colors: [.blue, .purple, .pink, .orange],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            Circle().fill(.white.opacity(0.22)).frame(width: 96).offset(x: -160, y: -120)
            Circle().fill(.yellow.opacity(0.30)).frame(width: 72).offset(x: 170, y: 120)
            Circle().fill(.cyan.opacity(0.28)).frame(width: 60).offset(x: -40, y: 150)

            VStack(spacing: 12) {
                Image(systemName: "water.waves")
                    .font(.system(size: 56))
                    .foregroundStyle(.white)
                Text("点击任意处，产生液体波纹")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(.white)
            }
        }
    }
}
