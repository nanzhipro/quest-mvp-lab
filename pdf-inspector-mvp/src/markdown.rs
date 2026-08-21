//! Markdown conversion: headings, lists, code blocks, image placeholders.

use crate::types::{ImageItem, PdfLine};

/// Options controlling markdown output.
#[derive(Debug, Clone, Copy)]
pub struct MarkdownOptions {
    /// Emit `---` page separators between pages.
    pub include_page_breaks: bool,
}

impl Default for MarkdownOptions {
    fn default() -> Self {
        Self {
            include_page_breaks: true,
        }
    }
}

/// One page's worth of ordered content for conversion.
pub struct PageContent<'a> {
    /// Lines in reading order, running heads already stripped.
    pub lines: Vec<PdfLine>,
    /// Embedded images of the page.
    pub images: &'a [ImageItem],
    /// Printed page number (for markers), if known.
    pub printed_page: Option<u32>,
    /// PDF page height in points.
    pub page_h: f32,
}

/// Heading level thresholds relative to the body font size.
const H1_RATIO: f32 = 1.35;
const H2_RATIO: f32 = 1.15;
const H3_RATIO: f32 = 1.07;

/// Convert per-page content into one Markdown document.
pub fn to_markdown(pages: &[PageContent<'_>], options: &MarkdownOptions) -> String {
    let body_size = median_body_size(pages);
    let mut out = String::new();

    for (i, page) in pages.iter().enumerate() {
        if options.include_page_breaks && i > 0 {
            out.push_str("\n\n---\n\n");
        }
        if let Some(p) = page.printed_page {
            out.push_str(&format!("<!-- page {p} -->\n"));
        }
        let events = build_events(page, body_size);
        for ev in events {
            out.push_str(&ev);
        }
    }
    out.trim().to_string() + "\n"
}

/// Body font size of the document.
///
/// The body size is the *mode* of the line sizes (ties resolve to the
/// smaller size): body text is the most common size, while headings,
/// captions, running heads, and footnotes are outliers. Falls back to the
/// median for empty/odd distributions.
fn median_body_size(pages: &[PageContent<'_>]) -> f32 {
    let mut sizes: Vec<f32> = pages
        .iter()
        .flat_map(|p| p.lines.iter().map(|l| l.size))
        .collect();
    if sizes.is_empty() {
        return 10.0;
    }
    sizes.sort_by(|a, b| a.total_cmp(b));
    let mut best_size: Option<f32> = None;
    let mut best_support = 0usize;
    let mut i = 0;
    while i < sizes.len() {
        let size = sizes[i];
        let mut j = i;
        while j < sizes.len() && (sizes[j] - size).abs() < 0.5 {
            j += 1;
        }
        let support = j - i;
        if support > best_support || (support == best_support && best_size.is_none()) {
            best_size = Some(size);
            best_support = support;
        }
        i = j;
    }
    match best_size {
        Some(size) => size,
        None => {
            let mid = sizes.len() / 2;
            if sizes.len().is_multiple_of(2) {
                (sizes[mid - 1] + sizes[mid]) / 2.0
            } else {
                sizes[mid]
            }
        }
    }
}

/// Build the markdown event stream for one page (y-descending order).
fn build_events(page: &PageContent<'_>, body_size: f32) -> Vec<String> {
    enum Event<'a> {
        Line(&'a PdfLine),
        Image(&'a ImageItem, String), // image + caption
    }

    let mut events: Vec<Event> = Vec::new();
    for img in page.images {
        let caption = page
            .lines
            .iter()
            .find(|l| {
                let near = (l.y - img.y).abs() < 60.0;
                near && is_caption(l)
            })
            .map(|l| l.text.clone())
            .unwrap_or_else(|| format!("page {} image {}", img.page, img.xref));
        events.push(Event::Image(img, caption));
    }
    for line in &page.lines {
        events.push(Event::Line(line));
    }
    events.sort_by(|a, b| {
        let (ya, xa) = match a {
            Event::Line(l) => (l.y, l.x0),
            Event::Image(i, _) => (i.y + i.h, i.x), // anchor images at their top
        };
        let (yb, xb) = match b {
            Event::Line(l) => (l.y, l.x0),
            Event::Image(i, _) => (i.y + i.h, i.x),
        };
        yb.total_cmp(&ya).then(xa.total_cmp(&xb))
    });

    let mut out = Vec::new();
    let mut para: Vec<String> = Vec::new();
    let mut code_block: Option<Vec<String>> = None;
    let mut last_y: Option<f32> = None;

    for ev in events {
        match ev {
            Event::Image(img, caption) => {
                flush_para(&mut out, &mut para);
                out.push(format!(
                    "\n![{}](assets/page-{}-{}.{})\n",
                    caption
                        .replace('|', "\\|")
                        .replace('(', "\\(")
                        .replace(')', "\\)"),
                    img.page,
                    img.xref,
                    img.ext()
                ));
                last_y = None;
            }
            Event::Line(line) => {
                if line.monospace {
                    flush_para(&mut out, &mut para);
                    code_block
                        .get_or_insert_with(Vec::new)
                        .push(line.text.clone());
                    last_y = Some(line.y);
                    continue;
                }
                if let Some(mut block) = code_block.take() {
                    block.insert(0, "```".into());
                    block.push("```".into());
                    out.push("\n".to_string());
                    out.extend(block.drain(..).map(|l| l + "\n"));
                    out.push("\n".to_string());
                }
                let md = classify_line(line, body_size);
                match md {
                    MdKind::Heading(level, text) => {
                        flush_para(&mut out, &mut para);
                        out.push(format!("\n{} {}\n", "#".repeat(level as usize), text));
                        last_y = None;
                    }
                    MdKind::List(text) => {
                        flush_para(&mut out, &mut para);
                        out.push(format!("- {}\n", text));
                        last_y = None;
                    }
                    MdKind::Paragraph(text) => {
                        let gap_ok = last_y
                            .map(|y| (y - line.y).abs() < line.size * 1.6)
                            .unwrap_or(false);
                        if !para.is_empty() && !gap_ok {
                            flush_para(&mut out, &mut para);
                        }
                        para.push(text);
                        last_y = Some(line.y);
                    }
                }
            }
        }
    }
    flush_para(&mut out, &mut para);
    if let Some(mut block) = code_block.take() {
        block.insert(0, "```".into());
        block.push("```".into());
        out.push("\n".to_string());
        out.extend(block.drain(..).map(|l| l + "\n"));
    }
    out
}

enum MdKind {
    Heading(u8, String),
    List(String),
    Paragraph(String),
}

fn classify_line(line: &PdfLine, body_size: f32) -> MdKind {
    let text = line.text.trim().to_string();
    if text.is_empty() {
        return MdKind::Paragraph(text);
    }
    let ratio = line.size / body_size.max(1.0);
    let level = if ratio >= H1_RATIO {
        Some(1)
    } else if ratio >= H2_RATIO {
        Some(2)
    } else if ratio >= H3_RATIO && line.text.ends_with(':') {
        Some(3)
    } else {
        None
    };
    if let Some(level) = level {
        // Skip headings that are really list items or captions.
        if !is_caption_line(&text) {
            return MdKind::Heading(level, text);
        }
    }
    if is_list_item(&text) {
        return MdKind::List(
            text.trim_start_matches(['•', '◦', '‣', '–', '-', '*', '·', '▪'])
                .trim()
                .to_string(),
        );
    }
    MdKind::Paragraph(text)
}

fn is_caption_line(text: &str) -> bool {
    ["Figure", "Listing", "Table", "Code", "Example", "Exhibit"]
        .iter()
        .any(|k| {
            text.starts_with(k)
                && text[7.min(text.len())..]
                    .chars()
                    .next()
                    .is_some_and(|c| c.is_ascii_digit() || c == ' ')
        })
}

fn is_caption(l: &PdfLine) -> bool {
    let t = l.text.trim_start();
    ["Figure", "Listing", "Table", "Code", "Example"]
        .iter()
        .any(|k| t.starts_with(k))
}

fn is_list_item(text: &str) -> bool {
    let t = text.trim_start();
    for bullet in ['•', '◦', '‣', '–', '-', '*', '·', '▪'] {
        if let Some(rest) = t.strip_prefix(bullet) {
            return !rest.is_empty() && rest.starts_with(' ');
        }
    }
    // Numbered items: digits followed by ". " or ") ".
    let mut chars = t.chars();
    let mut digits = 0usize;
    for c in chars.by_ref() {
        if c.is_ascii_digit() {
            digits += 1;
        } else {
            break;
        }
    }
    digits > 0 && chars.as_str().starts_with(". ")
}

fn flush_para(out: &mut Vec<String>, para: &mut Vec<String>) {
    if para.is_empty() {
        return;
    }
    out.push("\n".to_string());
    out.push(para.join(" "));
    out.push("\n".to_string());
    para.clear();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(text: &str, size: f32, mono: bool) -> PdfLine {
        PdfLine {
            text: text.into(),
            x0: 72.0,
            x1: 300.0,
            y: 400.0,
            size,
            font: "Helvetica".into(),
            page: 1,
            monospace: mono,
        }
    }

    fn page(lines: Vec<PdfLine>) -> Vec<PageContent<'static>> {
        vec![PageContent {
            lines,
            images: &[],
            printed_page: Some(257),
            page_h: 792.0,
        }]
    }

    #[test]
    fn headings_by_ratio() {
        let md = to_markdown(
            &page(vec![
                line("Chapter 11", 22.0, false),
                line("Tool Design", 12.0, false),
                line("Body text.", 9.0, false),
            ]),
            &MarkdownOptions::default(),
        );
        assert!(md.contains("# Chapter 11"), "{md}");
        assert!(md.contains("## Tool Design"), "{md}");
        assert!(md.contains("Body text."), "{md}");
    }

    #[test]
    fn lists_and_code() {
        let md = to_markdown(
            &page(vec![
                line("• first item", 9.0, false),
                line("• second item", 9.0, false),
                line("let x = 1;", 9.0, true),
                line("print(x)", 9.0, true),
            ]),
            &MarkdownOptions::default(),
        );
        assert!(md.contains("- first item"), "{md}");
        assert!(md.contains("- second item"), "{md}");
        assert!(md.contains("```"), "{md}");
        assert!(md.contains("let x = 1;"), "{md}");
    }

    #[test]
    fn caption_not_heading() {
        let md = to_markdown(
            &page(vec![
                line("Figure 11-2: A BlockBlock alert", 7.0, false),
                line("Body text", 9.0, false),
            ]),
            &MarkdownOptions::default(),
        );
        assert!(!md.contains("# Figure"), "{md}");
        assert!(md.contains("Figure 11-2: A BlockBlock alert"), "{md}");
    }
}
