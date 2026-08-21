//! Content-stream walker: converts PDF text operators into positioned text.

use std::collections::HashMap;

use super::fonts::{
    page_content, page_fonts, page_resources, resolve_dict, resolve_font, to_f32, to_f64, FontInfo,
};
use crate::error::{PdfError, Result};
use crate::types::{ImagePlacement, PageExtraction, TextItem};
use lopdf::{Dictionary, Document, Object, ObjectId};

/// Maximum recursion depth for Form XObjects.
const MAX_FORM_DEPTH: u8 = 5;
/// Maximum content-stream operations per page (defence against malformed docs).
const MAX_OPS_PER_PAGE: usize = 1_000_000;

/// Extract text items and image placements from one page.
pub fn extract_page(doc: &Document, page_id: ObjectId, page_num: u32) -> Result<PageExtraction> {
    let mut walker = Walker::new(doc, page_num);
    walker.walk_page(page_id)?;
    Ok(PageExtraction {
        page: page_num,
        items: walker.items,
        images: Vec::new(),
        placements: walker.placements,
        text_op_count: walker.text_ops,
        image_op_count: walker.image_ops,
    })
}

/// Graphics + text state for the walker.
#[derive(Clone)]
struct State {
    ctm: [f64; 6],
    tm: [f64; 6],
    tlm: [f64; 6],
    in_text: bool,
    font: Option<(String, FontInfo)>,
    size: f32,
    char_spacing: f64,
    word_spacing: f64,
    hscale: f64,
    lead: f64,
    rise: f64,
}

