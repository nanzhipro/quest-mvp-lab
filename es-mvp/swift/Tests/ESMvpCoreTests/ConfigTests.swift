import XCTest

@testable import ESMvpCore

final class ConfigTests: XCTestCase {
    private var tempDirs: [URL] = []

    override func tearDown() {
        for dir in tempDirs {
            try? FileManager.default.removeItem(at: dir)
        }
        tempDirs = []
        super.tearDown()
    }

    private func makeTempDir() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("esmvp-swift-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        tempDirs.append(url)
        return url
    }

    func testNormalizesAndAppendsTrailingSlash() throws {
        let dir = try makeTempDir()
        let rule = try normalizeWatchDir(dir.path)
        XCTAssertTrue(rule.hasSuffix("/"))
        XCTAssertTrue(rule.hasPrefix("/"))
    }

    func testResolvesSymlinkedWatchDir() throws {
        // macOS 上 /tmp → /private/tmp：经符号链接传入的规则必须按真实路径静音
        let base = try makeTempDir()
        let real = base.appendingPathComponent("real")
        try FileManager.default.createDirectory(at: real, withIntermediateDirectories: false)
        let link = base.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: real)

        let viaLink = try normalizeWatchDir(link.path)
        let viaReal = try normalizeWatchDir(real.path)
        XCTAssertEqual(viaLink, viaReal)
        XCTAssertTrue(viaLink.hasSuffix("/real/"), viaLink)
    }

    func testRejectsNonexistentAndFile() throws {
        XCTAssertThrowsError(try normalizeWatchDir("/definitely/not/exist/esmvp"))
        let file = try makeTempDir().appendingPathComponent("f.txt")
        try "x".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertThrowsError(try normalizeWatchDir(file.path))
    }

    func testExpandsHomeTilde() throws {
        let rule = try normalizeWatchDir("~/")
        XCTAssertEqual(rule, NSHomeDirectory() + "/")
    }

    func testModeReporting() throws {
        let muteAll = try AppConfig.from(cli: Cli.parse([]))
        XCTAssertEqual(muteAll.mode, .muteAll)

        let watched = try AppConfig.from(cli: Cli.parse(["--watch", "/tmp"]))
        guard case .watchOnly(let dirs) = watched.mode else {
            return XCTFail("应为 watch-only")
        }
        XCTAssertEqual(dirs, ["/private/tmp/"])
    }
}
