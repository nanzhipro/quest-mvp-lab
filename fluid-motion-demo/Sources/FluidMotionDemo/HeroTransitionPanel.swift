import SwiftUI

/// 面板二：Hero 转场（DESIGN.md 结构层 · matchedGeometryEffect）。
/// 点击卡片，卡片「旅行」为全屏详情；点击详情回到网格。
struct HeroTransitionPanel: View {
    @Namespace private var ns
    @State private var selected: SampleCard?

    var body: some View {
        ZStack {
            if let selected {
                detail(selected)
            } else {
                grid
            }
        }
        .animation(.fluidSpring, value: selected)
    }

    private var grid: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 16)], spacing: 16) {
                ForEach(SampleCard.all) { card in
                    cardThumb(card)
                        .matchedGeometryEffect(id: card.id, in: ns)
                        .onTapGesture { withAnimation(.fluidSpring) { selected = card } }
                }
            }
            .padding(32)
        }
    }

    private func detail(_ card: SampleCard) -> some View {
        VStack(spacing: 20) {
            Image(systemName: card.symbol)
                .font(.system(size: 88))
                .foregroundStyle(.white)
            Text(card.title)
                .font(.system(size: 56, weight: .bold))
                .foregroundStyle(.white)
            Text("点击任意处返回网格")
                .font(.title3)
                .foregroundStyle(.white.opacity(0.8))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(card.color.gradient)
        .matchedGeometryEffect(id: card.id, in: ns)
        .onTapGesture { withAnimation(.fluidSpring) { selected = nil } }
    }

    private func cardThumb(_ card: SampleCard) -> some View {
        VStack(spacing: 12) {
            Image(systemName: card.symbol)
                .font(.system(size: 44))
                .foregroundStyle(.white)
            Text(card.title)
                .font(.headline)
                .foregroundStyle(.white)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 130)
        .background(card.color.gradient, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}

struct SampleCard: Identifiable, Hashable {
    let title: String
    let symbol: String
    let color: Color
    var id: String { title }

    static let all: [SampleCard] = [
        .init(title: "山脉", symbol: "mountain.2.fill", color: .indigo),
        .init(title: "海洋", symbol: "water.waves", color: .cyan),
        .init(title: "森林", symbol: "leaf.fill", color: .green),
        .init(title: "星空", symbol: "sparkles", color: .purple),
        .init(title: "火焰", symbol: "flame.fill", color: .orange),
        .init(title: "极光", symbol: "wind", color: .teal),
    ]
}
