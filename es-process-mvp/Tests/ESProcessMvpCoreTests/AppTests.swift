import XCTest

@testable import ESProcessMvpCore

final class AppTests: XCTestCase {
    private let WECHAT = "com.tencent.xinWeChat"
    private let WECHAT_PATH = "/Applications/WeChat.app/Contents/MacOS/WeChat"

    private func config(bundleIds: Set<String> = ["com.tencent.xinWeChat"]) -> AppConfig {
        AppConfig(configPath: "/tmp/policy.yaml", policy: Policy(controlledBundleIds: bundleIds))
    }

    func testSetupSequenceMatchesAppleConstraints() throws {
        let backend = MockEs()
        try App.setup(config: config(), backend: backend, stats: Stats()).get()
        XCTAssertEqual(
            backend.calls,
            [
                "new_monitor_client",
                "invert_muting",
                "ensure_inverted",
                "new_discovery_client",
                "subscribe_exec",
                "subscribe_open",
            ]
        )
    }

    func testSetupFailsWhenInversionNotAccepted() {
        let backend = MockEs.inversionRejected()
        let result = App.setup(config: config(), backend: backend, stats: Stats())
        guard case .failure(.notInverted) = result else {
            return XCTFail("inversion 被拒绝时 setup 应返回 notInverted，实际：\(result)")
        }
        XCTAssertFalse(backend.calls.contains("new_discovery_client"))
    }

    func testMonitorNewClientFailureMapsToHint() {
        let backend = MockEs.failingMonitorClient(4)
        let result = App.setup(config: config(), backend: backend, stats: Stats())
        guard case .failure(let error) = result else { return XCTFail("应失败") }
        XCTAssertTrue("\(error)".contains("完全磁盘访问"), "\(error)")
    }

    func testDiscoveryNewClientFailurePropagates() {
        let backend = MockEs.failingDiscoveryClient(5)
        let result = App.setup(config: config(), backend: backend, stats: Stats())
        guard case .failure(let error) = result else { return XCTFail("应失败") }
        XCTAssertTrue("\(error)".contains("root"), "\(error)")
    }

    // MARK: - AUTH_EXEC（发现客户端）

    func testExecControlledWatchesAndAllows() throws {
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireExec(targetPath: WECHAT_PATH, bundleId: WECHAT)

        // exec 永远 ALLOW；cache=false（每次 launch 都要重新 mute 新 pid）
        XCTAssertEqual(backend.execResponds.map(\.deny), [false])
        XCTAssertEqual(backend.execResponds.map(\.cache), [false])
        XCTAssertTrue(backend.calls.contains("watch_process"))
        let s = stats.snapshot()
        XCTAssertEqual(s.controlled, 1)
        XCTAssertEqual(s.ignored, 0)
    }

    func testExecNonControlledFastPathAllowCached() throws {
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireExec(targetPath: "/usr/bin/ls", bundleId: "com.apple.ls")

        XCTAssertEqual(backend.execResponds.map(\.deny), [false])
        XCTAssertEqual(backend.execResponds.map(\.cache), [true])  // 非管控：写内核缓存减载
        XCTAssertFalse(backend.calls.contains("watch_process"))
        let s = stats.snapshot()
        XCTAssertEqual(s.ignored, 1)
        XCTAssertEqual(s.controlled, 0)
    }

    func testExecEmptyBundleIdNotControlled() throws {
        // 未签名进程 signing_id 为空，不得命中策略
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireExec(targetPath: "/tmp/unsigned-tool", bundleId: "")

        XCTAssertFalse(backend.calls.contains("watch_process"))
        XCTAssertEqual(stats.snapshot().ignored, 1)
    }

    // MARK: - AUTH_OPEN（监控客户端）

    func testOpenControlledPDFDeny() throws {
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireOpen(path: "/tmp/report.pdf", bundleId: WECHAT, fflag: 0x4)

        XCTAssertEqual(backend.openResponds.map(\.flags), [0])       // DENY = flags 0
        XCTAssertEqual(backend.openResponds.map(\.cache), [false])   // DENY 永不缓存
        let s = stats.snapshot()
        XCTAssertEqual(s.openReceived, 1)
        XCTAssertEqual(s.denied, 1)
    }

    func testOpenControlledPDFCaseInsensitive() throws {
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireOpen(path: "/tmp/REPORT.PDF", bundleId: WECHAT, fflag: 0x4)

        XCTAssertEqual(backend.openResponds.map(\.flags), [0])
        XCTAssertEqual(stats.snapshot().denied, 1)
    }

    func testOpenControlledNonPDFAllow() throws {
        // 管控进程打开非 PDF（如 .txt / .sqlite）→ ALLOW，且写内核缓存减载
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireOpen(path: "/tmp/chat.db", bundleId: WECHAT, fflag: 0x4)

        XCTAssertEqual(backend.openResponds.map(\.flags), [UInt32.max])  // ALLOW = UINT32_MAX（授权全部 flags）
        XCTAssertEqual(backend.openResponds.map(\.cache), [false])       // 不缓存 AUTH_OPEN（避免共享缓存 footgun）
        let s = stats.snapshot()
        XCTAssertEqual(s.denied, 0)
        XCTAssertEqual(s.allowed, 1)
    }

    func testOpenNonControlledPDFAllow() throws {
        // 未管控进程打开 PDF → 默认 ALLOW
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireOpen(path: "/tmp/report.pdf", bundleId: "com.other.app", fflag: 0x4)

        XCTAssertEqual(backend.openResponds.map(\.flags), [UInt32.max])
        XCTAssertEqual(stats.snapshot().allowed, 1)
        XCTAssertEqual(stats.snapshot().denied, 0)
    }

    func testEndToEndControlledFlow() throws {
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireExec(targetPath: WECHAT_PATH, bundleId: WECHAT)
        backend.fireOpen(path: "/tmp/report.pdf", bundleId: WECHAT, fflag: 0x4)
        backend.fireOpen(path: "/tmp/chat.db", bundleId: WECHAT, fflag: 0x4)

        let s = stats.snapshot()
        XCTAssertEqual(s.execReceived, 1)
        XCTAssertEqual(s.controlled, 1)
        XCTAssertEqual(s.openReceived, 2)
        XCTAssertEqual(s.denied, 1)   // 仅 PDF 被拒
        XCTAssertEqual(s.allowed, 1)  // 非 PDF 放行
    }

    func testNonListedBundleIdUntouchedEndToEnd() throws {
        // 不在 YAML 里的进程：exec 不 watch、open 不投递，天然 ALLOW
        let backend = MockEs()
        let stats = Stats()
        try App.setup(config: config(), backend: backend, stats: stats).get()

        backend.fireExec(targetPath: "/Applications/Safari.app/Contents/MacOS/Safari", bundleId: "com.apple.Safari")

        XCTAssertFalse(backend.calls.contains("watch_process"))
        XCTAssertEqual(stats.snapshot().ignored, 1)
        XCTAssertEqual(stats.snapshot().controlled, 0)
    }
}
