//! CLI entry: run validation scenarios against the DeepSeek vision model.

use clap::{Parser, Subcommand};
use ds_vision::client::{DeepSeekClient, ImageInput};
use ds_vision::config::Config;
use ds_vision::image::file_to_data_url;
use ds_vision::prompts;
use ds_vision::protocol::Usage;
use ds_vision::reporter::{CompareReport, CompareRow, Report, ReportWriter, TaskResult};
use std::path::{Path, PathBuf};

#[derive(Parser)]
#[command(
    name = "ds-vision",
    version,
    about = "DeepSeek 视觉多模态模型能力验证（deepseek-v4-flash-vision-exp）"
)]
struct Cli {
    /// 报告输出根目录（默认 reports/，内部按时间戳建子目录）
    #[arg(long, default_value = "reports")]
    out: PathBuf,
    /// 是否加载项目 .env 文件（默认加载）
    #[arg(long, action = clap::ArgAction::SetFalse)]
    dotenv: bool,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// 单图描述（图像理解）
    Describe {
        /// 图片路径（可多个）
        images: Vec<PathBuf>,
        /// 传图方式：base64（默认）或 file-api
        #[arg(long, default_value = "base64")]
        via: String,
    },
    /// 提取漫画文字（OCR）
    Ocr { image: PathBuf },
    /// 多图剧情连贯性对比（一次请求多张图）
    Compare { images: Vec<PathBuf> },
    /// 基于图片内容的创作（续写 + 分镜 + 画风）
    Create { image: PathBuf },
    /// 多模态生成能力探测（四连问）
    GenProbe {
        /// 可选输入图片
        image: Option<PathBuf>,
    },
    /// 基于理解用 SVG 还原图片（每张图产出一张还原图，保存至报告 assets/）
    Recreate {
        /// 图片路径（可多个，每张图单独还原）
        images: Vec<PathBuf>,
    },
    /// 三列对比报告：原图 | 模型理解输出 | 还原图（理解文本 → 再次调用视觉模型 → SVG → PNG）
    Report {
        /// 图片路径（可多个，每张图一行三列对比）
        images: Vec<PathBuf>,
    },
    /// 顺序执行全部场景（01.jpg 双路径：base64 + Files API）
    All,
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    if cli.dotenv {
        dotenvy::dotenv().ok();
    }
    let config = Config::from_env()?;
    let client = DeepSeekClient::new(config.clone());

    let writer = ReportWriter::new(&cli.out)?;
    let mut report = Report::new(now_label(), config.model().to_string());

    match cli.cmd {
        Cmd::Describe { images, via } => {
            if images.is_empty() {
                anyhow::bail!("describe 至少需要一张图片");
            }
            for img in &images {
                let r = describe_one(&client, img, &via, writer.dir());
                report.push(r);
            }
        }
        Cmd::Ocr { image } => {
            report.push(run_single(
                &client,
                &image,
                "ocr",
                prompts::OCR,
                "base64",
                writer.dir(),
            ));
        }
        Cmd::Compare { images } => {
            report.push(run_multi(
                &client,
                &images,
                "compare",
                prompts::COMPARE,
                writer.dir(),
            ));
        }
        Cmd::Create { image } => {
            report.push(run_single(
                &client,
                &image,
                "create",
                prompts::CREATE,
                "base64",
                writer.dir(),
            ));
        }
        Cmd::GenProbe { image } => {
            report.push(run_gen_probe(&client, image.as_deref(), writer.dir()));
        }
        Cmd::Recreate { images } => {
            if images.is_empty() {
                anyhow::bail!("recreate 至少需要一张图片");
            }
            for img in &images {
                report.push(run_recreate(&client, img, writer.dir()));
            }
        }
        Cmd::Report { images } => {
            if images.is_empty() {
                anyhow::bail!("report 至少需要一张图片");
            }
            return run_compare_report(&client, &images, &writer);
        }
        Cmd::All => {
            let images = default_images()?;
            // 1. 单图描述：三张图 base64 路径
            for img in &images {
                report.push(describe_one(&client, img, "base64", writer.dir()));
            }
            // 2. 01.jpg Files API 路径（用户指定的同步对照）
            report.push(describe_one(&client, &images[0], "file-api", writer.dir()));
            // 3. OCR
            report.push(run_single(
                &client,
                &images[0],
                "ocr",
                prompts::OCR,
                "base64",
                writer.dir(),
            ));
            // 4. 多图对比
            report.push(run_multi(
                &client,
                &images,
                "compare",
                prompts::COMPARE,
                writer.dir(),
            ));
            // 5. 创作
            report.push(run_single(
                &client,
                &images[0],
                "create",
                prompts::CREATE,
                "base64",
                writer.dir(),
            ));
            // 6. 多模态生成探测
            report.push(run_gen_probe(&client, Some(&images[0]), writer.dir()));
            // 7. 每张图基于理解还原（SVG）
            for img in &images {
                report.push(run_recreate(&client, img, writer.dir()));
            }
        }
    }

