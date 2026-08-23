import SwiftUI
import CoreGraphics

/// Metal 液体折射着色器（DESIGN.md「天花板层」· layerEffect）。
///
/// 为什么走「预编译 metallib + ShaderLibrary(url:)」而非运行时 makeLibrary：
/// SwiftUI 的 `Shader` / `ShaderFunction` 只接受 `ShaderLibrary`（预编译库），
/// 不直接接受运行时 `MTLLibrary`。故将 .metal 预编译为 .metallib 并作为资源打包，
/// 用 `ShaderLibrary(url:)` 加载，再经 `@dynamicMemberLookup` 取到 stitchable 函数。
enum MetalShaders {

    /// 预编译着色器库（惰性加载一次，缓存复用，避免每帧重载）。
    static let library: ShaderLibrary? = {
        guard let url = Bundle.module.url(forResource: "LiquidRefraction",
                                          withExtension: "metallib") else {
            return nil
        }
        return ShaderLibrary(url: url)
    }()

    /// 构造折射 Shader。`origin` 为点击位置（视图坐标），`time` 为已流逝秒数。
    /// `liquidRefraction` 动态成员返回 ShaderFunction，再经 dynamicCall 绑定参数返回 Shader。
    static func refractionShader(origin: CGPoint, time: Float) -> Shader? {
        guard let library else { return nil }
        return library.liquidRefraction(.float2(origin), .float(time))
    }
}
