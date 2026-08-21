import XCTest

@testable import ESProcessMvpCore

final class StatsTests: XCTestCase {
    func testCountersAccumulate() {
        let stats = Stats()
        stats.recordExecReceived()
        stats.recordExecReceived()
        stats.recordControlled()
        stats.recordIgnored()
        stats.recordOpenReceived()
        stats.recordVerdict(deny: true)
        stats.recordVerdict(deny: false)
        stats.recordWatchError()
        stats.recordRespondError()

        let s = stats.snapshot()
        XCTAssertEqual(s.execReceived, 2)
        XCTAssertEqual(s.controlled, 1)
        XCTAssertEqual(s.ignored, 1)
        XCTAssertEqual(s.openReceived, 1)
        XCTAssertEqual(s.denied, 1)
        XCTAssertEqual(s.allowed, 1)
        XCTAssertEqual(s.watchError, 1)
        XCTAssertEqual(s.respondError, 1)
        XCTAssertTrue(s.description.contains("exec_received=2"))
        XCTAssertTrue(s.description.contains("controlled=1"))
    }
}
