import XCTest

@testable import ESProcessMvpCore

final class PolicyTests: XCTestCase {
    private let policy = Policy(controlledBundleIds: ["com.tencent.xinWeChat", "com.example.foo"])

    func testIsControlled() {
        XCTAssertTrue(policy.isControlled("com.tencent.xinWeChat"))
        XCTAssertTrue(policy.isControlled("com.example.foo"))
        XCTAssertFalse(policy.isControlled("com.other.bar"))
        XCTAssertFalse(policy.isControlled(""))
    }

    func testIsPDF() {
        XCTAssertTrue(isPDF("/tmp/a.pdf"))
        XCTAssertTrue(isPDF("/tmp/A.PDF"))           // 大小写不敏感
        XCTAssertTrue(isPDF("/tmp/a.Pdf"))
        XCTAssertTrue(isPDF("no-slash.pdf"))
        XCTAssertFalse(isPDF("/tmp/a.txt"))
        XCTAssertFalse(isPDF("/tmp/a.pdf.bak"))      // 不以 .pdf 结尾
        XCTAssertFalse(isPDF("/tmp/pdf"))            // 无扩展名
        XCTAssertFalse(isPDF("/tmp/a.pd"))
    }

    func testDenyOpenOnlyControlledPDF() {
        // 管控 + PDF → DENY
        XCTAssertTrue(policy.denyOpen(bundleId: "com.tencent.xinWeChat", path: "/tmp/a.pdf"))
        XCTAssertTrue(policy.denyOpen(bundleId: "com.example.foo", path: "/tmp/a.PDF"))
        // 管控 + 非 PDF → ALLOW
        XCTAssertFalse(policy.denyOpen(bundleId: "com.tencent.xinWeChat", path: "/tmp/a.txt"))
        // 未管控 + PDF → ALLOW（默认放行）
        XCTAssertFalse(policy.denyOpen(bundleId: "com.other.bar", path: "/tmp/a.pdf"))
        // 空 bundleId（未签名）→ ALLOW
        XCTAssertFalse(policy.denyOpen(bundleId: "", path: "/tmp/a.pdf"))
    }
}