    let md_path = writer.write(&report)?;
    println!("✅ 报告已生成: {}", md_path.display());
    println!(
        "📊 成功 {}/{} 项任务",
        report.results.iter().filter(|r| r.status == "ok").count(),
        report.results.len()
    );
    for r in &report.results {
        let status = if r.status == "ok" { "✅" } else { "❌" };
        println!("  {status} {}", r.label);
    }
    Ok(())
}

/// 单图描述：base64 或 file-api 两种路径。
fn describe_one(
    client: &DeepSeekClient,
    img: &Path,
    via: &str,
    report_dir: &std::path::Path,
) -> TaskResult {
    let start = std::time::Instant::now();
    let name = img
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    match describe_impl(client, img, via) {
        Ok((output, usage)) => TaskResult::ok(
            "describe",
            format!("describe-{name} ({via})"),
            vec![rel_to_report(img, report_dir)],
            output,
            usage,
        )
        .with_duration(elapsed_ms(&start)),
        Err(e) => TaskResult::fail(
            "describe",
            format!("describe-{name} ({via})"),
            vec![rel_to_report(img, report_dir)],
            e.to_string(),
        )
        .with_duration(elapsed_ms(&start)),
    }
}

fn describe_impl(
    client: &DeepSeekClient,
    img: &Path,
    via: &str,
) -> anyhow::Result<(String, Option<Usage>)> {
    let inputs = match via {
        "file-api" | "files" => {
            let file = client.upload_file(img)?;
            vec![ImageInput::FileId { file_id: file.id }]
        }
        "base64" => vec![ImageInput::DataUrl {
            data_url: file_to_data_url(img)?,
        }],
        other => anyhow::bail!("未知传图方式 `{other}`（支持 base64 / file-api）"),
    };
    let resp = client.chat_with_images(prompts::DESCRIBE, &inputs, Some(8000), false)?;
    Ok((
        resp.text().unwrap_or("（无文本输出）").to_string(),
        Some(resp.usage),
    ))
}

/// 单图单提示词场景（ocr / create 等）。
fn run_single(
    client: &DeepSeekClient,
    img: &Path,
    task: &str,
    prompt: &str,
    via: &str,
    report_dir: &std::path::Path,
) -> TaskResult {
    let start = std::time::Instant::now();
    let name = img
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let inputs = match via {
        "base64" => match file_to_data_url(img) {
            Ok(url) => vec![ImageInput::DataUrl { data_url: url }],
            Err(e) => {
                return TaskResult::fail(
                    task,
                    format!("{task}-{name}"),
                    vec![rel_to_report(img, report_dir)],
                    e.to_string(),
                )
                .with_duration(elapsed_ms(&start));
            }
        },
        _ => unreachable!("run_single 仅支持 base64"),
    };
    let max_tokens = if task == "create" { 12000 } else { 8000 };
    match client.chat_with_images(prompt, &inputs, Some(max_tokens), false) {
        Ok(resp) => TaskResult::ok(
            task,
            format!("{task}-{name}"),
            vec![rel_to_report(img, report_dir)],
            resp.text().unwrap_or("（无文本输出）").to_string(),
            Some(resp.usage),
        )
        .with_duration(elapsed_ms(&start)),
        Err(e) => TaskResult::fail(
            task,
            format!("{task}-{name}"),
            vec![rel_to_report(img, report_dir)],
            e.to_string(),
        )
        .with_duration(elapsed_ms(&start)),
    }
}

