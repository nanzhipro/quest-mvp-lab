---
version: "1.0"
name: "Fluid Motion"
nameZh: "灵动动效设计语言"
kind: "motion-design-language"
description: "Apple 平台（iOS / macOS / watchOS / visionOS）顶级动效交互的统一设计语言。既是规范（token 精确值），也是指导原则（决策规则 + 代码配方 + 质量门禁）。Agent 据此驱动 AI 创作行业标杆级动效。"
platforms: [iOS, iPadOS, macOS, watchOS, visionOS]
deploymentTarget:
  iOS: "17.0"
  macOS: "14.0"
  watchOS: "10.0"
  visionOS: "1.0"

motion:
  duration:
    instant: "0.10s"
    quick: "0.20s"
    standard: "0.30s"
    expressive: "0.50s"
    hero: "0.70s"

  spring:
    snappy:
      response: "0.25s"
      dampingFraction: 0.85
    fluid:
      response: "0.40s"
      dampingFraction: 0.70
    playful:
      response: "0.55s"
      dampingFraction: 0.50
    gentle:
      response: "0.70s"
      dampingFraction: 0.80

  springBounce:
    subtle:
      duration: "0.50s"
      bounce: 0.15
    playful:
      duration: "0.60s"
      bounce: 0.30

  easing:
    standard: "cubic-bezier(0.4, 0.0, 0.2, 1.0)"
    decelerate: "cubic-bezier(0.0, 0.0, 0.2, 1.0)"
    accelerate: "cubic-bezier(0.4, 0.0, 1.0, 1.0)"
    linear: "linear"

  distance:
    micro: "4pt"
    small: "8pt"
    medium: "16pt"
    large: "48pt"
    offscreen: "100%"

  scale:
    pressed: 0.94
    hovered: 1.02
    emphasis: 1.08
    exit: 0.80

  opacity:
    hidden: 0.0
    scrim: 0.4
    dimmed: 0.6
    visible: 1.0

  haptics:
    selection: "UISelectionFeedbackGenerator"
    impactLight: "UIImpactFeedbackGenerator(.light)"
    impactMedium: "UIImpactFeedbackGenerator(.medium)"
    impactHeavy: "UIImpactFeedbackGenerator(.heavy)"
    success: "UINotificationFeedbackGenerator(.success)"
    rigid: "UIImpactFeedbackGenerator(.rigid)"
    soft: "UIImpactFeedbackGenerator(.soft)"

  material:
    glass: "glassEffect(.regular)"
    regular: "ultraThinMaterial"
    elevated: "regularMaterial"
    solid: "thickMaterial"

  performanceBudget:
    fps: 60
    proMotionFps: 120
    maxSingleDuration: "0.50s"
    maxHeroDuration: "0.80s"
    maxConcurrentAnimations: 5
    hitchRatio: 0.05
---

# Fluid Motion 动效设计语言

> **本文档的双重身份**：既是一份**规范**（token 给出精确值，可被 Agent 解析、校验、落地），也是一份**指导原则**（决策规则 + 代码配方 + 质量门禁，告诉 Agent「何时用、用什么、怎么验证」）。
>
> **设计目标**：驱动 AI 创作出**物理正确、空间连续、内容插画级、多感官同步、克制**的行业标杆动效——不是「能用」，而是「惊艳」。

---

## 1. 设计哲学（The Five Principles）

一切动效决策都从这五条出发。任何一条被违反，动效质量就滑向「廉价」。

1. **物理性（Physicality）**——动效由**手势驱动**而非时间驱动；可**中断、可重定向**；用**弹簧**而非缓动曲线。用户「觉得对」的动效，几乎都是物理驱动的。
2. **连续性（Continuity）**——元素在界面层级间**无缝旅行**（hero / 共享元素），不凭空出现或消失。
3. **内容感（Contentfulness）**——真正的「美」来自**内容在动**（插画、图标、品牌），而非「布局在动」。复杂插画交给 Lottie/Rive，工程师只触发。
4. **同步感（Synchrony）**——视觉、触觉、音效**同帧触发**。触觉是「高级感」的最后一公里，成本极低，收益极高。
5. **克制（Restraint）**——动效要**突出内容，而非抢戏**。动效密度高 = 廉价感。Liquid Glass 官方明确警告：玻璃特效只用于最重要的功能元素。