impl Default for State {
    fn default() -> Self {
        Self {
            ctm: [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            tm: [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            tlm: [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            in_text: false,
            font: None,
            size: 0.0,
            char_spacing: 0.0,
            word_spacing: 0.0,
            hscale: 100.0,
            lead: 0.0,
            rise: 0.0,
        }
    }
}

struct Walker<'a> {
    doc: &'a Document,
    page: u32,
    /// Font resource maps, innermost last (page resources, then form resources).
    font_maps: Vec<HashMap<Vec<u8>, Dictionary>>,
    /// XObject maps, innermost last.
    xobject_maps: Vec<HashMap<Vec<u8>, Object>>,
    cmap_cache: HashMap<ObjectId, Option<super::tounicode::CMap>>,
    state: State,
    /// Saved graphics/text states for q/Q.
    state_stack: Vec<State>,
    items: Vec<TextItem>,
    placements: Vec<ImagePlacement>,
    text_ops: u32,
    image_ops: u32,
    depth: u8,
    ops_seen: usize,
}

impl<'a> Walker<'a> {
    fn new(doc: &'a Document, page: u32) -> Self {
        Self {
            doc,
            page,
            font_maps: Vec::new(),
            xobject_maps: Vec::new(),
            cmap_cache: HashMap::new(),
            state: State::default(),
            state_stack: Vec::new(),
            items: Vec::new(),
            placements: Vec::new(),
            text_ops: 0,
            image_ops: 0,
            depth: 0,
            ops_seen: 0,
        }
    }

    fn walk_page(&mut self, page_id: ObjectId) -> Result<()> {
        let content = page_content(self.doc, page_id)?;
        let fonts = page_fonts(self.doc, page_id)?;
        let xobjects = page_xobjects(self.doc, page_id)?;
        self.font_maps.push(fonts);
        self.xobject_maps.push(xobjects);
        let result = self.walk_content(&content, self.depth);
        self.font_maps.pop();
        self.xobject_maps.pop();
        result
    }

    /// Walk one content stream (page or form), updating shared state.
    fn walk_content(&mut self, content: &[u8], depth: u8) -> Result<()> {
        if depth > MAX_FORM_DEPTH {
            return Ok(());
        }
        let ops = lopdf::content::Content::decode(content)
            .map_err(|e| PdfError::Parse(format!("content decode: {e}")))?;
        self.state.tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
        self.state.tlm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];

        for op in &ops.operations {
            self.ops_seen += 1;
            if self.ops_seen > MAX_OPS_PER_PAGE {
                return Err(PdfError::Parse(
                    "content stream exceeds operation budget".into(),
                ));
            }
            match op.operator.as_str() {
                "q" => self.push_state(),
                "Q" => self.pop_state(),
                "cm" => {
                    if let Some(m) = op_array6(&op.operands) {
                        self.state.ctm = mat_mul(&m, &self.state.ctm);
                    }
                }
                "BT" => {
                    self.state.tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
                    self.state.tlm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
                    self.state.in_text = true;
                }
                "ET" => self.state.in_text = false,
                "Td" => {
                    if let Some((tx, ty)) = op_pair(&op.operands) {
                        self.move_text_line(tx, ty);
                    }
                }
                "TD" => {
                    if let Some((tx, ty)) = op_pair(&op.operands) {
                        self.state.lead = -ty;
                        self.move_text_line(tx, ty);
                    }
                }
                "Tm" => {
                    if let Some(m) = op_array6(&op.operands) {
                        self.state.tm = m;
                        self.state.tlm = m;
                    }
                }
                "T*" => self.move_text_line(0.0, -self.state.lead),
                "Tf" => {
                    if op.operands.len() >= 2 {
                        if let Ok(name) = op.operands[0].as_name() {
                            let size = to_f32(&op.operands[1]).unwrap_or(0.0);
                            let font = self.resolve_font(name);
                            // Store the BaseFont name (from the font dict),
                            // not the resource name, so style heuristics
                            // (monospace detection) work.
                            self.state.font = font.map(|f| (f.name.clone(), f));
                            self.state.size = size;
                        }
                    }
                }
                "Tc" => {
                    if let Some(v) = op.operands.first().and_then(to_f32) {
                        self.state.char_spacing = v as f64;
                    }
                }
                "Tw" => {
                    if let Some(v) = op.operands.first().and_then(to_f32) {
                        self.state.word_spacing = v as f64;
                    }
                }
                "Tz" => {
                    if let Some(v) = op.operands.first().and_then(to_f32) {
                        self.state.hscale = v as f64;
                    }
                }
                "TL" => {
                    if let Some(v) = op.operands.first().and_then(to_f32) {
                        self.state.lead = v as f64;
                    }
                }
                "Ts" => {
                    if let Some(v) = op.operands.first().and_then(to_f32) {
                        self.state.rise = v as f64;
                    }
                }
                "Tj" => {
                    if let Some(bytes) = op.operands.first().and_then(|o| o.as_str().ok()) {
                        self.text_ops += 1;
                        self.show_text(bytes);
                    }
                }
                "TJ" => {
                    if let Some(arr) = op.operands.first().and_then(|o| o.as_array().ok()) {
                        self.text_ops += 1;
                        for item in arr {
                            match item {
                                Object::String(s, _) => self.show_text(s),
                                _ => {
                                    // Kerning adjustment: negative numbers move right.
                                    if let Some(n) = to_f64(item) {
                                        let eff = self.state.size as f64 * self.state.tm[0].abs();
                                        self.state.tm[4] -=
                                            n / 1000.0 * eff * self.state.hscale / 100.0;
                                    }
                                }
                            }
                        }
                    }
                }
                "'" => {
                    self.move_text_line(0.0, -self.state.lead);
                    if let Some(bytes) = op.operands.first().and_then(|o| o.as_str().ok()) {
                        self.text_ops += 1;
                        self.show_text(bytes);
                    }
                }
                "\"" => {
                    // (aw, ac, string): set word/char spacing, next line, show.
                    if op.operands.len() >= 3 {
                        if let (Some(aw), Some(ac)) =
                            (to_f32(&op.operands[0]), to_f32(&op.operands[1]))
                        {
                            self.state.word_spacing = aw as f64;
                            self.state.char_spacing = ac as f64;
                        }
                        self.move_text_line(0.0, -self.state.lead);
                        if let Ok(bytes) = op.operands[2].as_str() {
                            self.text_ops += 1;
                            self.show_text(bytes);
                        }
                    }
                }
                "Do" => self.do_xobject(&op.operands, depth)?,
                _ => {}
            }
        }
        Ok(())
    }

    // -- state helpers ------------------------------------------------------

    fn push_state(&mut self) {
        self.state_stack.push(self.state.clone());
    }

    fn pop_state(&mut self) {
        if let Some(s) = self.state_stack.pop() {
            self.state = s;
        }
    }

    fn move_text_line(&mut self, tx: f64, ty: f64) {
        let tlm = self.state.tlm;
        self.state.tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4] + tx, tlm[5] + ty];
        self.state.tm = self.state.tlm;
    }

    fn resolve_font(&mut self, name: &[u8]) -> Option<FontInfo> {
        for map in self.font_maps.iter().rev() {
            if let Some(dict) = map.get(name) {
                return Some(resolve_font(self.doc, dict, &mut self.cmap_cache));
            }
        }
        None
    }

    fn resolve_xobject(&self, name: &[u8]) -> Option<&Object> {
        for map in self.xobject_maps.iter().rev() {
            if let Some(obj) = map.get(name) {
                return Some(obj);
            }
        }
        None
    }

    // -- text showing -------------------------------------------------------

    /// Decode and position one string operand of Tj/TJ.
    fn show_text(&mut self, bytes: &[u8]) {
        if !self.state.in_text {
            return;
        }
        let Some((font_name, font)) = self.state.font.clone() else {
            return;
        };
        let tfs = self.state.size as f64;
        // Some producers (e.g. old InDesign) write `/F 1 Tf` and encode the
        // real point size in the text matrix (`10 0 0 10 … Tm`). The
        // effective size is Tfs × Tm[0]; glyph advances must scale by it.
        let tm_scale = self.state.tm[0].abs();
        let eff = tfs * tm_scale;
        if eff <= 0.0 {
            return;
        }
        let hscale = self.state.hscale / 100.0;
        let x = self.state.tm[4];
        let run_start = x;
        let mut text = String::new();
        let mut width = 0.0f64;

        let consume =
            |walker: &mut Self, ch: char, code: u16, text: &mut String, width: &mut f64| {
                let wu = walker
                    .state
                    .font
                    .as_ref()
                    .map(|(_, f)| f.width_for(code) as f64)
                    .unwrap_or(500.0);
                let extra = if ch == ' ' {
                    walker.state.word_spacing
                } else {
                    0.0
                };
                let adv =
                    (wu / 1000.0 * tfs + walker.state.char_spacing + extra) * tm_scale * hscale;
                text.push(ch);
                *width += adv;
            };

        if font.is_cid {
            for chunk in bytes.chunks(2) {
                if chunk.len() < 2 {
                    continue;
                }
                let code = u16::from_be_bytes([chunk[0], chunk[1]]);
                let decoded = font
                    .tounicode
                    .as_ref()
                    .and_then(|c| c.get(chunk))
                    .unwrap_or_else(|| "\u{FFFD}".to_string());
                for ch in decoded.chars() {
                    consume(self, ch, code, &mut text, &mut width);
                }
            }
        } else {
            for &b in bytes {
                let ch = font.decode_byte(b);
                if ch == '\u{FFFD}' {
                    continue;
                }
                consume(self, ch, b as u16, &mut text, &mut width);
            }
        }

        if text.is_empty() {
            return;
        }
        self.state.tm[4] = x + width;

        let (px, py) = self.to_page(run_start, self.state.tm[5] - self.state.rise);
        let xscale = (self.state.ctm[0].powi(2) + self.state.ctm[1].powi(2)).sqrt();
        let yscale = (self.state.ctm[2].powi(2) + self.state.ctm[3].powi(2)).sqrt();
        self.items.push(TextItem {
            text,
            x: px,
            y: py,
            width: (width * xscale) as f32,
            height: (eff * yscale) as f32,
            font: font_name,
            size: eff as f32,
            page: self.page,
        });
    }

    // -- XObjects -----------------------------------------------------------

    fn do_xobject(&mut self, operands: &[Object], depth: u8) -> Result<()> {
        let Some(name) = operands.first().and_then(|o| o.as_name().ok()) else {
            return Ok(());
        };
        let Some(obj) = self.resolve_xobject(name).cloned() else {
            return Ok(());
        };
        let dict = match resolve_dict(self.doc, &obj) {
            Ok(d) => d.clone(),
            Err(_) => return Ok(()),
        };
        let subtype = dict
            .get(b"Subtype")
            .ok()
            .and_then(|o| o.as_name().ok())
            .map(|n| String::from_utf8_lossy(n).into_owned())
            .unwrap_or_default();
        match subtype.as_str() {
            "Image" => {
                self.image_ops += 1;
                // Unit square [0,1]x[0,1] transformed by the CTM.
                let (x0, y0) = self.to_page(0.0, 0.0);
                let (x1, y1) = self.to_page(1.0, 1.0);
                self.placements.push(ImagePlacement {
                    name: name.to_vec(),
                    x: x0.min(x1),
                    y: y0.min(y1),
                    w: (x1 - x0).abs(),
                    h: (y1 - y0).abs(),
                });
            }
            "Form" => {
                let matrix = dict
                    .get(b"Matrix")
                    .ok()
                    .and_then(|o| o.as_array().ok())
                    .and_then(|a| op_array6_from_array(a))
                    .unwrap_or([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]);
                // ctm = form_matrix × ctm (outer-to-inner order).
                let saved = self.state.ctm;
                self.state.ctm = mat_mul(&matrix, &saved);
                self.state.in_text = false;
                let stream = match &obj {
                    Object::Reference(id) => match self.doc.get_object(*id) {
                        Ok(Object::Stream(s)) => Some(s.clone()),
                        _ => None,
                    },
                    Object::Stream(s) => Some(s.clone()),
                    _ => None,
                };
                let Some(stream) = stream else {
                    self.state.ctm = saved;
                    return Ok(());
                };
                // Form-level resources shadow outer ones.
                let mut pushed_fonts = false;
                let mut pushed_xobjects = false;
                if let Ok(res) = stream.dict.get(b"Resources") {
                    if let Ok(resdict) = resolve_dict(self.doc, res) {
                        if let Ok(f) = resdict.get(b"Font") {
                            if let Ok(fd) = resolve_dict(self.doc, f) {
                                self.font_maps.push(
                                    fd.iter()
                                        .filter_map(|(k, v)| {
                                            resolve_dict(self.doc, v)
                                                .ok()
                                                .map(|d| (k.clone(), d.clone()))
                                        })
                                        .collect(),
                                );
                                pushed_fonts = true;
                            }
                        }
                        if let Ok(xo) = resdict.get(b"XObject") {
                            if let Ok(xd) = resolve_dict(self.doc, xo) {
                                let map = xd.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
                                self.xobject_maps.push(map);
                                pushed_xobjects = true;
                            }
                        }
                    }
                }
                let content = stream
                    .decompressed_content()
                    .unwrap_or_else(|_| stream.content.clone());
                let result = self.walk_content(&content, depth + 1);
                if pushed_fonts {
                    self.font_maps.pop();
                }
                if pushed_xobjects {
                    self.xobject_maps.pop();
                }
                self.state.ctm = saved;
                return result;
            }
            _ => {}
        }
        Ok(())
    }

    // -- coordinates --------------------------------------------------------

    /// Transform a text-space point by the current CTM into page coordinates.
    fn to_page(&self, tx: f64, ty: f64) -> (f32, f32) {
        let c = &self.state.ctm;
        (
            (c[0] * tx + c[2] * ty + c[4]) as f32,
            (c[1] * tx + c[3] * ty + c[5]) as f32,
        )
    }
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

