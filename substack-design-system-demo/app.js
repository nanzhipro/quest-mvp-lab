"use strict";

const observedColors = [
  ["Muted", "#777777", "1084 uses"],
  ["Border", "rgba(0, 0, 0, 0.1)", "381 uses"],
  ["Icon muted", "#b6b6b6", "279 uses"],
  ["Content", "#363737", "261 uses"],
  ["Primary", "#ff6719", "218 uses"],
  ["Canvas", "#ffffff", "187 uses"],
  ["Soft surface", "#eeeeee", "159 uses"],
  ["Black", "#000000", "155 uses"],
  ["Disabled", "#c8c8c8", "111 uses"],
  ["Dark surface", "#232525", "31 uses"],
  ["Link", "#0083d5", "15 uses"],
  ["Light border", "rgba(255, 255, 255, 0.1)", "13 uses"],
  ["Focus", "#7b61ff", "12 uses"],
  ["Success", "#2f6d5d", "8 uses"],
  ["Light fill", "rgba(255, 255, 255, 0.5)", "6 uses"],
  ["Overlay", "rgba(40, 40, 40, 0.75)", "4 uses"],
  ["Glass", "rgba(255, 255, 255, 0.8)", "1 use"]
];

const observedGradients = [
  ["Olive", "rgba(93, 86, 58, 0.75)"],
  ["Violet", "rgba(54, 40, 91, 0.75)"],
  ["Graphite", "rgba(57, 57, 56, 0.75)"],
  ["Umber", "rgba(64, 33, 31, 0.75)"],
  ["Steel", "rgba(72, 76, 76, 0.75)"],
  ["Blue gray", "rgba(76, 96, 124, 0.75)"],
  ["Cool gray", "rgba(88, 96, 111, 0.75)"],
  ["White fade", "rgba(255, 255, 255, 0)"],
  ["Clay", "rgba(100, 84, 68, 0.75)"],
  ["Deep blue", "rgba(32, 51, 67, 0.75)"],
  ["Slate", "rgba(44, 60, 76, 0.75)"],
  ["Green", "rgba(61, 96, 83, 0.75)"],
  ["Lichen", "rgba(69, 67, 57, 0.75)"],
  ["Brick", "rgba(98, 53, 48, 0.75)"]
];

const coreSpacing = [4, 8, 12, 16, 20, 24, 32, 40];
const allObservedDimensions = [
  "-48px", "-8px", "-6px", "-4px", "-1.875px", "-1.625px", "0px", "0.8125px",
  "0.9375px", "1.625px", "1.875px", "2px", "4px", "6px", "8px", "10px", "12px",
  "13px", "15px", "16px", "17px", "18.2px", "20px", "21px", "24px", "28px", "30px",
  "32px", "39.68px", "40px", "232px", "420px", "9999px"
];

const radiusValues = [0, 4, 8, 12, 9999];
const componentChecks = [
  ["Fixed desktop navigation", "observed"],
  ["Mobile bottom navigation", "observed"],
  ["Primary action", "observed"],
  ["Secondary action", "observed"],
  ["Pill action", "observed"],
  ["Subscribe action", "observed"],
  ["More options", "observed"],
  ["Segmented feed filter", "observed"],
  ["Article stream", "observed"],
  ["Author metadata", "observed"],
  ["Action counters", "observed"],
  ["Avatar treatment", "observed"],
  ["Media region", "observed"],
  ["Link preview", "observed"],
  ["See more action", "observed"],
  ["Search field", "extension"],
  ["Email validation", "extension"],
  ["Select / textarea", "extension"],
  ["Radio / checkbox", "extension"],
  ["Toggle switch", "extension"],
  ["Hover / focus / active", "extension"],
  ["Disabled / loading", "extension"],
  ["Empty / error", "extension"],
  ["Dialog + backdrop", "extension"],
  ["Toast feedback", "extension"],
  ["250ms motion curve", "observed"],
  ["500ms motion curve", "observed"],
  ["Reduced motion", "extension"],
  ["Desktop 1440", "observed"],
  ["Mobile 390", "observed"]
];