/// 多图一次请求。
fn run_multi(
    client: &DeepSeekClient,
    imgs: &[PathBuf],
    task: &str,
    prompt: &str,
    report_dir: &std::path::Path,
) -> TaskResult {
    let mut inputs = Vec::new();
    let mut rels = Vec::new();
    for img in imgs {
        match file_to_data_url(img) {
            Ok(url) => {
                inputs.push(ImageInput::DataUrl { data_url: url });
                rels.push(rel_to_report(img, report_dir));
            }
            Err(e) => {
                return TaskResult::fail(task, format!("{task}-multi"), rels, e.to_string());
            }
        }
    }
    let label = format!("{task}-{}图", imgs.len());
    let start = std::time::Instant::now();
    match client.chat_with_images(prompt, &inputs, Some(8000), false) {
        Ok(resp) => TaskResult::ok(
            task,
            label,
            rels,
            resp.text().unwrap_or("（无文本输出）").to_string(),
            Some(resp.usage),
        )
        .with_duration(elapsed_ms(&start)),
        Err(e) => {
            TaskResult::fail(task, label, rels, e.to_string()).with_duration(elapsed_ms(&start))
        }
    }
}

/// 多模态生成探测：四连问 + 能力边界总结，结果聚合到 extra。
fn run_gen_probe(
    client: &DeepSeekClient,
    img: Option<&Path>,
    report_dir: &std::path::Path,
) -> TaskResult {
    let start = std::time::Instant::now();
    let mut rounds = Vec::new();
    let mut label = "gen-probe".to_string();
    let mut rels = Vec::new();
    let mut usage_sum: Option<Usage> = None;

    if let Some(img) = img {
        label.push_str("-with-image");
        rels.push(rel_to_report(img, report_dir));
    }

    for (name, prompt) in prompts::gen_probe_prompts() {
        let inputs = match img {
            Some(img) => match file_to_data_url(img) {
                Ok(url) => vec![ImageInput::DataUrl { data_url: url }],
                Err(e) => {
                    rounds.push(serde_json::json!({"probe": name, "status": "error", "error": e.to_string()}));
                    continue;
                }
            },
            None => Vec::new(),
        };
        match client.chat_with_images(&prompt, &inputs, Some(8000), true) {
            Ok(resp) => {
                usage_sum = Some(resp.usage);
                rounds.push(serde_json::json!({
                    "probe": name,
                    "status": "ok",
                    "completion_tokens": resp.usage.completion_tokens,
                    "output": resp.text().unwrap_or("（无文本输出）"),
                }));
            }
            Err(e) => {
                rounds.push(
                    serde_json::json!({"probe": name, "status": "error", "error": e.to_string()}),
                );
            }
        }
    }

    // 收尾：能力边界自述（无图）
    let summary =
        match client.chat_with_images(&prompts::gen_probe_summary(), &[], Some(2000), true) {
            Ok(resp) => {
                usage_sum = Some(resp.usage);
                resp.text().unwrap_or("（无文本输出）").to_string()
            }
            Err(e) => format!("（总结请求失败）{e}"),
        };

    // 探测结论判定：
    // - media_output：模型直接产出图片数据（data:image/ 或带图片扩展名的 URL）
    //   → 真正的多模态输出。启发式必须排除两种假阳性：
    //   a) SVG 代码的 xmlns="http://..."（不含图片扩展名，已被扩展名匹配排除）；
    //   b) 拒绝回答中引用占位示例（如 "https://.../image.png"）——拒绝语出现即不算产出。
    // - code_output：模型用代码媒介创作（SVG / ASCII 画）→ 文本输出但具备"创作"能力
    // - 二者皆无 → 纯文本理解型
    let is_refusal = |out: &str| {
        out.contains("无法")
            || out.contains("抱歉")
            || out.contains("不能")
            || out.contains("不具备")
    };
    let media_output = rounds.iter().any(|r| {
        let out = r.get("output").and_then(|o| o.as_str()).unwrap_or("");
        if is_refusal(out) || out.contains(".../") {
            return false;
        }
        out.contains("data:image/")
            || out.contains(".png")
            || out.contains(".jpg")
            || out.contains(".jpeg")
            || out.contains(".webp")
            || out.contains(".gif")
    });
    let code_output = rounds.iter().any(|r| {
        let out = r.get("output").and_then(|o| o.as_str()).unwrap_or("");
        out.contains("<svg")
            || out.contains("```svg")
            || out.contains("```ascii")
            || out.contains("```txt")
    });

    let conclusion = if media_output {
        "检测到图片数据输出：模型具备直接生成图片的能力（多模态输出）"
    } else if code_output {
        "未检测到图片文件输出（纯文本输出模型），但能以 SVG/ASCII 等代码媒介进行图像类创作"
    } else {
        "未检测到任何图像类输出：模型为纯文本输出的图像理解模型"
    };

    TaskResult::ok("gen-probe", label, rels, summary, usage_sum)
        .with_extra(serde_json::json!({
            "rounds": rounds,
            "media_output": media_output,
            "code_output": code_output,
            "conclusion": conclusion,
        }))
        .with_duration(elapsed_ms(&start))
}

