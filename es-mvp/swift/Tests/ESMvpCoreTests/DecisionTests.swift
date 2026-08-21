import XCTest

@testable import ESMvpCore

final class DecisionTests: XCTestCase {
    private let REG = UInt32(S_IFREG) | 0o644 // 普通文件
    private let DIR = UInt32(S_IFDIR) | 0o755 // 目录

    func testDeniesPngAndJpeg() {
        for path in ["/w/a.png", "/w/b.jpg", "/w/c.jpeg"] {
            let decision = DecisionEngine.decide(path: path, stMode: REG)
            XCTAssertTrue(decision.isDeny, "\(path) 应被拒绝")
            XCTAssertNotNil(decision.deniedMime)
        }
    }

    func testExtensionMatchingIsCaseInsensitive() {
        for path in ["/w/A.PNG", "/w/B.JPG", "/w/C.JpeG"] {
            XCTAssertTrue(DecisionEngine.decide(path: path, stMode: REG).isDeny, "\(path) 大小写不应逃逸")
        }
    }

    func testAllowsOtherTypesAndExtensionless() {
        for path in ["/w/a.txt", "/w/b.pdf", "/w/Makefile", "/w/.gitignore", "/w/data.bin"] {
            XCTAssertFalse(DecisionEngine.decide(path: path, stMode: REG).isDeny, "\(path) 应放行")
        }
    }

    func testAllowsNonRegularFilesEvenWithImageSuffix() {
        // 名为 x.png 的目录不是拦截对象
        XCTAssertFalse(DecisionEngine.decide(path: "/w/x.png", stMode: DIR).isDeny)
    }

    func testResponseFlagsSemantics() {
        let fflag: UInt32 = 0x4
        XCTAssertEqual(DecisionEngine.decide(path: "/w/a.txt", stMode: REG).responseFlags(fflag: fflag), fflag)
        XCTAssertEqual(DecisionEngine.decide(path: "/w/a.png", stMode: REG).responseFlags(fflag: fflag), 0)
    }

    func testDenyIsNeverCached() {
        let deny = DecisionEngine.decide(path: "/w/a.png", stMode: REG)
        XCTAssertFalse(deny.cacheable(cacheAllow: true))
        let allow = DecisionEngine.decide(path: "/w/a.txt", stMode: REG)
        XCTAssertTrue(allow.cacheable(cacheAllow: true))
        XCTAssertFalse(allow.cacheable(cacheAllow: false))
    }
}
