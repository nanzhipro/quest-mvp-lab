//! Result reporting: one unified Markdown file for all task outcomes.

use crate::protocol::Usage;
use serde::Serialize;
use std::path::{Path, PathBuf};

/// Outcome of a single task run.
#[derive(Debug, Clone, Serialize)]
pub struct TaskResult {
    /// Task category (describe / ocr / compare / create / gen-probe).
    pub task: String,
    /// Human-readable label, e.g. `describe-01.jpg (base64)`.
    pub label: String,
    /// Input image paths, relative to the report directory when possible.
    pub images: Vec<String>,
    /// `ok` or `error`.
    pub status: String,
    /// Model text output (never contains the API key).
    pub output: String,
    /// Token usage, when available.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage: Option<Usage>,
    /// Error message for failed runs.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// Extra structured data (e.g. gen-probe rounds).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extra: Option<serde_json::Value>,
    /// Wall-clock duration of the task in milliseconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<u64>,
}

impl TaskResult {
    pub fn ok(
        task: impl Into<String>,
        label: impl Into<String>,
        images: Vec<String>,
        output: String,
        usage: Option<Usage>,
    ) -> Self {
        TaskResult {
            task: task.into(),
            label: label.into(),
            images,
            status: "ok".into(),
            output,
            usage,
            error: None,
            extra: None,
            duration_ms: None,
        }
    }

    pub fn fail(
        task: impl Into<String>,
        label: impl Into<String>,
        images: Vec<String>,
        error: impl Into<String>,
    ) -> Self {
        TaskResult {
            task: task.into(),
            label: label.into(),
            images,
            status: "error".into(),
            output: String::new(),
            usage: None,
            error: Some(error.into()),
            extra: None,
            duration_ms: None,
        }
    }

    /// Attach extra structured data (e.g. gen-probe rounds).
    pub fn with_extra(mut self, extra: serde_json::Value) -> Self {
        self.extra = Some(extra);
        self
    }

    /// Attach wall-clock duration (for performance benchmarking).
    pub fn with_duration(mut self, duration_ms: u64) -> Self {
        self.duration_ms = Some(duration_ms);
        self
    }
}

/// Full report: metadata + all task results.
#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub created_at: String,
    pub model: String,
    pub results: Vec<TaskResult>,
}

impl Report {
    pub fn new(created_at: String, model: String) -> Self {
        Report {
            created_at,
            model,
            results: Vec::new(),
        }
    }

    pub fn push(&mut self, r: TaskResult) {
        self.results.push(r);
    }

    /// Render everything into a single Markdown document.
    pub fn render_markdown(&self) -> String {
        let ok_count = self.results.iter().filter(|r| r.status == "ok").count();
        let total = self.results.len();
        let mut md = String::new();
        md.push_str("# DeepSeek 视觉多模态模型验证报告\n\n");
        md.push_str(&format!("- **模型**: `{}`\n", self.model));
        md.push_str(&format!("- **时间**: {}\n", self.created_at));
        md.push_str(&format!("- **结果**: {ok_count}/{total} 项任务成功\n\n"));

        // 任务索引
        md.push_str("## 任务索引\n\n| # | 任务 | 状态 | Token | 耗时 |\n|---|------|------|-------|------|\n");
        for (i, r) in self.results.iter().enumerate() {
            let usage = r
                .usage
                .map(|u| u.total_tokens.to_string())
                .unwrap_or_else(|| "-".into());
            let dur = r
                .duration_ms
                .map(|ms| format!("{:.1}s", ms as f64 / 1000.0))
                .unwrap_or_else(|| "-".into());
            md.push_str(&format!(
                "| {} | {} | {} | {} | {} |\n",
                i + 1,
                r.label,
                r.status,
                usage,
                dur
            ));
        }
        md.push('\n');

        for (i, r) in self.results.iter().enumerate() {
            md.push_str(&format!("---\n\n## {}. {}\n\n", i + 1, r.label));
            md.push_str(&format!(
                "**任务类型**: `{}`  **状态**: {}\n\n",
                r.task, r.status
            ));
            if !r.images.is_empty() {
                md.push_str("**输入图片**:\n\n");
                for img in &r.images {
                    md.push_str(&format!("- `{img}`\n"));
                }
                md.push('\n');
            }
            if r.status == "error" {
                md.push_str(&format!(
                    "**错误**:\n\n```text\n{}\n```\n\n",
                    r.error.as_deref().unwrap_or("未知错误")
                ));
            } else {
                let dur = r
                    .duration_ms
                    .map(|ms| format!("，耗时 {:.1}s", ms as f64 / 1000.0))
                    .unwrap_or_default();
                if let Some(usage) = r.usage {
                    md.push_str(&format!(
                        "**Token 用量**: prompt={} completion={} total={}{}\n\n",
                        usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, dur
                    ));
                }
                md.push_str("**模型输出**:\n\n```text\n");
                md.push_str(&r.output);
                md.push_str("\n```\n\n");
            }
            if let Some(extra) = &r.extra {
                md.push_str("**附加数据**:\n\n```json\n");
                md.push_str(&serde_json::to_string_pretty(extra).unwrap_or_default());
                md.push_str("\n```\n\n");
            }
        }
        md
    }
}