function renderStaticSpecimens() {
  const colorGrid = document.querySelector("#color-grid");
  observedColors.forEach(([name, value, usage]) => {
    const item = document.createElement("div");
    item.className = "swatch";
    item.innerHTML = `<div class="swatch-color"></div><div class="swatch-meta"><strong>${name}</strong><span>${value}</span><span>${usage}</span></div>`;
    item.querySelector(".swatch-color").style.background = value;
    colorGrid.append(item);
  });

  const gradientGrid = document.querySelector("#gradient-grid");
  observedGradients.forEach(([name, color], index) => {
    const item = document.createElement("div");
    item.className = "gradient-swatch";
    item.textContent = `${String(index + 1).padStart(2, "0")} ${name}`;
    item.style.backgroundImage = index === 7
      ? "linear-gradient(to left, rgb(255, 255, 255), rgba(255, 255, 255, 0)), linear-gradient(#363737, #363737)"
      : `linear-gradient(${color}, ${color}), linear-gradient(#b6b6b6, #b6b6b6)`;
    gradientGrid.append(item);
  });

  const spacingScale = document.querySelector("#spacing-scale");
  coreSpacing.forEach((value) => {
    const item = document.createElement("div");
    item.className = "spacing-item";
    item.innerHTML = `<strong>${value}</strong><i class="spacing-bar"></i><code>${value}px</code>`;
    item.querySelector(".spacing-bar").style.width = `${Math.max(value * 5, 8)}px`;
    spacingScale.append(item);
  });

  const radiusScale = document.querySelector("#radius-scale");
  radiusValues.forEach((value) => {
    const item = document.createElement("div");
    item.className = "radius-item";
    item.style.borderRadius = `${value}px`;
    item.innerHTML = `<code>${value === 9999 ? "full" : `${value}px`}</code>`;
    radiusScale.append(item);
  });

  const allDimensions = document.querySelector("#all-dimensions");
  allObservedDimensions.forEach((value) => {
    const item = document.createElement("code");
    item.textContent = value;
    allDimensions.append(item);
  });

  const checklist = document.querySelector("#component-checklist");
  componentChecks.forEach(([name, kind]) => {
    const item = document.createElement("div");
    item.className = "check-item";
    item.innerHTML = `<i class="check-indicator">✓</i><span>${name}</span><code>${kind}</code>`;
    checklist.append(item);
  });
}

function tokenPropertyFor(name) {
  if (name.includes("font-family")) return "fontFamily";
  if (name.includes("font-weight")) return "fontWeight";
  if (name.includes("font-size")) return "fontSize";
  if (name.includes("line-height")) return "lineHeight";
  if (name.includes("radius")) return "borderRadius";
  if (name.includes("border-width")) return "borderTopWidth";
  if (name.includes("spacing") || name.includes("size-left-nav")) return "marginLeft";
  return "color";
}

function getTokenDeclarations() {
  const sheet = Array.from(document.styleSheets).find((candidate) => candidate.href && candidate.href.endsWith("tokens.css"));
  if (!sheet) return [];
  const tokens = [];
  Array.from(sheet.cssRules).forEach((rule) => {
    if (!rule.style) return;
    Array.from(rule.style).filter((name) => name.startsWith("--")).forEach((name) => {
      tokens.push({ name, source: rule.style.getPropertyValue(name).trim() });
    });
  });
  return tokens;
}

function probeToken(token) {
  const node = document.createElement("span");
  const property = tokenPropertyFor(token.name);
  node.style.position = "absolute";
  node.style.visibility = "hidden";
  node.style[property] = `var(${token.name})`;
  document.body.append(node);
  const resolved = getComputedStyle(node)[property];
  node.remove();
  const valid = Boolean(resolved) && !resolved.includes("var(");
  return { ...token, property, resolved, valid };
}

function runTokenAudit(showToast = false) {
  const declarations = getTokenDeclarations();
  const results = declarations.map(probeToken);
  const passed = results.filter((item) => item.valid).length;
  const score = declarations.length ? Math.round((passed / declarations.length) * 100) : 0;

  document.querySelector("#metric-token-count").textContent = String(declarations.length || "--");
  document.querySelector("#coverage-score").textContent = `${passed}/${declarations.length}`;
  document.querySelector("#coverage-progress").style.width = `${score}%`;
  document.querySelector("#coverage-message").textContent = declarations.length
    ? `${passed} 项已通过 CSSOM 解析与实际属性应用；${declarations.length - passed} 项需要复核。`
    : "未能读取 tokens.css；请通过本地 HTTP 服务打开页面。";

  const probeList = document.querySelector("#token-probes");
  probeList.replaceChildren();
  results.forEach((result) => {
    const item = document.createElement("div");
    item.className = "token-probe";
    item.title = `${result.source} → ${result.resolved}`;
    item.innerHTML = `<i class="probe-indicator">${result.valid ? "✓" : "!"}</i><span>${result.name}</span><code>${result.property}</code>`;
    if (!result.valid) item.querySelector(".probe-indicator").style.background = "var(--color-palette-hex-ff6719)";
    probeList.append(item);
  });

  if (showToast) showToastMessage(`Token 检查完成：${passed}/${declarations.length} 已解析`);
  return { passed, total: declarations.length, score };
}

let toastTimer = 0;
function showToastMessage(message) {
  const toast = document.querySelector("#toast");
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2200);
}

