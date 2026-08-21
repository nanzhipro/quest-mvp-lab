//! Printed-page mapping: recover the book's page number from the running head.

use crate::types::PdfLine;

/// Fraction of the page height considered the header zone.
const HEADER_ZONE: f32 = 0.08;

/// Infer the printed page number of a page from its header/footer lines.
///
/// Strategy: scan the topmost line for a standalone 1–4 digit token (the
/// classic book running head, e.g. "Persistence Monitor 257"). Falls back to
/// the bottom zone. Returns `None` when no credible token exists.
pub fn printed_page_number(lines: &[PdfLine], page_h: f32) -> Option<u32> {
    // Highest line first (largest y).
    let mut top: Vec<&PdfLine> = lines
        .iter()
        .filter(|l| l.y > page_h - HEADER_ZONE * page_h)
        .collect();
    top.sort_by(|a, b| b.y.total_cmp(&a.y).then(a.x0.total_cmp(&b.x0)));
    for l in top.iter().take(3) {
        if let Some(n) = token_number(l) {
            return Some(n);
        }
    }
    let mut bottom: Vec<&PdfLine> = lines.iter().filter(|l| l.y < page_h * 0.06).collect();
    bottom.sort_by(|a, b| b.y.total_cmp(&a.y));
    for l in bottom.iter().take(3) {
        if let Some(n) = token_number(l) {
            return Some(n);
        }
    }
    None
}

/// Extract a standalone 1–4 digit token from a short line.
///
/// Handles tokens the extractor glues together (e.g. the page number and
/// chapter name sharing a baseline become "254!!!Chapter"): leading or
/// trailing digit runs inside a token also qualify.
fn token_number(l: &PdfLine) -> Option<u32> {
    if l.text.len() > 80 {
        return None;
    }
    for t in l.text.split_whitespace() {
        // Standalone digits.
        if t.len() <= 4 && t.chars().all(|c| c.is_ascii_digit()) {
            if let Ok(n) = t.parse() {
                return Some(n);
            }
        }
        // Leading digit run ("254!!!Chapter").
        let lead: String = t.chars().take_while(|c| c.is_ascii_digit()).collect();
        if (1..=4).contains(&lead.len()) {
            if let Ok(n) = lead.parse() {
                return Some(n);
            }
        }
        // Trailing digit run ("Monitor!!!257").
        let trail_rev: String = t.chars().rev().take_while(|c| c.is_ascii_digit()).collect();
        let trail: String = trail_rev.chars().rev().collect();
        if (1..=4).contains(&trail.len()) {
            if let Ok(n) = trail.parse() {
                return Some(n);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(text: &str, y: f32) -> PdfLine {
        PdfLine {
            text: text.into(),
            x0: 72.0,
            x1: 300.0,
            y,
            size: 9.0,
            font: "Helvetica".into(),
            page: 1,
            monospace: false,
        }
    }

    #[test]
    fn header_trailing_number() {
        let lines = vec![
            line("Persistence Monitor 257", 760.0),
            line("Tool Design", 730.0),
        ];
        assert_eq!(printed_page_number(&lines, 792.0), Some(257));
    }

    #[test]
    fn header_leading_number() {
        let lines = vec![line("254 Chapter 11", 760.0)];
        assert_eq!(printed_page_number(&lines, 792.0), Some(254));
    }

    #[test]
    fn footer_number_fallback() {
        let lines = vec![line("body text here", 400.0), line("12", 30.0)];
        assert_eq!(printed_page_number(&lines, 792.0), Some(12));
    }

    #[test]
    fn no_number() {
        let lines = vec![line("Tool Design", 760.0)];
        assert_eq!(printed_page_number(&lines, 792.0), None);
    }
}