/// 基于理解还原：每张图产出一张 SVG 还原图，保存到报告 assets/。
///
/// 关闭思考模式（保证 content 全部是 SVG 代码）；输出不完整（未以 </svg>
/// 闭合）时自动用简化版提示词重试，最多 3 次，确保每张图都被还原。
fn run_recreate(client: &DeepSeekClient, img: &Path, report_dir: &std::path::Path) -> TaskResult {
    let start = std::time::Instant::now();
    let file_name = img
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let stem = img
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "image".to_string());
    let rel = rel_to_report(img, report_dir);

    let inputs = match file_to_data_url(img) {
        Ok(url) => vec![ImageInput::DataUrl { data_url: url }],
        Err(e) => {
            return TaskResult::fail(
                "recreate",
                format!("recreate-{file_name}"),
                vec![rel],
                e.to_string(),
            )
            .with_duration(elapsed_ms(&start));
        }
    };

    let assets = report_dir.join("assets");
    if let Err(e) = std::fs::create_dir_all(&assets) {
        return TaskResult::fail(
            "recreate",
            format!("recreate-{file_name}"),
            vec![rel],
            format!("创建 assets 失败: {e}"),
        )
        .with_duration(elapsed_ms(&start));
    }

    let mut attempts: Vec<serde_json::Value> = Vec::new();
    let mut last_reason = String::from("未知错误");

    for attempt in 0..5 {
        let prompt = if attempt == 0 {
            prompts::RECREATE
        } else {
            prompts::RECREATE_RETRY
        };
        match client.chat_with_images(prompt, &inputs, Some(12000), true) {
            Ok(resp) => {
                let raw = resp.text().unwrap_or("");
                let svg = extract_svg(raw);
                let complete = svg.trim_end().ends_with("</svg>");
                attempts.push(serde_json::json!({
                    "attempt": attempt + 1,
                    "chars": svg.len(),
                    "complete": complete,
                    "completion_tokens": resp.usage.completion_tokens,
                }));
                // 成功条件：非空 + </svg> 闭合 + xmllint 校验合法 + PNG 渲染有效
                if !svg.is_empty() && complete {
                    let svg_name = format!("recreated_{stem}.svg");
                    let svg_path = assets.join(&svg_name);
                    if let Err(e) = std::fs::write(&svg_path, &svg) {
                        return TaskResult::fail(
                            "recreate",
                            format!("recreate-{file_name}"),
                            vec![rel],
                            format!("保存 SVG 失败: {e}"),
                        )
                        .with_duration(elapsed_ms(&start));
                    }
                    if !svg_is_valid(&svg_path) {
                        last_reason = format!(
                            "第 {} 次 SVG 不是合法 XML（已保存但渲染会失败，重试）",
                            attempt + 1
                        );
                        continue;
                    }
                    let png_path = assets.join(format!("{svg_name}.png"));
                    if svg_to_png(&svg_path, &assets).is_err() || !png_is_valid(&png_path) {
                        last_reason =
                            format!("第 {} 次 SVG 合法但 PNG 渲染无效（重试）", attempt + 1);
                        continue;
                    }
                    let desc = raw.replace(&svg, "").trim().to_string();
                    let output = format!(
                        "{desc}\n\n[SVG 还原图] `assets/{svg_name}`（{} 字符，XML 合法 + PNG 渲染验证通过，第 {} 次尝试成功）",
                        svg.len(),
                        attempt + 1
                    );
                    return TaskResult::ok(
                        "recreate",
                        format!("recreate-{file_name}"),
                        vec![rel],
                        output,
                        Some(resp.usage),
                    )
                    .with_extra(serde_json::json!({
                        "svg_file": format!("assets/{svg_name}"),
                        "svg_png": format!("assets/{svg_name}.png"),
                        "svg_chars": svg.len(),
                        "complete": true,
                        "xml_valid": true,
                        "attempts": attempts,
                    }))
                    .with_duration(elapsed_ms(&start));
                }
                last_reason = if svg.is_empty() {
                    format!("第 {} 次输出为空（无 SVG 代码）", attempt + 1)
                } else {
                    format!(
                        "第 {} 次输出被截断（{} 字符，未以 </svg> 闭合）",
                        attempt + 1,
                        svg.len()
                    )
                };
            }
            Err(e) => {
                attempts.push(serde_json::json!({"attempt": attempt + 1, "error": e.to_string()}));
                last_reason = format!("第 {} 次请求失败: {e}", attempt + 1);
            }
        }
    }

    TaskResult::fail(
        "recreate",
        format!("recreate-{file_name}"),
        vec![rel],
        last_reason,
    )
    .with_extra(serde_json::json!({"attempts": attempts}))
    .with_duration(elapsed_ms(&start))
}

