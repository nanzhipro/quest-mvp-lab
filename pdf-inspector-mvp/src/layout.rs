//! Layout: group glyph runs into lines and reconstruct reading order.

use crate::types::{PdfLine, TextItem};

/// Baseline tolerance for grouping items into one line (fraction of size).
const BASELINE_TOL: f32 = 0.35;
/// Minimum horizontal gap (× line size) that synthesizes a space.
const SPACE_GAP: f32 = 0.3;
/// Column split gap: columns whose x-ranges never overlap by more than this
/// fraction of the page width are treated as separate columns.
const COLUMN_GAP: f32 = 0.06;

/// Group positioned text items into lines, one page at a time.
pub fn group_into_lines(items: &[TextItem]) -> Vec<PdfLine> {
    let mut by_page: Vec<Vec<&TextItem>> = Vec::new();
    for it in items {
        while by_page.len() < it.page as usize {
            by_page.push(Vec::new());
        }
        by_page[(it.page - 1) as usize].push(it);
    }
    let mut lines = Vec::new();
    for page_items in by_page {
        lines.extend(group_page_lines(&page_items));
    }
    lines
}

/// Group the items of a single page into lines.
///
/// Clusters are produced in *content order* so the reading-order stage can
/// detect pages whose coordinate system is y-flipped (producers that draw
/// with a top-left origin).
fn group_page_lines(items: &[&TextItem]) -> Vec<PdfLine> {
    let mut clusters: Vec<Vec<&TextItem>> = Vec::new();
    'outer: for it in items {
        // Tolerance scales with the *smaller* size so giant display glyphs
        // (e.g. a 120pt chapter numeral) do not swallow nearby body lines.
        let tol = match clusters.last() {
            Some(cluster) => {
                let base = it.size.min(cluster[0].size);
                (base * BASELINE_TOL).max(2.0)
            }
            None => (it.size * BASELINE_TOL).max(2.0),
        };
        for cluster in clusters.iter_mut() {
            if (cluster[0].y - it.y).abs() <= tol {
                cluster.push(it);
                continue 'outer;
            }
        }
        clusters.push(vec![*it]);
    }

    let mut lines = Vec::with_capacity(clusters.len());
    for mut cluster in clusters {
        cluster.sort_by(|a, b| a.x.total_cmp(&b.x));
        let page = cluster[0].page;
        let x0 = cluster[0].x;
        let size = cluster.iter().map(|i| i.size).fold(0.0f32, f32::max);
        let y = cluster[0].y;
        let mut text = String::new();
        let mut prev_right = x0;
        let mut x1 = x0;
        let mut mono_count = 0usize;
        let mut font = cluster[0].font.clone();
        for it in &cluster {
            if it.font.contains("Mono") || it.font.to_uppercase().contains("COURIER") {
                mono_count += 1;
            }
            if !text.is_empty() {
                let gap = it.x - prev_right;
                if gap > SPACE_GAP * size {
                    text.push(' ');
                }
            } else {
                font = it.font.clone();
            }
            text.push_str(&it.text);
            prev_right = it.x + it.width;
            x1 = prev_right;
        }
        lines.push(PdfLine {
            text,
            x0,
            x1,
            y,
            size,
            font,
            page,
            monospace: mono_count * 2 > cluster.len(),
        });
    }
    lines
}

/// True when a page's content is drawn with a top-left origin (y flipped).
///
/// In bottom-left PDF coordinates the first content lines (visually the top
/// of the page) carry the *largest* y; flipped pages start with the smallest.
fn page_is_flipped(lines: &[PdfLine]) -> bool {
    if lines.len() < 2 {
        return false;
    }
    let n = lines.len();
    let (head, tail) = if n <= 8 {
        (lines[0].y, lines[n - 1].y)
    } else {
        let q = n / 4;
        let head = lines[..q].iter().map(|l| l.y).sum::<f32>() / q as f32;
        let tail = lines[n - q..].iter().map(|l| l.y).sum::<f32>() / q as f32;
        (head, tail)
    };
    head < tail
}

/// Reorder lines into reading order: left→right columns, then top→bottom.
///
/// Column detection is gap-based: lines are clustered into x-overlapping
/// columns; a page is multi-column only when columns interleave vertically.
pub fn reading_order(lines: Vec<PdfLine>) -> Vec<PdfLine> {
    let mut by_page: Vec<Vec<PdfLine>> = Vec::new();
    for line in lines {
        while by_page.len() < line.page as usize {
            by_page.push(Vec::new());
        }
        by_page[(line.page - 1) as usize].push(line);
    }
    let mut out = Vec::new();
    for page_lines in by_page {
        out.extend(order_page(page_lines));
    }
    out
}

