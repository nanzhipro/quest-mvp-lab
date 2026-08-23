import SwiftUI
import AppKit

/// SPM 可执行目标缺省无 app bundle，需手动提升为前台 app 并激活窗口。
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct FluidMotionDemoApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    /// 支持 `--panel <spring|hero|mesh|liquid|haptics|glass>` 启动参数，
    /// 用于确定性截图（CI / 文档生成）直接定位到指定面板。
    private var initialPanel: Panel? {
        let args = CommandLine.arguments
        guard let idx = args.firstIndex(of: "--panel"), idx + 1 < args.count else { return nil }
        return Panel(rawValue: args[idx + 1])
    }

    var body: some Scene {
        WindowGroup {
            ContentView(initialPanel: initialPanel)
        }
        .defaultSize(width: 980, height: 620)
    }
}