/// 从模型输出中提取 SVG 代码（优先 ```svg 围栏，其次裸 <svg>...</svg>）。
///
/// 必须裁剪到真正的 `<svg` 起点：实测模型输出常在围栏后带垃圾前缀
/// （如 `g\n<svg...>`），不裁剪会导致 SVG 不是合法 XML、渲染失败。
fn extract_svg(raw: &str) -> String {
    let mut candidate = String::new();
    if let Some(start) = raw.find("```svg") {
        let body = &raw[start + 5..];
        if let Some(end) = body.find("```") {
            candidate = body[..end].to_string();
        }
    }
    if candidate.is_empty() {
        candidate = raw.to_string();
    }
    // 裁剪：从第一个 <svg 开始，到最后一个 </svg> 结束
    if let Some(s) = candidate.find("<svg") {
        if let Some(e) = candidate[s..].rfind("</svg>") {
            return candidate[s..s + e + 6].trim().to_string();
        }
        return candidate[s..].trim().to_string();
    }
    candidate.trim().to_string()
}

/// 用 xmllint（macOS 自带 libxml2）校验 SVG 是否为合法 XML。
fn svg_is_valid(path: &Path) -> bool {
    let out = std::process::Command::new("xmllint")
        .arg("--noout")
        .arg(path)
        .output();
    match out {
        Ok(o) => o.status.success(),
        Err(_) => false,
    }
}

