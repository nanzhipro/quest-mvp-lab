// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "fluid-motion-demo",
    platforms: [.macOS(.v15)],
    targets: [
        // 单一可执行目标：token + 着色器 + 全部面板同模块，规避 library 目标 public API 的模块 emit 问题
        .executableTarget(
            name: "FluidMotionDemo",
            resources: [.copy("LiquidRefraction.metallib")]
        ),
        // 测试目标：依赖可执行目标（@testable import），验证着色器加载 + token 合法
        .testTarget(
            name: "FluidMotionDemoTests",
            dependencies: ["FluidMotionDemo"]
        ),
    ],
    // 显式保持 Swift 5 语言模式，规避 Swift 6 严格并发对 SwiftUI 视图的干扰
    swiftLanguageModes: [.v5]
)
