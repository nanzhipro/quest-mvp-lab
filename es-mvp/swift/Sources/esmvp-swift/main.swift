import Foundation

import ESMvpCore

let cli = Cli.parseOrExit()

do {
    let config = try AppConfig.from(cli: cli)
    if case .failure(let error) = App.run(config: config) {
        Log.error("启动失败", ["error": "\(error)"])
        exit(1)
    }
} catch {
    Log.error("配置无效", ["error": "\(error)"])
    exit(2)
}