/// 校验 PNG 是否有效可渲染（非空 + sips 可读取尺寸）。
fn png_is_valid(path: &Path) -> bool {
    let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    if size < 256 {
        return false;
    }
    let out = std::process::Command::new("sips")
        .args(["-g", "pixelWidth"])
        .arg(path)
        .output();
    matches!(out, Ok(o) if o.status.success())
}

/// 三列对比报告流水线：每张图 = 原图缩略图 | 模型理解 | 文本驱动的还原图。
fn run_compare_report(
    client: &DeepSeekClient,
    imgs: &[PathBuf],
    writer: &ReportWriter,
) -> anyhow::Result<()> {
    let mut report = CompareReport::new(now_label(), client.model().to_string());
    for img in imgs {
        match build_compare_row(client, img, writer.dir()) {
            Ok(row) => {
                println!(
                    "  ✅ {} （理解 {:.1}s → 还原 {:.1}s）",
                    row.title,
                    row.duration_understanding_ms as f64 / 1000.0,
                    row.duration_recreate_ms as f64 / 1000.0
                );
                report.push(row);
            }
            Err(e) => {
                eprintln!("  ❌ {} 处理失败: {e}", img.display());
            }
        }
    }
    let md_path = writer.write_compare(&report)?;
    println!("✅ 三列对比报告已生成: {}", md_path.display());
    Ok(())
}

/// 一行对比数据：理解（带原图）→ 缩略图 → 文本驱动还原（不带图）→ SVG→PNG。
fn build_compare_row(
    client: &DeepSeekClient,
    img: &Path,
    report_dir: &std::path::Path,
) -> anyhow::Result<CompareRow> {
    let file_name = img
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let stem = img
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "image".to_string());
    let assets = report_dir.join("assets");
    std::fs::create_dir_all(&assets)?;

    // 1. 理解：带原图 → 文本描述
    let t0 = std::time::Instant::now();
    let inputs = vec![ImageInput::DataUrl {
        data_url: file_to_data_url(img)?,
    }];
    let resp_u = client.chat_with_images(prompts::DESCRIBE, &inputs, Some(8000), false)?;
    let understanding = resp_u.text().unwrap_or("（无文本输出）").to_string();
    let usage_u = Some(resp_u.usage);
    let dur_u = elapsed_ms(&t0);

    // 2. 原图缩略图（对比用，控制报告体积）
    let thumb_path = make_thumbnail(img, &assets)?;
    let original_rel = format!(
        "assets/{}",
        thumb_path.file_name().unwrap().to_string_lossy()
    );

    // 3. 文本驱动还原：理解输出 → 再次调用视觉模型（不带原图）→ SVG
    let (svg_name, dur_r, usage_r, attempts) =
        recreate_from_text(client, &understanding, &assets, &stem)?;

    // 4. SVG → PNG（qlmanage 渲染）
    let png_name = format!("{svg_name}.png");
    svg_to_png(&assets.join(&svg_name), &assets)?;

    Ok(CompareRow {
        title: file_name,
        original_rel,
        understanding,
        recreated_png_rel: format!("assets/{png_name}"),
        recreated_svg_rel: format!("assets/{svg_name}"),
        usage_understanding: usage_u,
        usage_recreate: usage_r,
        duration_understanding_ms: dur_u,
        duration_recreate_ms: dur_r,
        recreate_attempts: attempts,
    })
}

