import XCTest
@testable import ESProcattrCore

final class ConfigTests: XCTestCase {
    private func write(_ json: String) throws -> String {
        let path = NSTemporaryDirectory() + "procattr-test-\(UUID().uuidString).json"
        try json.write(toFile: path, atomically: true, encoding: .utf8)
        return path
    }
    private func config(_ path: String, output: String? = nil) throws -> AppConfig {
        try AppConfig.from(cli: Cli(configPath: path, output: output))
    }

    func testParsePathContainsRoot() throws {
        let path = try write(#"{"roots":[{"pathContains":"hermes-agent"}]}"#)
        let config = try config(path)
        XCTAssertEqual(config.matcher.rules.count, 1)
        XCTAssertEqual(config.matcher.rules[0].pathContains, "hermes-agent")

        var g = ProcGene()
        g.path = "/Users/x/.hermes/hermes-agent/venv/bin/python3"
        XCTAssertTrue(config.matcher.matches(g))
        g.path = "/bin/ls"
        XCTAssertFalse(config.matcher.matches(g))
    }

    func testParseSigningIdRoot() throws {
        let path = try write(#"{"roots":[{"signingId":"com.example.tool"}]}"#)
        let config = try config(path)
        var g = ProcGene()
        g.signingId = "com.example.tool"
        XCTAssertTrue(config.matcher.matches(g))
        g.signingId = "com.other"
        XCTAssertFalse(config.matcher.matches(g))
    }

    func testParseReportDefaults() throws {
        let path = try write(#"{"roots":[{"pathContains":"x"}]}"#)
        let config = try config(path)
        XCTAssertEqual(config.report.snapshotSeconds, 5)
        XCTAssertFalse(config.report.logOpen)
        XCTAssertFalse(config.report.logLibraryOpen)
    }

    func testParseReportOverrides() throws {
        let path = try write(#"{"roots":[{"pathContains":"x"}],"report":{"snapshotSeconds":3,"logOpen":true,"logLibraryOpen":true}}"#)
        let config = try config(path)
        XCTAssertEqual(config.report.snapshotSeconds, 3)
        XCTAssertTrue(config.report.logOpen)
        XCTAssertTrue(config.report.logLibraryOpen)
    }

    func testMissingRootsThrows() throws {
        let path = try write(#"{"report":{"snapshotSeconds":3}}"#)
        XCTAssertThrowsError(try config(path))
    }

    func testEmptyRuleThrows() throws {
        let path = try write(#"{"roots":[{}]}"#)
        XCTAssertThrowsError(try config(path))
    }

    func testLibraryPathHeuristic() {
        XCTAssertTrue(App.isLibraryPath("/usr/lib/libc.dylib"))
        XCTAssertTrue(App.isLibraryPath("/System/Library/Frameworks/AppKit.framework/Versions/C/AppKit"))
        XCTAssertTrue(App.isLibraryPath("/foo/bar.dylib"))
        XCTAssertFalse(App.isLibraryPath("/Users/x/notes.txt"))
        XCTAssertFalse(App.isLibraryPath("/tmp/script.sh"))
    }
}

final class CliTests: XCTestCase {
    func testParsePositionalAndOutput() throws {
        let cli = try Cli.parse(["es-procattr", "config.json", "--output", "out.jsonl"])
        XCTAssertEqual(cli.configPath, "config.json")
        XCTAssertEqual(cli.output, "out.jsonl")
    }

    func testParseOutputEquals() throws {
        let cli = try Cli.parse(["es-procattr", "config.json", "--output=out.jsonl"])
        XCTAssertEqual(cli.output, "out.jsonl")
    }

    func testMissingConfigThrows() {
        XCTAssertThrowsError(try Cli.parse(["es-procattr"]))
    }

    func testUnknownFlagThrows() {
        XCTAssertThrowsError(try Cli.parse(["es-procattr", "config.json", "--nope"]))
    }
}
