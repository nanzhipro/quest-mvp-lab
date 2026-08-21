import XCTest

@testable import ESProcessMvpCore

final class ConfigTests: XCTestCase {
    private func write(_ content: String) -> String {
        let path = NSTemporaryDirectory() + "esprocmvp-policy-\(UUID().uuidString).yaml"
        try! content.write(toFile: path, atomically: true, encoding: .utf8)
        return path
    }

    func testLoadValidYaml() throws {
        let path = write("""
        bundleIds:
          - com.tencent.xinWeChat
          - com.example.foo
        """)
        let cfg = try PolicyConfig.load(path: path)
        XCTAssertEqual(cfg.policy.controlledBundleIds, ["com.tencent.xinWeChat", "com.example.foo"])
    }

    func testLoadTrimsWhitespaceAndDropsEmpty() throws {
        let path = write("""
        bundleIds:
          - "  com.tencent.xinWeChat  "
          - ""
          - com.example.foo
        """)
        let cfg = try PolicyConfig.load(path: path)
        XCTAssertEqual(cfg.policy.controlledBundleIds, ["com.tencent.xinWeChat", "com.example.foo"])
    }

    func testLoadMissingFile() {
        XCTAssertThrowsError(try PolicyConfig.load(path: "/nonexistent-policy-xyz.yaml")) { error in
            guard case ConfigError.unreadable = error else { return XCTFail("应为 unreadable，实际 \(error)") }
        }
    }

    func testLoadInvalidYaml() {
        let path = write("bundleIds: [unclosed")
        XCTAssertThrowsError(try PolicyConfig.load(path: path)) { error in
            guard case ConfigError.invalidYaml = error else { return XCTFail("应为 invalidYaml，实际 \(error)") }
        }
    }

    func testLoadNonMappingRoot() {
        let path = write("- just\n- a\n- list")
        XCTAssertThrowsError(try PolicyConfig.load(path: path)) { error in
            guard case ConfigError.invalidStructure = error else { return XCTFail("应为 invalidStructure，实际 \(error)") }
        }
    }

    func testLoadMissingBundleIdsKey() {
        let path = write("other: value")
        XCTAssertThrowsError(try PolicyConfig.load(path: path)) { error in
            guard case ConfigError.invalidStructure = error else { return XCTFail("应为 invalidStructure，实际 \(error)") }
        }
    }

    func testLoadBundleIdsNotList() {
        let path = write("bundleIds: not-a-list")
        XCTAssertThrowsError(try PolicyConfig.load(path: path)) { error in
            guard case ConfigError.invalidStructure = error else { return XCTFail("应为 invalidStructure，实际 \(error)") }
        }
    }

    func testLoadEmptyBundleIds() throws {
        let path = write("bundleIds: []")
        let cfg = try PolicyConfig.load(path: path)
        XCTAssertTrue(cfg.policy.controlledBundleIds.isEmpty)
    }
}
