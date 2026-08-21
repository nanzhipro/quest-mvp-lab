// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "esmvp-swift",
    platforms: [.macOS(.v13)], // inversion API 需要 13.0+，与 objc/rs 版一致
    products: [
        .executable(name: "esmvp-swift", targets: ["esmvp-swift"]),
        .library(name: "ESMvpCore", targets: ["ESMvpCore"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0"),
    ],
    targets: [
        .target(
            name: "ESMvpCore",
            dependencies: [.product(name: "ArgumentParser", package: "swift-argument-parser")],
            // EndpointSecurity 以动态库（非 framework）形式存在于 SDK usr/lib
            linkerSettings: [.linkedLibrary("EndpointSecurity")]
        ),
        .executableTarget(name: "esmvp-swift", dependencies: ["ESMvpCore"]),
        .testTarget(name: "ESMvpCoreTests", dependencies: ["ESMvpCore"]),
    ],
    // v5 语言模式：ES 回调为 C 线程模型，严格并发检查对本工具是净负担
    swiftLanguageModes: [.v5]
)