**一句心法**：惊艳 = 物理正确（弹簧）× 空间连续（hero）× 内容插画（Lottie/Rive）× 多感官同步（触觉/声音）× 质感材质（Liquid Glass）× 克制。

---

## 2. 动效模型（The Motion Model）

### 2.1 五轴架构（分层动效体系）

所有动效归属于五层之一，各层有明确的技术载体与预算：

| 层 | 职责 | 技术载体 | 使用原则 |
|---|---|---|---|
| **结构层** Structure | 状态反馈、导航、空间关系 | SwiftUI 弹簧 / `matchedGeometryEffect` | 永远原生，免费获得可访问性适配 |
| **质感层** Quality | 材质、玻璃、深度 | `glassEffect`（iOS 26+）/ material 降级 | 主线质感，突出内容 |
| **天花板层** Ceiling | 折射、水波、光斑、活渐变 | Metal 着色器 / `MeshGradient` | 招牌「wow」时刻，控制数量 |
| **内容层** Content | 插画、吉祥物、品牌动效 | Lottie（线性）/ Rive（交互） | 设计师产出，工程师触发 |
| **点睛层** Accent | 触觉、音效同步 | `sensoryFeedback` | 每个确认点都应有 |

### 2.2 动效优先级（Motion Hierarchy）

不是所有动效都同等重要。按优先级分配精力：

| 优先级 | 类别 | 作用 | 是否必须 |
|---|---|---|---|
| L1 | **功能反馈**（状态变化、按下、开关） | 让用户知道发生了什么 | 必须 |
| L2 | **空间连续**（导航、转场、hero） | 让用户知道「在哪」 | 通常必须 |
| L3 | **愉悦点缀**（品牌、惊喜、wow） | 让用户「感到好」 | 克制使用 |

> **铁律**：L1 功能动效缺失是 bug；L3 点缀动效过度是品味问题。先确保 L1/L2 完美，再谈 L3。

---

## 3. Token 语义（Token Semantics）

### 3.1 duration（时长）

| Token | 值 | 用途 |
|---|---|---|
| `instant` | 0.10s | 极微反馈：hover、pressed、光标 |
| `quick` | 0.20s | 按钮、开关、小状态切换 |
| `standard` | 0.30s | 卡片、弹层、通用转场（Apple 主流区间 0.25–0.35s） |
| `expressive` | 0.50s | 内容揭示、愉悦动效、hero |
| `hero` | 0.70s | 全屏转场、App 打开 |

### 3.2 spring（弹簧，核心）

弹簧是**交互元素唯一正确的动效**。三档心法（阻尼比 `dampingFraction`）：

| Token | response | dampingFraction | 手感 | 典型场景 |
|---|---|---|---|---|
| `snappy` | 0.25s | 0.85 | 干脆利落、轻微回弹 | 按钮、开关、即时反馈 |
| `fluid` | 0.40s | 0.70 | 流畅优雅、可见回弹 | 卡片、弹层、通用 UI |
| `playful` | 0.55s | 0.50 | 活泼俏皮、明显回弹 | 点赞、成就、空状态 |
| `gentle` | 0.70s | 0.80 | 舒缓大气、近无回弹 | 大面板、氛围背景 |

> **阻尼比记忆点**：0.6–0.85 = 流畅，0.4–0.5 = 活泼，1.0 = 稳定无回弹。**响应时间（response）决定快慢，阻尼比（dampingFraction）决定回弹量。**

### 3.3 easing（缓动，仅限非交互）

缓动曲线**只用于时间驱动、不可中断的场景**（进度、纯淡入、线性运动）。交互元素禁用缓动。