/// Page /Resources /XObject map (name → object).
fn page_xobjects(doc: &Document, page_id: ObjectId) -> Result<HashMap<Vec<u8>, Object>> {
    let mut out = HashMap::new();
    if let Some(resources) = page_resources(doc, page_id)? {
        if let Ok(xo) = resources.get(b"XObject") {
            if let Ok(xd) = resolve_dict(doc, xo) {
                for (k, v) in xd.iter() {
                    out.insert(k.clone(), v.clone());
                }
            }
        }
    }
    Ok(out)
}

fn op_pair(ops: &[Object]) -> Option<(f64, f64)> {
    if ops.len() < 2 {
        return None;
    }
    Some((to_f32(&ops[0])? as f64, to_f32(&ops[1])? as f64))
}

fn op_array6(ops: &[Object]) -> Option<[f64; 6]> {
    if ops.len() < 6 {
        return None;
    }
    let mut m = [0.0f64; 6];
    for (i, slot) in m.iter_mut().enumerate() {
        *slot = to_f32(&ops[i])? as f64;
    }
    Some(m)
}

fn op_array6_from_array(arr: &[Object]) -> Option<[f64; 6]> {
    if arr.len() < 6 {
        return None;
    }
    let mut m = [0.0f64; 6];
    for (i, slot) in m.iter_mut().enumerate() {
        *slot = to_f32(&arr[i])? as f64;
    }
    Some(m)
}

