# SPEC — fluid-motion-demo 可复现规格

> 本文件是**可复现规格**：任何 AI 代理拿到本文件即可从零重建完整 Demo，含全部踩坑记录与验收标准。
> 上游：调研报告 + DESIGN.md（灵动动效设计语言）。

## 1. 目标

落地 DESIGN.md 的五个核心动效配方 + 质感层，产出可运行的 macOS SwiftUI 动效 Demo，证明「物理正确 × 空间连续 × 内容插画 × 多感官同步 × 质感材质」的行业标杆方案可落地。

六个面板：弹簧按钮 / Hero 转场 / MeshGradient 活背景 / Metal 液体折射 / 触觉同步 / Liquid Glass 质感。

## 2. 技术栈

| 项 | 值 |
| --- | --- |
| 语言 / 工具链 | Swift 6.3（Swift 5 语言模式）、SwiftPM（tools-version 6.0） |
| UI | SwiftUI（macOS） |
| 渲染 | Metal（预编译 metallib）+ SwiftUI `layerEffect` |
| 触觉 | AppKit `NSHapticFeedbackManager` |
| 最低系统 | macOS 15（MeshGradient）；Liquid Glass 面板需 macOS 26+ |
| 架构 | arm64（metallib 预编译为 arm64） |

## 3. 项目结构

```
Package.swift                     # 单一 executableTarget + testTarget，swiftLanguageModes: [.v5]
shaders/LiquidRefraction.metal    # 液体折射 MSL（内联 SwiftUI::Layer）
scripts/compile_shaders.sh        # xcrun metal -c → metallib
Sources/FluidMotionDemo/*.swift   # 全部源码（同模块，internal）
  MotionTokens.swift              # token 常量（单一事实来源）
  SpringAliases.swift             # Animation 弹簧别名
  Haptics.swift                   # NSHapticFeedbackManager
  MetalShaders.swift              # ShaderLibrary(url:) + refractionShader
  FluidMotionDemoApp.swift        # @main + NSApplicationDelegateAdaptor 前台激活
  ContentView.swift               # NavigationSplitView + List(selection:) + .tag
  六个 *Panel.swift               # 六个动效面板
  LiquidRefraction.metallib       # 预编译着色器（提交入库，作为资源打包）
Tests/FluidMotionDemoTests/       # @testable import FluidMotionDemo
```

## 4. 构建 / 运行 / 测试

```bash
swift build            # 构建（metallib 作为资源自动打包）
swift run              # 启动 GUI
swift test             # 3 项测试
scripts/compile_shaders.sh   # 仅修改 .metal 后重跑
```

## 5. 五个配方（代码要点）

1. **弹簧按钮**：`ButtonStyle` 捕获 `configuration.isPressed`，`.scaleEffect(pressed ? 0.94 : 1)` + `.animation(.spring(...), value:)` + `.onChange { _, pressed in if pressed { Haptics.impact(...) } }`（触觉与视觉同帧）。
2. **Hero 转场**：`@Namespace` + 源/目标 `matchedGeometryEffect(id:in:)` 同 id，`withAnimation(.fluidSpring)` 包裹 `selected` 切换。
3. **MeshGradient**：`MeshGradient(width:4,height:4,points:colors:)`，`withAnimation(.easeInOut(duration:4).repeatForever(autoreverses:true))` 动画 `points`（顶点漂移，角点不动）。
4. **Metal 液体折射**：`.metal` 里 `[[ stitchable ]] half4 liquidRefraction(float2 position, SwiftUI::Layer layer, float2 origin, float time)`，`layer.sample(position + offset)` 做折射；Swift 侧 `ShaderLibrary(url:)` + `library.liquidRefraction(.float2(origin), .float(time))`，`TimelineView(.animation)` 驱动 time，`SpatialTapGesture` 更新 origin。
5. **触觉同步**：`NSHapticFeedbackManager.defaultPerformer.perform(.alignment/.generic/.levelChange, performanceTime: .now)`。
6. **Liquid Glass**：`.glassEffect(.regular, in: RoundedRectangle(cornerRadius:))`，`#available(macOS 26.0, *)` 守卫。

## 6. 关键踩坑记录（重建时必读）

1. **SwiftUI Shader 只接受预编译 ShaderLibrary**：`Shader` / `ShaderFunction.init(library:name:)` 的参数类型是 `ShaderLibrary`（预编译 `.metallib`），**不接受运行时 `MTLLibrary`**。所以不能 `device.makeLibrary(source:)` 直接喂给 `Shader`。正确做法：`xcrun metal` 预编译 `.metallib` → 作为 SwiftPM 资源打包 → `ShaderLibrary(url: Bundle.module.url(...))` 加载 → `@dynamicMemberLookup` 取函数 → `Shader(function:arguments:)`。
2. **`Shader.Argument.float2` 接受 `CGPoint`（不是 `SIMD2<Float>`）**：`float2(_ point: CGPoint)`、`float2(_ size: CGSize)`、`float2(_ vector: CGVector)`、`float2<T>(_ x: T, _ y: T) where T: BinaryFloatingPoint`。公开 API 里直接用 `CGPoint` 最稳。
3. **`SwiftUI::Layer` 需内联**：系统头在 `SwiftUI.framework/Versions/A/Headers/SwiftUI_Metal.h`（33 行自包含 struct），内联进 .metal 可避免 SwiftPM 编译 .metal 时缺 include 路径。
4. **macOS 上 SwiftUI `SensoryFeedback` 是 no-op**：官方文档明确「Only plays feedback on iOS and watchOS」。macOS 触觉必须走 `NSHapticFeedbackManager`（Force Touch 触控板）。
5. **SwiftPM library 目标的 public API 触发模块 emit 失败**：多文件 public 类型（含 `public extension Animation`）导致 `-emit-objc-header` + ABI descriptor 的 emit-module 静默失败（SwiftPM 只报 `error: emit-module command failed`，不显示具体错误）。**解法：单一 executableTarget，全 internal**，测试用 `@testable import` 依赖可执行目标。
6. **`List(data, selection:)` 的 selection 类型是 `Element.ID`**（不是 Element 本身），与 `.tag()` 混用会静默失效。**解法：`List(selection:)` + `ForEach` + `.tag(panel)`**，selection 类型用 `Panel?`。
7. **`onChange(of:perform:)` 在 macOS 14 已废弃**：用双参数闭包 `{ _, newValue in }`。
8. **SPM 可执行目标无 app bundle**：需 `NSApplicationDelegateAdaptor` + `NSApp.setActivationPolicy(.regular)` + `NSApp.activate(ignoringOtherApps: true)` 才能前台显示窗口。
9. **metallib 是架构相关的**：预编译为 arm64；x86_64 需重跑 `compile_shaders.sh`。

## 7. 验收标准

- [ ] `swift build` 零错误零警告
- [ ] `swift test` 3/3 通过（含 `testMetalRefractionShaderLoads`）
- [ ] `swift run` 启动后：默认显示弹簧按钮面板（snappy/fluid/playful 三按钮）
- [ ] 侧栏六面板可切换：Hero 卡片、MeshGradient 全屏彩色渐变、液体折射、触觉、Liquid Glass
- [ ] MeshGradient 面板像素采样彩色占比 > 80%（实测 86%）
- [ ] 着色器加载成功（`refractionShader != nil`）
- [ ] 无企业数据资产（无签名证书/密钥/品牌字样）