| Token | 曲线 | 用途 |
|---|---|---|
| `standard` | cubic-bezier(0.4, 0, 0.2, 1) | 进出场通用（近似 easeInOut） |
| `decelerate` | cubic-bezier(0, 0, 0.2, 1) | 元素**进入**（快速入场，减速收尾） |
| `accelerate` | cubic-bezier(0.4, 0, 1, 1) | 元素**退出**（加速离场） |
| `linear` | linear | 进度条、旋转、匀速循环 |

### 3.4 distance（位移量）

| Token | 值 | 用途 |
|---|---|---|
| `micro` | 4pt | 按压下沉、图标微动 |
| `small` | 8pt | 图标进出、hover 位移 |
| `medium` | 16pt | 卡片上浮进入、列表重排 |
| `large` | 48pt | 弹层上滑、元素从底部进入 |
| `offscreen` | 100% | 模态、全屏转场 |

### 3.5 scale / opacity（缩放 / 透明度）

- `pressed` 0.94 —— 按压反馈（Apple 典型区间 0.90–0.96）。
- `hovered` 1.02 —— macOS 悬停。
- `emphasis` 1.08 —— 拉起强调（配合触觉）。
- `exit` 0.80 —— 退场缩小。
- `opacity.scrim` 0.4 —— 遮罩；`opacity.dimmed` 0.6 —— 次级内容。

### 3.6 haptics（触觉）

**触觉必须与视觉动画同帧触发**（`sensoryFeedback` 的 `trigger:` 绑定到与动画相同的状态值）。

- `selection` —— 选择器滚动、分段切换。
- `impactLight/Medium/Heavy` —— 点击强度分级：轻点 light、确认 medium、破坏性 heavy。
- `rigid/soft` —— 硬/软撞击质感。
- `success/warning/error` —— 通知语义。

### 3.7 material（材质）

- `glass`（iOS 26+）= `glassEffect(.regular)`；旧系统降级 `ultraThinMaterial`。
- 层级递进：`regular`(ultraThin) → `elevated`(regular) → `solid`(thick)。

---

## 4. 交互 → 动效映射（决策规则，Agent 的指导原则）

Agent 面对任意 UI 元素，按以下**确定性流程**选动效。这是本文档最核心的「指导原则」。

### 4.1 分类决策流程（Decision Flow）

```
问 1：该元素是否响应触摸/手势？（按钮、滑块、拖拽卡片…）
  └─ 是 → 弹簧（snappy/fluid），禁用缓动；确认点加触觉。→ 跳到「弹簧配方」
问 2：是否是结构变化？（进入/退出、导航、转场、列表增删）
  └─ 是 → 用 standard/expressive 时长 + 对应缓动（进入 decelerate，退出 accelerate）
          或 fluid 弹簧；跨层用 matchedGeometryEffect。→ 跳到「转场配方」
问 3：是否是内容/插画/品牌？（吉祥物、引导、空状态、片头）
  └─ 是 → Lottie（线性）/ Rive（交互）。禁止手写代码拼插画。→ 「内容配方」
问 4：是否是「wow」时刻？（需要惊艳，如液态、折射、光斑、活渐变）
  └─ 是 → Metal 着色器 / MeshGradient + hero + 触觉同步。→ 「天花板配方」
问 5：是否改变布局属性（frame/padding/文本尺寸）？
  └─ 是 → 禁止动画布局。改用 transform（scale/offset/rotation）+ opacity。→ 「性能铁律」
问 6：用户是否开启「减弱动态」？
  └─ 是 → 全部坍缩为 ≤0.2s 的淡入淡出或静止。→ 「可访问性」
```

### 4.2 组件级动效映射表

