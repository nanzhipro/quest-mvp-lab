// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "es-procattr-mvp",
    platforms: [.macOS(.v13)], // es_process_t parent/responsible audit token 需消息版本 >= 4（13.0 起默认满足）
    products: [
        .executable(name: "es-procattr", targets: ["es-procattr"]),
        .library(name: "ESProcattrCore", targets: ["ESProcattrCore"]),
    ],
    targets: [
        .target(
            name: "ESProcattrCore",
            // 零第三方依赖（仅 Foundation）：EndpointSecurity 以动态库形式在 SDK usr/lib，
            // libproc 提供进程枚举。这样 MVP 可离线构建，不依赖网络拉取 SwiftPM 依赖。
            linkerSettings: [.linkedLibrary("EndpointSecurity"), .linkedLibrary("proc")]
        ),
        .executableTarget(name: "es-procattr", dependencies: ["ESProcattrCore"]),
        .testTarget(name: "ESProcattrCoreTests", dependencies: ["ESProcattrCore"]),
    ],
    // v5 语言模式：ES 回调为 C 线程模型，严格并发检查对本工具是净负担
    swiftLanguageModes: [.v5]
)
