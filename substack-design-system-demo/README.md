# Substack Design System Demo

## 结论

这个独立测试页证明：`Substack - Extracted Design System` 已足以驱动一套一致、响应式且可交互的界面实现，但它仍是**单页、双视口、渲染态证据**，不能等同于 Substack 的私有组件源码或完整产品规范。

页面覆盖 88 个 CSS token，并将颜色、排版、间距、圆角、阴影、渐变证据、按钮、表单、导航、内容流、媒体、状态、动效和响应式行为放进同一验证台。原始采样没有覆盖的 hover、focus、disabled、loading、error 和 dialog 状态在界面中明确标记为 `TEST EXTENSION`。

## 背景

输入规范位于工作区：

`docss/2026-09-01/2026-09-01-substack-design-system-attempt-4/DESIGN.md`

项目直接复用与该规范一同验证通过的 `tokens.css`。本地图片是为测试页制作的原创程序化位图，不包含或重新分发捕获页面中的字体和图片。

## 关键设计决定

- **规范优先**：所有页面颜色、字号、行高、间距、圆角和结构尺寸均来自抽取 token 或规范中记录的实测值。
- **证据与扩展分离**：已观察组件和为了完整 QA 增加的状态测试分别标记，避免扩大采样结论。
- **运行时覆盖**：页面通过 CSSOM 发现 88 个 token，并逐个应用到真实 CSS 属性，显示解析结果。
- **保留缺陷**：橙底白字的 `2.91:1` 对比度按原样展示并标记失败，不用替换色掩盖风险。
- **响应式双轨**：桌面使用 232px 固定侧栏，移动端切换为顶部菜单和底部导航。
- **自包含**：除浏览器标准能力外没有运行时依赖、远程字体或远程图片。

## 运行

在工作区根目录执行：

```bash
python3 -m http.server 4311 --directory quest-mvp-lab
```

然后打开：

```text
http://localhost:4311/substack-design-system-demo/
```

不要直接使用 `file://` 打开；运行时 token 审计依赖同源样式表的 CSSOM 访问。

## 验证

静态验证：

```bash
cd quest-mvp-lab/substack-design-system-demo
node verify.mjs
```

浏览器验证至少覆盖：

1. 桌面视口 `1440x900`。
2. 移动视口 `390x844`。
3. 页面无横向溢出、图片空白或文字遮挡。
4. `Run audit` 显示 `88/88 tokens resolved`。
5. 筛选、订阅、表单校验、对话框、toast 和动效控件均可操作。
6. 系统启用 `prefers-reduced-motion` 时，持续动画和过渡被压缩。

## 文件结构

```text
substack-design-system-demo/
├── index.html              # 语义结构和全部测试场景
├── styles.css              # 组件、状态和响应式实现
├── app.js                  # 交互与 CSSOM token 审计
├── tokens.css              # 经验证的 88 个抽取 token
├── assets/                 # 原创 SVG 源和 PNG 位图
├── screenshots/            # 浏览器验收截图
└── verify.mjs              # 零依赖静态验证
```

## 已知边界

- Cahuenga 和 Spectral 字体文件未被重新分发；缺失时按规范中的字体栈回退到系统 serif。
- 单次公开页面采样未覆盖登录态、个性化、分页尾部、私有组件状态和跨域 iframe 内部。
- 渐变样本只作为证据目录使用，不承担主界面装饰角色。