| 组件 / 交互 | 动效 | Token 组合 | 触觉 |
|---|---|---|---|
| 按钮按压 | 缩放 + 弹簧回弹 | `scale.pressed` + `spring.snappy` | light |
| 开关 / Toggle | 弹簧滑到新位 | `spring.snappy` | medium |
| 卡片出现 | 淡入 + 上浮 | `opacity` 0→1 + `distance.small` + `spring.fluid` | — |
| 弹层 / Sheet | 上滑 + 遮罩淡入 | `distance.large` + `easing.decelerate` + `opacity.scrim` | — |
| 导航 push | 从右滑入 + 视差 | `distance.offscreen` + `easing.decelerate` | — |
| Hero 转场 | 共享元素旅行 | `matchedGeometryEffect` + `spring.fluid` / `duration.hero` | — |
| 列表增删/重排 | 位移弹簧 + 兄弟项让位 | `spring.fluid` + `distance.medium` | light |
| 图标状态（点赞） | bounce/pulse 符号动画 | `symbolEffect(.bounce)` + `spring.playful` | medium |
| 空状态 / 引导 | 插画动效 | Lottie | — |
| 背景氛围 | 渐变漂移 | `MeshGradient` + `gentle` 4s 循环 | — |
| 滚动视差 | 滚动驱动 | `scrollTransition(.interactive)` | — |
| 3D 卡片翻转 | 旋转 | `rotation3DEffect` + `spring.fluid` | light |
| 数字滚动 | 数值插值 | `contentTransition(.numericText)` + `spring.fluid` | — |

---

## 5. 代码配方（Token → Code，Agent 落地依据）

### 5.1 弹簧配方（交互元素，唯一正确）

```swift
// 按钮：可中断弹簧 + 同帧触觉
Button("发送") { action() }
    .buttonStyle(.borderedProminent)
    .scaleEffect(isPressed ? 0.94 : 1.0)          // scale.pressed
    .animation(.spring(response: 0.25, dampingFraction: 0.85), value: isPressed) // spring.snappy
    .sensoryFeedback(.impact(weight: .light), trigger: isPressed) // haptics.impactLight 同帧

// 手势驱动的可中断弹簧（拖拽卡片）
@GestureState private var drag = CGSize.zero
card.offset(drag)
    .animation(.interactiveSpring(response: 0.4, dampingFraction: 0.7), value: drag) // spring.fluid
```

### 5.2 转场配方（结构变化）

```swift
// Hero：共享元素旅行
@Namespace private var ns
listCard.matchedGeometryEffect(id: "card", in: ns)     // 源
detailView.matchedGeometryEffect(id: "card", in: ns)   // 目标
withAnimation(.spring(response: 0.4, dampingFraction: 0.7)) { showDetail = true }

// 进出场（进入 decelerate，退出 accelerate）
view.transition(.asymmetric(
    insertion: .move(edge: .bottom).combined(with: .opacity),  // 进入
    removal: .opacity))                                         // 退出
```

### 5.3 天花板配方（wow 时刻）

```swift
// Metal 液体波纹着色器
.layerEffect(ShaderLibrary.ripple(.float2(origin), .float(time)), maxSampleOffset: .zero)
// MeshGradient 活背景
MeshGradient(width: 3, height: 3, points: pts, colors: cols)
    .animation(.easeInOut(duration: 4).repeatForever(autoreverses: true), value: pts)
// 图标状态 + 触觉
Image(systemName: isLiked ? "heart.fill" : "heart")
    .symbolEffect(.bounce, value: isLiked)
    .sensoryFeedback(.impact(weight: .medium), trigger: isLiked)
// 多阶段脉冲
PhaseAnimator([false, true]) { p in Circle().scaleEffect(p ? 1.25 : 1) }
    animation: { _ in .spring(duration: 0.5, bounce: 0.3) }
// 数字滚动
Text("\(amount)").contentTransition(.numericText(value: Double(amount)))
// 滚动视差
CardView(item).scrollTransition(.interactive, axis: .vertical) { c, phase in
    c.scaleEffect(phase.isIdentity ? 1 : 0.85).opacity(phase.isIdentity ? 1 : 0.6)
}
```

### 5.4 质感配方（Liquid Glass，含降级）

```swift
if #available(iOS 26.0, *) {
    customControl.glassEffect(.regular, in: .rect(cornerRadius: 16))
} else {
    customControl.background(.ultraThinMaterial, in: .rect(cornerRadius: 16))
}
```