function initializeInteractions() {
  document.querySelectorAll("[data-toast]").forEach((button) => {
    button.addEventListener("click", () => showToastMessage(button.dataset.toast));
  });

  document.querySelectorAll("[data-action='run-audit']").forEach((button) => {
    button.addEventListener("click", () => runTokenAudit(true));
  });

  document.querySelectorAll(".segmented").forEach((group) => {
    group.querySelectorAll(".segment").forEach((button) => {
      button.addEventListener("click", () => {
        group.querySelectorAll(".segment").forEach((item) => {
          item.classList.toggle("is-selected", item === button);
          if (item.hasAttribute("role")) item.setAttribute("aria-selected", item === button ? "true" : "false");
        });
        if (button.dataset.view) document.body.classList.toggle("stress-mode", button.dataset.view === "stress");
      });
    });
  });

  document.querySelectorAll("#topic-filters .filter-chip").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#topic-filters .filter-chip").forEach((item) => item.classList.toggle("is-selected", item === button));
      const filter = button.dataset.filter;
      document.querySelectorAll(".feed-item").forEach((item) => {
        item.hidden = filter !== "all" && item.dataset.topic !== filter;
      });
    });
  });

  document.querySelectorAll(".button-subscribe").forEach((button) => {
    button.addEventListener("click", () => {
      const active = button.textContent === "Subscribed";
      button.textContent = active ? "Subscribe" : "Subscribed";
      button.classList.toggle("button-primary", !active);
      showToastMessage(active ? "订阅已取消" : "订阅成功");
    });
  });

  document.querySelectorAll(".action-row button").forEach((button) => {
    button.addEventListener("click", () => button.classList.toggle("is-active"));
  });

  document.querySelector(".play-button").addEventListener("click", (event) => {
    const playing = event.currentTarget.textContent === "Ⅱ";
    event.currentTarget.textContent = playing ? "▶" : "Ⅱ";
    event.currentTarget.setAttribute("aria-label", playing ? "播放视频" : "暂停视频");
    showToastMessage(playing ? "视频已暂停" : "视频样本正在播放");
  });

  const form = document.querySelector("#sample-form");
  const email = document.querySelector("#email-input");
  const message = form.querySelector(".field-message");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const valid = email.validity.valid;
    email.classList.toggle("is-invalid", !valid);
    message.classList.toggle("is-error", !valid);
    message.textContent = valid ? "格式有效，测试数据不会发送。" : "请输入有效的邮箱格式。";
    showToastMessage(valid ? "表单验证通过" : "请检查邮箱字段");
  });
  form.addEventListener("reset", () => {
    window.setTimeout(() => {
      email.classList.remove("is-invalid");
      message.classList.remove("is-error");
      message.textContent = "用于状态测试，不会发送。";
    }, 0);
  });

  const motionObject = document.querySelector("#motion-object");
  const reduceMotion = document.querySelector("#reduce-motion");
  const playMotion = (slow) => {
    motionObject.classList.toggle("is-slow", slow);
    motionObject.classList.toggle("is-reduced", reduceMotion.checked);
    motionObject.classList.toggle("is-moved");
  };
  document.querySelector("[data-action='play-motion']").addEventListener("click", () => playMotion(false));
  document.querySelector("[data-action='play-slow-motion']").addEventListener("click", () => playMotion(true));
  reduceMotion.addEventListener("change", () => motionObject.classList.toggle("is-reduced", reduceMotion.checked));

  const dialog = document.querySelector("#sample-dialog");
  document.querySelector("[data-action='open-dialog']").addEventListener("click", () => dialog.showModal());

  const sidebar = document.querySelector(".sidebar");
  const menuButton = document.querySelector(".mobile-menu");
  menuButton.addEventListener("click", () => {
    const open = sidebar.classList.toggle("is-open");
    menuButton.setAttribute("aria-expanded", String(open));
  });
  document.querySelectorAll(".side-nav a").forEach((link) => {
    link.addEventListener("click", () => {
      sidebar.classList.remove("is-open");
      menuButton.setAttribute("aria-expanded", "false");
    });
  });
}

function initializeSectionObserver() {
  const links = Array.from(document.querySelectorAll(".side-nav a"));
  const bottomLinks = Array.from(document.querySelectorAll(".mobile-bottom-nav a"));
  const sections = Array.from(document.querySelectorAll("main > section[id]"));
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    [...links, ...bottomLinks].forEach((link) => link.classList.toggle("is-active", link.getAttribute("href") === `#${visible.target.id}`));
  }, { rootMargin: "-20% 0px -65%", threshold: [0.05, 0.2, 0.5] });
  sections.forEach((section) => observer.observe(section));
}

window.addEventListener("DOMContentLoaded", () => {
  renderStaticSpecimens();
  initializeInteractions();
  initializeSectionObserver();
  window.requestAnimationFrame(() => {
    const audit = runTokenAudit(false);
    window.__designSystemAudit = audit;
    document.documentElement.dataset.audit = audit.total > 0 && audit.passed === audit.total ? "pass" : "review";
  });
});