/// 一张图的三列对比行：原图 | 模型理解 | 还原图（理解文本 → SVG → PNG）。
#[derive(Debug, Clone, Serialize)]
pub struct CompareRow {
    /// 图片文件名（标题）。
    pub title: String,
    /// 原图（缩略图）相对报告目录的路径。
    pub original_rel: String,
    /// 模型理解输出全文（列 2）。
    pub understanding: String,
    /// 还原图 PNG 相对报告目录的路径（列 3）。
    pub recreated_png_rel: String,
    /// 还原图 SVG 源码相对路径。
    pub recreated_svg_rel: String,
    /// 理解阶段 token 用量。
    pub usage_understanding: Option<Usage>,
    /// 还原阶段 token 用量。
    pub usage_recreate: Option<Usage>,
    /// 理解阶段耗时（毫秒）。
    pub duration_understanding_ms: u64,
    /// 还原阶段耗时（毫秒）。
    pub duration_recreate_ms: u64,
    /// 还原尝试次数。
    pub recreate_attempts: u32,
}

/// 三列对比报告：一张图一行，原图/理解/还原并排对照。
#[derive(Debug, Clone, Serialize)]
pub struct CompareReport {
    pub created_at: String,
    pub model: String,
    pub rows: Vec<CompareRow>,
}

impl CompareReport {
    pub fn new(created_at: String, model: String) -> Self {
        CompareReport {
            created_at,
            model,
            rows: Vec::new(),
        }
    }

    pub fn push(&mut self, row: CompareRow) {
        self.rows.push(row);
    }

    /// 渲染为单个 Markdown 文件（三列表格）。
    pub fn render_markdown(&self) -> String {
        let mut md = String::new();
        md.push_str("# DeepSeek 视觉多模态 — 图片理解与还原对比报告\n\n");
        md.push_str(&format!("- **模型**: `{}`\n", self.model));
        md.push_str(&format!("- **时间**: {}\n", self.created_at));
        md.push_str(&format!("- **图片数**: {}\n\n", self.rows.len()));
        md.push_str("> 每张图三列对照：**原图**（左）｜**模型理解输出**（中）｜**还原图**（右，理解文本 → 再次调用视觉模型 → SVG → PNG）。\n\n");

        for (i, row) in self.rows.iter().enumerate() {
            md.push_str(&format!("---\n\n## {}. {}\n\n", i + 1, row.title));
            md.push_str("| 原图 | 模型理解输出 | 还原图（理解文本 → SVG → PNG） |\n");
            md.push_str("| ---- | ------------ | ------------------------------ |\n");
            let text = row
                .understanding
                .replace("\r\n", "\n")
                .replace("\n\n", "<br><br>")
                .replace('\n', "<br>");
            md.push_str(&format!(
                "| ![]({}) | {} | ![]({}) |\n",
                row.original_rel, text, row.recreated_png_rel
            ));
            md.push('\n');
            let dur_u = row.duration_understanding_ms as f64 / 1000.0;
            let dur_r = row.duration_recreate_ms as f64 / 1000.0;
            let usage_u = row
                .usage_understanding
                .map(|u| format!("{} token", u.total_tokens))
                .unwrap_or_else(|| "-".into());
            let usage_r = row
                .usage_recreate
                .map(|u| format!("{} token", u.total_tokens))
                .unwrap_or_else(|| "-".into());
            md.push_str(&format!("- **SVG 源码**: `{}`\n", row.recreated_svg_rel));
            md.push_str(&format!(
                "- **理解**: {dur_u:.1}s（{usage_u}）｜**还原**: {dur_r:.1}s（{usage_r}，{n} 次尝试）\n\n",
                dur_u = dur_u,
                usage_u = usage_u,
                dur_r = dur_r,
                usage_r = usage_r,
                n = row.recreate_attempts
            ));
        }
        md
    }
}

/// Writes the unified report into a timestamped directory under `reports/`.
pub struct ReportWriter {
    dir: PathBuf,
}