### 5.5 底层引擎配方（Core Animation / 粒子）

```swift
// 真物理弹簧（CASpringAnimation）：mass/stiffness/damping 三参数
let spring = CASpringAnimation(keyPath: "transform.scale")
spring.mass = 1; spring.stiffness = 300; spring.damping = 25   // 对应 snappy 手感
// 粒子系统（烟花/雪花/光点）
CAEmitterLayer + CAEmitterCell
// 复制层（波纹/螺旋/反射）
CAReplicatorLayer
```

### 5.6 内容配方（Lottie / Rive）

- **Lottie**（Airbnb → LottieFiles）：After Effects 导出 → 原生播放。用于**插画级线性动效**（吉祥物、引导、空状态、品牌片头）。
- **Rive**：状态机 + 运行时输入。用于**交互级动效**（按钮、游戏化控件、需要状态机的复杂动画）。
- **铁律**：复杂插画禁止手写代码拼。Lottie/Rive 的质量天花板高于任何手写补间。

---

## 6. 可访问性（Accessibility，Apple 红线）

**可访问性不是事后补充，是验收门禁。**

| 设置 | 检测 | 降级规则 |
|---|---|---|
| 减弱动态（Reduce Motion） | `@Environment(\.accessibilityReduceMotion)` | 所有动效坍缩为 ≤0.2s 淡入淡出或静止；禁用回弹、视差、粒子、MeshGradient 漂移、连续旋转 |
| 减弱透明度（Reduce Transparency） | `@Environment(\.accessibilityReduceTransparency)` | 玻璃/模糊材质替换为实色 |
| 前庭安全（Vestibular） | — | 禁止大范围视差；内容缩放 ≤ 1.15；禁止连续全屏旋转 |

```swift
@Environment(\.accessibilityReduceMotion) private var reduceMotion
if reduceMotion { content.opacity(1) }              // 静止
else { content.scaleEffect(...).animation(...) }
```

> **关键优势**：使用原生 SwiftUI 动画 + 标准材质时，以上适配**自动生效**；只有自定义 Metal 着色器/粒子需要手动监听降级。

---

## 7. 性能预算（Performance Budget，硬指标）

高级感的前提是「绝不掉帧」。以下为**硬性红线**：

1. **帧率**：60fps（ProMotion 120fps），0 掉帧，hitch 占比 < 5%。
2. **只动画合成器属性**：`opacity` / `scale` / `offset` / `rotation` / `color` / `cornerRadius`。**禁止动画 `frame` / `padding` / 文本尺寸**（触发主线程布局，性能灾难）。
3. **时长上限**：单次动画 ≤ 0.5s，hero ≤ 0.8s。
4. **并发上限**：同屏并发动画 ≤ 5。
5. **着色器成本**：`maxSampleOffset` 收敛；全屏着色器同屏 ≤ 1；渐变优先用 `MeshGradient`（GPU 优化）而非自定义着色器。
6. **避免 `GeometryReader` 进入动画路径**（触发布局重算）。
7. **`onAppear` 动画闪烁**：首帧从默认值跳变——用 `PhaseAnimator` 或 `onAppear + withAnimation` 处理首帧。

---

## 8. 平台矩阵（Platform Matrix）

| 能力 | iOS 26 / macOS 26 | iOS 18 / macOS 15 | iOS 17 / macOS 14 | watchOS | visionOS |
|---|---|---|---|---|---|
| `glassEffect`（Liquid Glass） | ✅ | ✗ | ✗ | ✗ | ✅ |
| `MeshGradient` | ✅ | ✅ | ✗ | ✗ | ✅ |
| `PhaseAnimator` / `KeyframeAnimator` / `symbolEffect` / `scrollTransition` / `sensoryFeedback` / 着色器 | ✅ | ✅ | ✅ | ✅（降级） | ✅ |
| `matchedGeometryEffect` | ✅ | ✅ | ✅ | ✅ | ✅ |
| 弹簧全家桶 | ✅ | ✅ | ✅ | ✅ | ✅ |
| RealityKit 3D | ✅ | ✅ | ✅ | ✗ | ✅（主引擎） |