fn order_page(lines: Vec<PdfLine>) -> Vec<PdfLine> {
    let flipped = page_is_flipped(&lines);
    if lines.len() < 3 {
        return sort_top_down(lines, flipped);
    }
    let page_w = lines.iter().map(|l| l.x1).fold(0.0f32, f32::max);
    let columns = cluster_columns(&lines, page_w);
    if columns.len() < 2 {
        return sort_top_down(lines, flipped);
    }
    // Multi-column only when lines from different columns interleave in y.
    let mut prev_col = None;
    let mut switches = 0usize;
    let mut ordered: Vec<&PdfLine> = lines.iter().collect();
    ordered.sort_by(|a, b| sort_y(a, b, flipped).then(a.x0.total_cmp(&b.x0)));
    for line in &ordered {
        let col = column_of(&columns, line);
        if let Some(p) = prev_col {
            if p != col {
                switches += 1;
            }
        }
        prev_col = Some(col);
    }
    if switches < 2 {
        return sort_top_down(lines, flipped);
    }
    // Emit column by column (left→right), each top→bottom.
    let mut out = Vec::with_capacity(lines.len());
    let mut sorted_cols = columns.clone();
    sorted_cols.sort_by(|a, b| a.x0.total_cmp(&b.x0));
    for col in &sorted_cols {
        let mut col_lines: Vec<&PdfLine> = lines
            .iter()
            .filter(|l| column_of(&columns, l) == col.idx)
            .collect();
        col_lines.sort_by(|a, b| sort_y(a, b, flipped).then(a.x0.total_cmp(&b.x0)));
        out.extend(col_lines.into_iter().cloned());
    }
    out
}

/// y-order comparator: top-first for normal pages, bottom-first when the
/// page's coordinate system is flipped (y grows downward).
fn sort_y(a: &PdfLine, b: &PdfLine, flipped: bool) -> std::cmp::Ordering {
    if flipped {
        a.y.total_cmp(&b.y)
    } else {
        b.y.total_cmp(&a.y)
    }
}

fn sort_top_down(mut lines: Vec<PdfLine>, flipped: bool) -> Vec<PdfLine> {
    lines.sort_by(|a, b| sort_y(a, b, flipped).then(a.x0.total_cmp(&b.x0)));
    lines
}

#[derive(Debug, Clone)]
struct Column {
    idx: usize,
    x0: f32,
    x1: f32,
}

/// Cluster lines into columns by x-range overlap.
fn cluster_columns(lines: &[PdfLine], page_w: f32) -> Vec<Column> {
    let mut sorted: Vec<&PdfLine> = lines.iter().collect();
    sorted.sort_by(|a, b| a.x0.total_cmp(&b.x0));
    let mut columns: Vec<Column> = Vec::new();
    for line in sorted {
        let gap = (page_w * COLUMN_GAP).max(12.0);
        if let Some(col) = columns
            .iter_mut()
            .find(|c| line.x0 <= c.x1 + gap && line.x1 >= c.x0 - gap)
        {
            col.x0 = col.x0.min(line.x0);
            col.x1 = col.x1.max(line.x1);
        } else {
            columns.push(Column {
                idx: columns.len(),
                x0: line.x0,
                x1: line.x1,
            });
        }
    }
    columns
}

fn column_of(columns: &[Column], line: &PdfLine) -> usize {
    for c in columns {
        if line.x0 <= c.x1 && line.x1 >= c.x0 {
            return c.idx;
        }
    }
    columns.len() - 1
}

/// Strip running headers/footers: repeated top/bottom-zone lines and bare
/// page-number lines. Operates on the full document so repeats are visible.
pub fn strip_running_headers(lines: Vec<PdfLine>, page_h: f32, total_pages: usize) -> Vec<PdfLine> {
    // Normalized top/bottom zone texts across all pages.
    let mut zone_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    let zone_lines: Vec<&PdfLine> = lines.iter().filter(|l| in_zone(l, page_h)).collect();
    for l in &zone_lines {
        *zone_counts.entry(normalize_zone(l)).or_insert(0) += 1;
    }
    let repeat_threshold = ((total_pages as f32) * 0.4).max(2.0) as usize;

    lines
        .into_iter()
        .filter(|l| {
            if in_zone(l, page_h) {
                let is_page_number =
                    l.text.trim().len() <= 5 && l.text.trim().chars().all(|c| c.is_ascii_digit());
                let is_repeat =
                    zone_counts.get(&normalize_zone(l)).copied().unwrap_or(0) >= repeat_threshold;
                let is_header_text =
                    l.text.trim().is_empty() || l.text.trim().chars().all(|c| c == ' ');
                !is_page_number && !is_repeat && !is_header_text
            } else {
                true
            }
        })
        .collect()
}

