// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "es-process-mvp",
    platforms: [.macOS(.v13)], // es_invert_muting / 进程反转需 13.0+
    products: [
        .executable(name: "es-process-mvp", targets: ["es-process-mvp"]),
        .library(name: "ESProcessMvpCore", targets: ["ESProcessMvpCore"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0"),
        .package(url: "https://github.com/jpsim/Yams.git", from: "5.0.0"),
    ],
    targets: [
        .target(
            name: "ESProcessMvpCore",
            dependencies: [
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
                .product(name: "Yams", package: "Yams"),
            ],
            // EndpointSecurity 以动态库（非 framework）形式存在于 SDK usr/lib
            linkerSettings: [.linkedLibrary("EndpointSecurity")]
        ),
        .executableTarget(name: "es-process-mvp", dependencies: ["ESProcessMvpCore"]),
        .testTarget(name: "ESProcessMvpCoreTests", dependencies: ["ESProcessMvpCore"]),
    ],
    // v5 语言模式：ES 回调为 C 线程模型，严格并发检查对本工具是净负担
    swiftLanguageModes: [.v5]
)
