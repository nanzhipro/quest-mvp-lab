import XCTest
@testable import ESProcattrCore

final class TokenTests: XCTestCase {
    private func token(_ pid: UInt32, _ ver: UInt32) -> audit_token_t {
        var t = audit_token_t()
        t.val.5 = pid
        t.val.7 = ver
        return t
    }

    func testPidAndPidversion() {
        let tok = Token(token(4242, 3))
        XCTAssertEqual(tok.pid, 4242)
        XCTAssertEqual(tok.pidversion, 3)
    }

    func testZeroDefaults() {
        let tok = Token(audit_token_t())
        XCTAssertEqual(tok.pid, 0)
        XCTAssertEqual(tok.pidversion, 0)
    }
}

final class PidTests: XCTestCase {
    func testComparableAndHashable() {
        let a = Pid(pid: 10, pidversion: 1)
        let b = Pid(pid: 10, pidversion: 2)
        let c = Pid(pid: 11, pidversion: 0)
        XCTAssertLessThan(a, b)   // 同 pid 比 pidversion
        XCTAssertLessThan(a, c)   // 比 pid
        XCTAssertEqual(Set([a, b, c]).count, 3)
    }
}
