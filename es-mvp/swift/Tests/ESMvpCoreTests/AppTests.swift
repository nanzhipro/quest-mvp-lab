import XCTest

@testable import ESMvpCore

final class AppTests: XCTestCase {
    private let REG = UInt32(S_IFREG) | 0o644

    private func testConfig(watch: [String] = [], cache: Bool = false) -> AppConfig {
        AppConfig(
            watchRules: watch.map { "\($0)/" },
            cacheAllow: cache,
            verbose: true,
            statsInterval: 10
        )
    }

    func testSetupSequenceMatchesAppleConstraints() throws {
        let backend = MockEs()
        try App.setup(
            config: testConfig(watch: ["/watched/a", "/watched/b"]),
            backend: backend,
            stats: Stats()
        ).get()
        XCTAssertEqual(
            backend.calls,
            [
                "new_client",
                "default_target_mute_count",
                "unmute_all_target_paths",
                "invert_muting",
                "ensure_inverted",
                "mute_target_prefix:/watched/a/",
                "mute_target_prefix:/watched/b/",
                "subscribe_auth_open",
            ]
        )
    }

    func testMuteAllModeAppliesNoRules() throws {
        let backend = MockEs()
        try App.setup(config: testConfig(), backend: backend, stats: Stats()).get()
        XCTAssertFalse(backend.calls.contains { $0.hasPrefix("mute_target_prefix") })
    }

    func testSetupFailsWhenInversionNotAccepted() {
        let backend = MockEs.inversionRejected()
        let result = App.setup(config: testConfig(watch: ["/watched"]), backend: backend, stats: Stats())
        guard case .failure(.notInverted) = result else {
            return XCTFail("inversion 被拒绝时 setup 应返回 notInverted，实际：\(result)")
        }
        // inversion 未生效时必须终止在 subscribe 之前
        XCTAssertFalse(backend.calls.contains("subscribe_auth_open"))
    }

    func testEventFlowDenyThenAllow() throws {
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: testConfig(watch: ["/watched"], cache: true), backend: backend, stats: stats).get()

        backend.fire(path: "/watched/a.png", stMode: REG, fflag: 0x4)
        backend.fire(path: "/watched/b.txt", stMode: REG, fflag: 0x4)

        // DENY → flags=0 且不缓存；ALLOW → 透传 fflag 且写入内核缓存
        XCTAssertEqual(backend.responds.map(\.flags), [0, 0x4])
        XCTAssertEqual(backend.responds.map(\.cache), [false, true])
        let snapshot = stats.snapshot()
        XCTAssertEqual(snapshot.received, 2)
        XCTAssertEqual(snapshot.allowed, 1)
        XCTAssertEqual(snapshot.denied, 1)
    }

    func testNewClientFailureMapsToHint() {
        let backend = MockEs.failingNewClient(4)
        let result = App.setup(config: testConfig(), backend: backend, stats: Stats())
        guard case .failure(let error) = result else {
            return XCTFail("应失败")
        }
        XCTAssertTrue("\(error)".contains("完全磁盘访问"), "\(error)")
    }
}