impl ReportWriter {
    /// Create `reports/<YYYYMMDD-HHMMSS>/` under `root`.
    pub fn new(root: &Path) -> anyhow::Result<Self> {
        let ts = chrono_now();
        let dir = root.join(&ts);
        std::fs::create_dir_all(&dir)?;
        Ok(ReportWriter { dir })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Persist the unified Markdown report plus a raw JSON companion.
    pub fn write(&self, report: &Report) -> anyhow::Result<PathBuf> {
        let md_path = self.dir.join("report.md");
        std::fs::write(&md_path, report.render_markdown())?;

        let json_path = self.dir.join("report.json");
        std::fs::write(&json_path, serde_json::to_string_pretty(report)?)?;
        Ok(md_path)
    }

    /// Persist a three-column compare report (Markdown + JSON).
    pub fn write_compare(&self, report: &CompareReport) -> anyhow::Result<PathBuf> {
        let md_path = self.dir.join("report.md");
        std::fs::write(&md_path, report.render_markdown())?;

        let json_path = self.dir.join("report.json");
        std::fs::write(&json_path, serde_json::to_string_pretty(report)?)?;
        Ok(md_path)
    }
}

/// Local-time `YYYYMMDD-HHMMSS` (no external chrono dependency).
pub fn now_label() -> String {
    chrono_now()
}

fn chrono_now() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (y, mo, d, h, mi, s) = civil_from_unix(now);
    format!("{y:04}{mo:02}{d:02}-{h:02}{mi:02}{s:02}")
}

/// Convert Unix seconds to civil (UTC) date components — self-contained,
/// avoids pulling chrono for a timestamp.
fn civil_from_unix(secs: i64) -> (i64, i64, i64, i64, i64, i64) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (h, mi, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);

    // Howard Hinnant's civil_from_days algorithm
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d, h, mi, s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_date_known_epoch() {
        assert_eq!(civil_from_unix(0), (1970, 1, 1, 0, 0, 0));
    }

    #[test]
    fn civil_date_mid_2026() {
        // 2026-08-21 00:00:00 UTC
        assert_eq!(civil_from_unix(1_787_270_400), (2026, 8, 21, 0, 0, 0));
    }

    #[test]
    fn markdown_report_contains_all_sections() {
        let mut report = Report::new(
            "20260821-120000".into(),
            "deepseek-v4-flash-vision-exp".into(),
        );
        report.push(TaskResult::ok(
            "describe",
            "describe-01.jpg (base64)",
            vec!["testimages/《欢迎来龙餐馆》01.jpg".into()],
            "画面中有一条龙。".into(),
            Some(Usage {
                prompt_tokens: 400,
                completion_tokens: 20,
                total_tokens: 420,
            }),
        ));
        report.push(TaskResult::fail("ocr", "ocr-01.jpg", vec![], "请求失败"));
        let md = report.render_markdown();
        assert!(md.contains("# DeepSeek 视觉多模态模型验证报告"));
        assert!(md.contains("1/2 项任务成功"));
        assert!(md.contains("describe-01.jpg (base64)"));
        assert!(md.contains("画面中有一条龙。"));
        assert!(md.contains("**错误**"));
        assert!(md.contains("```text\n请求失败\n```"));
    }

    #[test]
    fn compare_report_renders_three_columns() {
        let mut report = CompareReport::new(
            "20260821-120000".into(),
            "deepseek-v4-flash-vision-exp".into(),
        );
        report.push(CompareRow {
            title: "《欢迎来龙餐馆》01.jpg".into(),
            original_rel: "assets/original_01.png".into(),
            understanding: "画面主体是一辆白色货车，旁边有五人。\n\n背景是中东街道。".into(),
            recreated_png_rel: "assets/recreated_01.svg.png".into(),
            recreated_svg_rel: "assets/recreated_01.svg".into(),
            usage_understanding: Some(Usage {
                prompt_tokens: 483,
                completion_tokens: 200,
                total_tokens: 683,
            }),
            usage_recreate: Some(Usage {
                prompt_tokens: 400,
                completion_tokens: 300,
                total_tokens: 700,
            }),
            duration_understanding_ms: 10_000,
            duration_recreate_ms: 40_000,
            recreate_attempts: 1,
        });
        let md = report.render_markdown();
        assert!(md.contains("# DeepSeek 视觉多模态 — 图片理解与还原对比报告"));
        assert!(md.contains("| 原图 | 模型理解输出 | 还原图（理解文本 → SVG → PNG） |"));
        assert!(md.contains("![](assets/original_01.png)"));
        assert!(md.contains("画面主体是一辆白色货车，旁边有五人。<br><br>背景是中东街道。"));
        assert!(md.contains("![](assets/recreated_01.svg.png)"));
        assert!(md.contains("**理解**: 10.0s（683 token）｜**还原**: 40.0s（700 token，1 次尝试）"));
    }
}
