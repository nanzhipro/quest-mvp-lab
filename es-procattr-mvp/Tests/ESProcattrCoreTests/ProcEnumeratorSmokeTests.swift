import XCTest
@testable import ESProcattrCore

/// ProcEnumerator 冒烟测试：验证 libproc/mach/KERN_PROCARGS2 枚举在当前机器可用。
/// 这是 e2e 种子阶段的前置条件（不依赖 root，枚举本用户进程）。
final class ProcEnumeratorSmokeTests: XCTestCase {
    func testEnumeratePidsAndSelfMetadata() {
        let pids = ProcEnumerator.allPids()
        XCTAssertGreaterThan(pids.count, 10, "应能枚举到系统进程")

        let selfPid = getpid()
        XCTAssertNotNil(ProcEnumerator.path(of: selfPid), "应能取到自身可执行路径")
        XCTAssertNotNil(ProcEnumerator.ppid(of: selfPid), "应能取到自身 ppid")
        XCTAssertNotNil(ProcEnumerator.auditToken(of: selfPid), "应能取到自身 audit token（含 pidversion）")

        let args = ProcEnumerator.arguments(of: selfPid)
        XCTAssertFalse(args.isEmpty, "应能取到自身 argv（KERN_PROCARGS2）")
    }
}
