import XCTest
import CoreGraphics
@testable import FluidMotionDemo

final class FluidMotionDemoTests: XCTestCase {

    /// 实测：预编译 metallib 能加载，且 `liquidRefraction` stitchable 函数能取到并构造 Shader。
    /// 这是「天花板层」着色器可落地的最强客观证据——不是「看起来能编译」，而是真正加载成功。
    func testMetalRefractionShaderLoads() {
        XCTAssertNotNil(MetalShaders.refractionShader(origin: CGPoint(x: 0, y: 0), time: 0),
                        "metallib 加载或 liquidRefraction 函数查找失败")
    }

    /// 实测：DESIGN.md 弹簧 token 值必须在物理合法区间内。
    func testSpringTokensArePhysicallySane() {
        let springs = [
            Motion.Spring.snappy,
            Motion.Spring.fluid,
            Motion.Spring.playful,
            Motion.Spring.gentle,
        ]
        for s in springs {
            XCTAssertGreaterThan(s.response, 0, "response 必须为正")
            XCTAssertTrue((0.0...1.0).contains(s.dampingFraction), "dampingFraction 必须在 [0,1]")
        }
    }

    /// 实测：时长 token 单调递增（instant < quick < standard < expressive < hero）。
    func testDurationTokensAreMonotonic() {
        let durations = [
            Motion.Duration.instant,
            Motion.Duration.quick,
            Motion.Duration.standard,
            Motion.Duration.expressive,
            Motion.Duration.hero,
        ]
        for (a, b) in zip(durations, durations.dropFirst()) {
            XCTAssertLessThan(a, b, "时长 token 必须单调递增")
        }
    }
}