/// 文本驱动还原：仅凭理解文本调用视觉模型生成 SVG（无图输入），
/// 未闭合 / 非合法 XML / PNG 渲染无效时重试，最多 5 次。
fn recreate_from_text(
    client: &DeepSeekClient,
    desc: &str,
    assets: &Path,
    stem: &str,
) -> anyhow::Result<(String, u64, Option<Usage>, u32)> {
    let start = std::time::Instant::now();
    let mut attempts = 0u32;
    for attempt in 0..5 {
        attempts += 1;
        let prompt = if attempt == 0 {
            prompts::recreate_from_text_prompt()
                .replace(prompts::TEXT_DESCRIPTION_PLACEHOLDER, desc)
        } else {
            prompts::recreate_from_text_retry_prompt()
                .replace(prompts::TEXT_DESCRIPTION_PLACEHOLDER, desc)
        };
        let resp = client.chat_with_images(&prompt, &[], Some(12000), true)?;
        let svg = extract_svg(resp.text().unwrap_or(""));
        if svg.is_empty() || !svg.trim_end().ends_with("</svg>") {
            continue;
        }
        let svg_name = format!("recreated_from_text_{stem}.svg");
        let svg_path = assets.join(&svg_name);
        std::fs::write(&svg_path, &svg)?;
        if !svg_is_valid(&svg_path) {
            continue;
        }
        let png_path = assets.join(format!("{svg_name}.png"));
        if svg_to_png(&svg_path, assets).is_err() || !png_is_valid(&png_path) {
            continue;
        }
        return Ok((svg_name, elapsed_ms(&start), Some(resp.usage), attempts));
    }
    anyhow::bail!("文本驱动还原失败：5 次尝试均未产出合法 SVG（未闭合/非法 XML/渲染失败）")
}

/// 用 sips 生成 PNG 缩略图（最长边 480px）。
fn make_thumbnail(src: &Path, out_dir: &Path) -> anyhow::Result<PathBuf> {
    let stem = src
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "image".to_string());
    let out = out_dir.join(format!("original_{stem}.png"));
    let status = std::process::Command::new("sips")
        .args(["-s", "format", "png", "-Z", "480"])
        .arg(src)
        .arg("--out")
        .arg(&out)
        .status()
        .map_err(|e| anyhow::anyhow!("调用 sips 失败: {e}"))?;
    if !status.success() {
        anyhow::bail!("sips 缩略图失败: {}", src.display());
    }
    Ok(out)
}

/// 用 qlmanage 将 SVG 渲染为 PNG（macOS QuickLook）。
fn svg_to_png(svg_path: &Path, out_dir: &Path) -> anyhow::Result<PathBuf> {
    let status = std::process::Command::new("qlmanage")
        .arg("-t")
        .arg("-s")
        .arg("800")
        .arg("-o")
        .arg(out_dir)
        .arg(svg_path)
        .status()
        .map_err(|e| anyhow::anyhow!("调用 qlmanage 失败: {e}"))?;
    if !status.success() {
        anyhow::bail!("qlmanage 渲染失败: {}", svg_path.display());
    }
    let png = out_dir.join(format!(
        "{}.png",
        svg_path.file_name().unwrap().to_string_lossy()
    ));
    if !png.exists() || !png_is_valid(&png) {
        anyhow::bail!("qlmanage 渲染无效: {}", png.display());
    }
    Ok(png)
}

/// 毫秒耗时（性能基准）。
fn elapsed_ms(start: &std::time::Instant) -> u64 {
    start.elapsed().as_millis() as u64
}

/// 默认测试图片：testimages 下的三张图。
fn default_images() -> anyhow::Result<Vec<PathBuf>> {
    let dir = PathBuf::from("testimages");
    let mut imgs: Vec<PathBuf> = std::fs::read_dir(&dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.extension()
                .map(|x| matches!(x.to_str(), Some("jpg" | "jpeg" | "png" | "gif" | "webp")))
                .unwrap_or(false)
        })
        .collect();
    imgs.sort();
    if imgs.is_empty() {
        anyhow::bail!("testimages/ 下没有找到图片");
    }
    Ok(imgs)
}

/// 相对报告目录的图片路径（Markdown 可读）。
fn rel_to_report(img: &std::path::Path, report_dir: &std::path::Path) -> String {
    let rel = img
        .strip_prefix(report_dir.parent().unwrap_or(std::path::Path::new(".")))
        .unwrap_or(img);
    rel.to_string_lossy().into_owned()
}

/// 本地时间标签（与报告目录命名一致）。
fn now_label() -> String {
    ds_vision::reporter::now_label()
}
