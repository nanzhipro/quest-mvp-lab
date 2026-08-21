import XCTest

@testable import ESProcessMvpCore

final class CliTests: XCTestCase {
    func testConfigPathPositional() throws {
        let cli = try Cli.parse(["/tmp/policy.yaml"])
        XCTAssertEqual(cli.configPath, "/tmp/policy.yaml")
    }

    func testMissingConfigPathFails() {
        XCTAssertThrowsError(try Cli.parse([]))
    }
}