**watchOS 特殊约束**：屏幕小、GPU 弱 → 禁用 `MeshGradient` / 玻璃 / 大粒子；动效以「即时、克制」为主；触觉是主要反馈通道。
**visionOS 特殊约束**：动效需尊重空间上下文（深度、视差）；3D 用 RealityKit；避免让用户眩晕的快速移动。

---

## 9. Do's and Don'ts

### Do（必须）

- ✅ 交互元素**永远用弹簧**，禁用缓动。
- ✅ 触觉与视觉**同帧触发**。
- ✅ 跨层转场用 `matchedGeometryEffect`。
- ✅ 复杂插画交给 **Lottie / Rive**。
- ✅ 玻璃质感优先 `glassEffect`（降级 material）。
- ✅ 动效尊重「减弱动态 / 减弱透明度」。
- ✅ 动画只作用于合成器属性。
- ✅ 动效参数固化为 token，全局统一。

### Don't（禁止）

- ❌ 用 `easeInOut` 做交互反馈（「假」动效）。
- ❌ 动画 `frame` / `padding` / 文本（布局动画 = 掉帧）。
- ❌ 手写代码拼复杂插画（用 Lottie/Rive）。
- ❌ 玻璃特效铺满全屏自定义控件（Liquid Glass 官方警告「少用」）。
- ❌ 连续动画不提供停止/降级（尊重 reduce motion）。
- ❌ 触觉用于连续动画（只用于确认点）。
- ❌ 动效密度过高（L3 点缀过度 = 廉价感）。
- ❌ 动画时长 > 0.8s（用户等待焦虑）。

---

## 10. Agent 创作工作流（How an Agent Applies This Spec）

Agent 接到「为 X 元素设计动效」的任务时，按以下步骤执行：

1. **分类**：用第 4.1 节决策流程判定 X 属于哪一类（交互 / 结构 / 内容 / wow / 布局 / 无障碍）。
2. **查表**：在第 4.2 节组件映射表找到 X 对应的 token 组合。
3. **落地**：用第 5 节代码配方，将 token 替换为精确代码（不脑补参数）。
4. **无障碍**：叠加第 6 节降级逻辑（`accessibilityReduceMotion` / `ReduceTransparency`）。
5. **验证**：对照第 7 节性能预算自审（合成器属性？时长？并发？）+ 第 9 节 Do/Don't 自审。
6. **交付**：产出可直接运行的 SwiftUI 代码，附「token 名 → 使用原因」一行注释。

**质量自评表（交付前勾选）**：

| 检查项 | 通过标准 |
|---|---|
| 物理正确 | 交互用弹簧，未用缓动 |
| 空间连续 | 跨层用 hero / matched geometry |
| 内容插画 | 复杂动画走 Lottie/Rive |
| 多感官同步 | 触觉与视觉同帧 |
| 克制 | 无 L3 动效过度 |
| 性能 | 合成器属性 + 时长/并发达标 |
| 无障碍 | reduce motion / transparency 降级齐全 |

---

## 11. 参考实现锚点

- **灵动岛（Dynamic Island）**：hero / matched geometry + 弹簧的巅峰示范。
- **App 图标展开**：共享元素 + 弹簧，全屏铺满再进 App。
- **iOS 26 Liquid Glass**：玻璃折射、随设备高光、流体形变。
- **Duolingo**：Lottie 吉祥物 + 游戏化微交互。
- **WWDC18「Designing Fluid Interfaces」**：本设计语言物理哲学的源头。
- **WWDC25「What's new in SwiftUI」（256）/「Build a SwiftUI app with the new design」（323）**：Liquid Glass 落地。

---

*本文档是可执行的规范：token 精确值驱动代码，决策规则驱动选型，质量门禁驱动验收。任何「惊艳」都建立在物理正确、空间连续、内容插画、多感官同步、克制的组合之上。*
