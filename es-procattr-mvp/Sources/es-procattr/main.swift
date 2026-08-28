import Foundation

import ESProcattrCore

do {
    let cli = try Cli.parse(CommandLine.arguments)
    let config = try AppConfig.from(cli: cli)
    if case .failure(let error) = App.run(config: config) {
        Log.error("启动失败", ["error": "\(error)"])
        exit(1)
    }
} catch let error as CliError where error.description.isEmpty {
    exit(0) // --help 已打印用法
} catch {
    Log.error("配置无效", ["error": "\(error)"])
    exit(2)
}
