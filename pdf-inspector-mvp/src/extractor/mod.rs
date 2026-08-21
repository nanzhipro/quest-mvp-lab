//! Text + image extraction: content streams, fonts, and embedded images.

pub mod content;
pub mod fonts;
pub mod images;
pub mod tounicode;

use lopdf::{Document, ObjectId};

use crate::error::Result;
use crate::types::{ImageItem, TextItem};

pub use crate::types::{ImagePlacement, PageExtraction};
pub use content::extract_page;
pub use fonts::FontInfo;
pub use images::build_images;
pub use tounicode::CMap;

/// Extract text items and resolved images for every page of a document.
///
/// Pages come back sorted by PDF page number (1-indexed).
pub fn extract_pages(doc: &Document) -> Result<Vec<PageExtraction>> {
    let pages = doc.get_pages();
    let mut out = Vec::with_capacity(pages.len());
    for (num, id) in pages {
        let mut page = extract_page(doc, id, num)?;
        page.images = build_images(doc, id, num, &page.placements)?;
        out.push(page);
    }
    out.sort_by_key(|p| p.page);
    Ok(out)
}

/// Extract the raw text of a page (unpositioned, content order).
pub fn page_text(page: &PageExtraction) -> String {
    page.items.iter().map(|i| i.text.as_str()).collect()
}

/// Extract positioned text items for one page id.
pub fn extract_page_items(
    doc: &Document,
    page_id: ObjectId,
    page_num: u32,
) -> Result<Vec<TextItem>> {
    Ok(extract_page(doc, page_id, page_num)?.items)
}

/// Flatten all text items across pages into one list.
pub fn all_items(pages: &[PageExtraction]) -> Vec<TextItem> {
    pages.iter().flat_map(|p| p.items.iter().cloned()).collect()
}

/// Flatten all images across pages into one list.
pub fn all_images(pages: &[PageExtraction]) -> Vec<ImageItem> {
    pages
        .iter()
        .flat_map(|p| p.images.iter().cloned())
        .collect()
}