/// 3x3 affine matrix multiplication (last row implied [0,0,1]).
fn mat_mul(a: &[f64; 6], b: &[f64; 6]) -> [f64; 6] {
    [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use lopdf::{Dictionary, Object, Stream};

    /// Build a minimal one-page document with the given content stream.
    fn doc_with_content(content: &str) -> Document {
        let mut doc = Document::with_version("1.7");
        let font_id = doc.add_object(Object::Dictionary({
            let mut d = Dictionary::new();
            d.set(b"Type", Object::Name(b"Font".to_vec()));
            d.set(b"Subtype", Object::Name(b"Type1".to_vec()));
            d.set(b"BaseFont", Object::Name(b"Helvetica".to_vec()));
            d.set(b"Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
            d
        }));
        let fonts = doc.add_object(Object::Dictionary({
            let mut d = Dictionary::new();
            d.set(b"F1", Object::Reference(font_id));
            d
        }));
        let resources = doc.add_object(Object::Dictionary({
            let mut d = Dictionary::new();
            d.set(b"Font", Object::Reference(fonts));
            d
        }));
        let stream_id = doc.add_object(Object::Stream(Stream::new(
            Dictionary::new(),
            content.as_bytes().to_vec(),
        )));
        let pages_id = doc.add_object(Object::Dictionary({
            let mut d = Dictionary::new();
            d.set(b"Type", Object::Name(b"Pages".to_vec()));
            d.set(b"Kids", Object::Array(vec![]));
            d.set(b"Count", Object::Integer(0));
            d
        }));
        let page_id = doc.add_object(Object::Dictionary({
            let mut d = Dictionary::new();
            d.set(b"Type", Object::Name(b"Page".to_vec()));
            d.set(b"Parent", Object::Reference(pages_id));
            d.set(
                b"MediaBox",
                Object::Array(vec![
                    Object::Integer(0),
                    Object::Integer(0),
                    Object::Integer(612),
                    Object::Integer(792),
                ]),
            );
            d.set(b"Resources", Object::Reference(resources));
            d.set(b"Contents", Object::Reference(stream_id));
            d
        }));
        if let Ok(Object::Dictionary(pages)) = doc.get_object_mut(pages_id) {
            if let Ok(Object::Array(kids)) = pages.get_mut(b"Kids") {
                kids.push(Object::Reference(page_id));
            }
            pages.set(b"Count", Object::Integer(1));
        }
        // Root catalog so lopdf's get_pages can find the page tree.
        let catalog_id = doc.add_object(Object::Dictionary({
            let mut d = Dictionary::new();
            d.set(b"Type", Object::Name(b"Catalog".to_vec()));
            d.set(b"Pages", Object::Reference(pages_id));
            d
        }));
        doc.trailer.set(b"Root", Object::Reference(catalog_id));
        doc
    }

    fn extract(content: &str) -> PageExtraction {
        let doc = doc_with_content(content);
        let pages = doc.get_pages();
        let (num, id) = pages.iter().next().unwrap();
        extract_page(&doc, *id, *num).unwrap()
    }

    #[test]
    fn extracts_simple_text() {
        let page = extract("BT /F1 12 Tf 72 720 Td (Hello World) Tj ET");
        assert_eq!(page.items.len(), 1);
        let it = &page.items[0];
        assert_eq!(it.text, "Hello World");
        assert!((it.x - 72.0).abs() < 0.01);
        assert!((it.y - 720.0).abs() < 0.01);
        assert!(it.width > 40.0 && it.width < 120.0);
        assert_eq!(page.text_op_count, 1);
    }

    #[test]
    fn tj_array_kerning() {
        let page = extract("BT /F1 12 Tf 72 720 Td [(Hel) -120 (lo)] TJ ET");
        assert_eq!(page.items.len(), 2);
        assert_eq!(page.items[0].text, "Hel");
        assert_eq!(page.items[1].text, "lo");
        // Kerning pulls the second run left of where it would naturally sit.
        assert!(page.items[1].x < page.items[0].x + page.items[0].width + 10.0);
        assert_eq!(page.text_op_count, 1);
    }

    #[test]
    fn two_lines_via_td() {
        let page = extract("BT /F1 12 Tf 72 720 Td (First) Tj 0 -14 Td (Second) Tj ET");
        assert_eq!(page.items.len(), 2);
        assert!(
            page.items[0].y > page.items[1].y,
            "first line higher on page"
        );
    }

    #[test]
    fn ctm_scale_applies() {
        // 2x scale: 72 → 144 in page coordinates.
        let page = extract("q 2 0 0 2 0 0 cm BT /F1 12 Tf 72 720 Td (A) Tj ET Q");
        assert_eq!(page.items.len(), 1);
        assert!((page.items[0].x - 144.0).abs() < 0.5);
        assert!((page.items[0].y - 1440.0).abs() < 0.5);
        assert!((page.items[0].width - 2.0 * page.items[0].height * 0.5).abs() < 20.0);
    }

    #[test]
    fn garbage_content_is_tolerated() {
        let page = extract("BT /F1 12 Tf 72 720 Td (Ok) Tj ET garbage ops");
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].text, "Ok");
    }
}
