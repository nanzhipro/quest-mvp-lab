import XCTest

@testable import ESMvpCore

final class StatsTests: XCTestCase {
    func testCountsAndSnapshots() {
        let stats = Stats()
        stats.recordReceived()
        stats.recordReceived()
        stats.recordVerdict(deny: false)
        stats.recordVerdict(deny: true)
        stats.recordRespondError()
        XCTAssertEqual(
            stats.snapshot(),
            StatsSnapshot(received: 2, allowed: 1, denied: 1, respondError: 1)
        )
    }

    func testSnapshotRendersKeyValueLine() {
        XCTAssertEqual(
            Stats().snapshot().description,
            "received=0 allowed=0 denied=0 respond_error=0"
        )
    }
}