fn in_zone(l: &PdfLine, page_h: f32) -> bool {
    l.y > page_h * 0.93 || l.y < page_h * 0.05
}

/// Normalize a zone line: strip trailing standalone digits (page numbers).
fn normalize_zone(l: &PdfLine) -> String {
    let mut text = l.text.trim().to_string();
    let tokens: Vec<&str> = text.split_whitespace().collect();
    if let Some(last) = tokens.last() {
        if last.len() <= 4 && last.chars().all(|c| c.is_ascii_digit()) {
            let cut = text.len() - last.len();
            text = text[..cut].trim_end().to_string();
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(text: &str, x: f32, y: f32, size: f32, page: u32) -> TextItem {
        TextItem {
            text: text.into(),
            x,
            y,
            width: text.len() as f32 * size * 0.5,
            height: size,
            font: "Helvetica".into(),
            size,
            page,
        }
    }

    fn line(text: &str, x0: f32, x1: f32, y: f32, page: u32) -> PdfLine {
        PdfLine {
            text: text.into(),
            x0,
            x1,
            y,
            size: 9.0,
            font: "Helvetica".into(),
            page,
            monospace: false,
        }
    }

    #[test]
    fn groups_same_baseline() {
        let items = vec![
            item("Hello", 72.0, 720.0, 12.0, 1),
            item("World", 132.0, 720.0, 12.0, 1),
        ];
        let lines = group_into_lines(&items);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].text, "Hello World");
        assert!((lines[0].y - 720.0).abs() < 0.01);
    }

    #[test]
    fn splits_different_baselines() {
        let items = vec![
            item("Top", 72.0, 720.0, 12.0, 1),
            item("Bottom", 72.0, 700.0, 12.0, 1),
        ];
        let lines = group_into_lines(&items);
        assert_eq!(lines.len(), 2);
    }

    #[test]
    fn single_column_reading_order() {
        // Content order runs top→bottom (larger y first).
        let lines = vec![
            line("first line", 72.0, 200.0, 120.0, 1),
            line("second line", 72.0, 200.0, 100.0, 1),
        ];
        let ordered = reading_order(lines);
        assert_eq!(ordered[0].text, "first line");
        assert_eq!(ordered[1].text, "second line");
    }

    #[test]
    fn flipped_page_reads_bottom_up() {
        // A producer that draws with a top-left origin yields increasing y.
        let lines = vec![
            line("first line", 72.0, 200.0, 24.0, 1),
            line("second line", 72.0, 200.0, 40.0, 1),
        ];
        let ordered = reading_order(lines);
        assert_eq!(ordered[0].text, "first line");
        assert_eq!(ordered[1].text, "second line");
    }

    #[test]
    fn multi_column_reads_left_then_right() {
        // Column A lines interleaved with column B lines in y.
        let lines = vec![
            line("A1", 72.0, 200.0, 300.0, 1),
            line("B1", 400.0, 520.0, 290.0, 1),
            line("A2", 72.0, 200.0, 250.0, 1),
            line("B2", 400.0, 520.0, 240.0, 1),
        ];
        let ordered = reading_order(lines);
        let texts: Vec<&str> = ordered.iter().map(|l| l.text.as_str()).collect();
        assert_eq!(texts, vec!["A1", "A2", "B1", "B2"]);
    }

    #[test]
    fn strip_repeated_running_head() {
        let page_h = 792.0;
        let mut lines = Vec::new();
        for p in 1..=4u32 {
            lines.push(line("Persistence Monitor", 72.0, 200.0, 760.0, p));
            lines.push(line("body text", 72.0, 200.0, 400.0, p));
            lines.push(line("257", 500.0, 520.0, 30.0, p)); // footer page number
        }
        let stripped = strip_running_headers(lines, page_h, 4);
        assert_eq!(stripped.len(), 4);
        assert!(stripped.iter().all(|l| l.text == "body text"));
    }
}
