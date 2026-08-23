import SwiftUI

/// 六个动效面板，对应 DESIGN.md 五轴架构 + 决策流程里的配方。
enum Panel: String, CaseIterable, Identifiable {
    case spring, hero, mesh, liquid, haptics, glass

    var id: String { rawValue }

    var title: String {
        switch self {
        case .spring: return "弹簧按钮"
        case .hero: return "Hero 转场"
        case .mesh: return "MeshGradient 活背景"
        case .liquid: return "Metal 液体波纹"
        case .haptics: return "触觉同步"
        case .glass: return "Liquid Glass 质感"
        }
    }

    /// DESIGN.md token 标注（侧栏副标题，展示每个配方用的 token）。
    var subtitle: String {
        switch self {
        case .spring: return "spring.snappy · fluid · playful + 触觉"
        case .hero: return "matchedGeometryEffect + spring.fluid"
        case .mesh: return "MeshGradient · gentle 4s 循环"
        case .liquid: return "Metal layerEffect · 液体折射"
        case .haptics: return "sensoryFeedback 同帧触发"
        case .glass: return "glassEffect(.regular)"
        }
    }

    var symbol: String {
        switch self {
        case .spring: return "circle.dotted.circle"
        case .hero: return "rectangle.2.swap"
        case .mesh: return "swirl.circle.righthalf.filled"
        case .liquid: return "water.waves"
        case .haptics: return "hand.tap"
        case .glass: return "cube.transparent"
        }
    }

    var tint: Color {
        switch self {
        case .spring: return .blue
        case .hero: return .indigo
        case .mesh: return .purple
        case .liquid: return .cyan
        case .haptics: return .pink
        case .glass: return .teal
        }
    }

    @ViewBuilder
    var detail: some View {
        switch self {
        case .spring: SpringButtonPanel()
        case .hero: HeroTransitionPanel()
        case .mesh: MeshGradientPanel()
        case .liquid: LiquidShaderPanel()
        case .haptics: HapticsPanel()
        case .glass:
            if #available(macOS 26.0, *) {
                GlassEffectPanel()
            } else {
                Text("Liquid Glass 需要 macOS 26+")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct ContentView: View {
    @State private var selection: Panel?

    init(initialPanel: Panel? = nil) {
        _selection = State(initialValue: initialPanel ?? .spring)
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                ForEach(Panel.allCases) { panel in
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(panel.title).font(.headline)
                            Text(panel.subtitle)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: panel.symbol)
                            .foregroundStyle(panel.tint)
                    }
                    .tag(panel)
                }
            }
            .navigationTitle("Fluid Motion")
            .listStyle(.sidebar)
        } detail: {
            if let selection {
                selection.detail
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("选择一个动效", systemImage: "sparkles")
            }
        }
    }
}
