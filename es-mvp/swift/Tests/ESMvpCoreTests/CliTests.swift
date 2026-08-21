import XCTest

@testable import ESMvpCore

final class CliTests: XCTestCase {
    func testDefaults() throws {
        let cli = try Cli.parse([])
        XCTAssertTrue(cli.watch.isEmpty)
        XCTAssertFalse(cli.cache)
        XCTAssertFalse(cli.verbose)
        XCTAssertEqual(cli.statsInterval, 10)
    }

    func testRepeatedWatchAndFlags() throws {
        let cli = try Cli.parse([
            "--watch", "/a", "--watch", "/b", "--cache", "--verbose", "--stats-interval", "3",
        ])
        XCTAssertEqual(cli.watch, ["/a", "/b"])
        XCTAssertTrue(cli.cache)
        XCTAssertTrue(cli.verbose)
        XCTAssertEqual(cli.statsInterval, 3)
    }

    func testUnknownArgIsRejected() {
        XCTAssertThrowsError(try Cli.parse(["--bogus"]))
    }
}
