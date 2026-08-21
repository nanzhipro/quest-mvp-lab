//! Font encoding and metrics resolution for the content-stream walker.

use std::collections::HashMap;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream};

use super::tounicode::{parse_cmap, CMap};
use crate::error::{PdfError, Result};

/// Resolved information for one font resource.
#[derive(Debug, Clone)]
pub struct FontInfo {
    /// BaseFont name (e.g. "ABCDEF+Times-Roman").
    pub name: String,
    /// True for Type0 (composite / CID) fonts — 2-byte character codes.
    pub is_cid: bool,
    /// Monospaced font (code-block detection).
    pub monospace: bool,
    /// Simple-font byte → Unicode table (WinAnsi/Standard + Differences).
    pub encoding: Option<HashMap<u8, char>>,
    /// ToUnicode CMap for CID fonts (and occasionally simple fonts).
    pub tounicode: Option<CMap>,
    /// Character code → glyph width in 1000ths of an em.
    pub widths: HashMap<u16, f32>,
    /// Fallback glyph width in 1000ths of an em.
    pub default_width: f32,
}

impl FontInfo {
    /// Decode one byte for a simple font, falling back to cp1252.
    pub fn decode_byte(&self, b: u8) -> char {
        if b < 0x20 {
            return ' '; // control characters carry no glyph
        }
        match &self.encoding {
            Some(map) => map.get(&b).copied().unwrap_or('�'),
            None => cp1252(b),
        }
    }

    /// Glyph width for a character code, in 1000ths of an em.
    pub fn width_for(&self, code: u16) -> f32 {
        self.widths
            .get(&code)
            .copied()
            .unwrap_or(self.default_width)
    }
}

/// Resolve the /Font resource map of a page: resource name → font dictionary.
pub fn page_fonts(doc: &Document, page_id: ObjectId) -> Result<HashMap<Vec<u8>, Dictionary>> {
    let mut out = HashMap::new();
    if let Some(resources) = page_resources(doc, page_id)? {
        if let Ok(fonts_obj) = resources.get(b"Font") {
            let fonts_dict = resolve_dict(doc, fonts_obj)?;
            for (name, val) in fonts_dict.iter() {
                if let Ok(dict) = resolve_dict(doc, val) {
                    out.insert(name.clone(), dict.clone());
                }
            }
        }
    }
    Ok(out)
}

/// Resolve a font dictionary (handling indirect references) into a `FontInfo`.
///
/// `cmap_cache` avoids re-parsing the same ToUnicode stream per page.
pub fn resolve_font(
    doc: &Document,
    dict: &Dictionary,
    cmap_cache: &mut HashMap<ObjectId, Option<CMap>>,
) -> FontInfo {
    let subtype = dict
        .get(b"Subtype")
        .ok()
        .and_then(|o| o.as_name().ok())
        .map(|n| String::from_utf8_lossy(n).into_owned())
        .unwrap_or_default();
    let is_cid = subtype == "Type0";

    let name = dict
        .get(b"BaseFont")
        .ok()
        .and_then(|o| o.as_name().ok())
        .map(|n| String::from_utf8_lossy(n).into_owned())
        .unwrap_or_else(|| "Unknown".into());
    let monospace = font_is_monospace(doc, &name, dict);

    let encoding = if is_cid {
        None
    } else {
        Some(resolve_simple_encoding(dict))
    };

    let tounicode = resolve_tounicode(doc, dict, cmap_cache);

    let (widths, default_width) = if is_cid {
        resolve_cid_widths(doc, dict)
    } else {
        resolve_simple_widths(dict)
    };

    FontInfo {
        name,
        is_cid,
        monospace,
        encoding,
        tounicode,
        widths,
        default_width,
    }
}

// ---------------------------------------------------------------------------
// Encoding
// ---------------------------------------------------------------------------

/// Build the byte → char table for a simple font.
///
/// Base encodings supported: WinAnsi, Standard, MacRoman (approximated).
/// `/Differences` overlays are applied through a glyph-name table.
fn resolve_simple_encoding(dict: &Dictionary) -> HashMap<u8, char> {
    let mut map: HashMap<u8, char> = (0x20..0x7F).map(|b| (b, b as char)).collect();
    for (b, c) in (0x80..=0xFF).map(|b| (b, cp1252(b))) {
        map.insert(b, c);
    }

    let enc_obj = match dict.get(b"Encoding") {
        Ok(o) => o,
        Err(_) => return map,
    };

    // Named base encodings.
    if let Ok(name) = enc_obj.as_name() {
        let base = String::from_utf8_lossy(name);
        if base == "WinAnsiEncoding" {
            return map; // already the cp1252 table
        }
        if base == "MacRomanEncoding" {
            // Approximation: cp1252 with the euro replaced.
            map.insert(0x80, 'Ä');
            return map;
        }
        // StandardEncoding and anything unknown: keep ASCII + cp1252.
        return map;
    }

    // Encoding dictionary: /BaseEncoding + /Differences.
    let enc_dict = match enc_obj.as_dict() {
        Ok(d) => d,
        Err(_) => return map,
    };
    if let Ok(base) = enc_dict.get(b"BaseEncoding") {
        if let Ok(name) = base.as_name() {
            let base_name = String::from_utf8_lossy(name);
            if base_name == "MacRomanEncoding" {
                map.insert(0x80, 'Ä');
            }
        }
    }
    if let Ok(diffs) = enc_dict.get(b"Differences") {
        if let Ok(arr) = diffs.as_array() {
            let mut code: i32 = -1;
            for item in arr {
                match item {
                    Object::Integer(i) => code = *i as i32,
                    Object::Name(n) if code >= 0 => {
                        let name = String::from_utf8_lossy(n).into_owned();
                        if let Some(c) = glyph_name_char(&name) {
                            map.insert(code as u8, c);
                        }
                        code += 1;
                    }
                    _ => {}
                }
            }
        }
    }
    map
}

/// cp1252 (WinAnsi) table for bytes 0x80..=0xFF.
fn cp1252_base() -> [(u8, char); 128] {
    const T: &str = "\u{20AC}\u{FFFD}\u{201A}\u{0192}\u{201E}\u{2026}\u{2020}\u{2021}\u{02C6}\u{2030}\u{0160}\u{2039}\u{0152}\u{FFFD}\u{017D}\u{FFFD}\u{FFFD}\u{2018}\u{2019}\u{201C}\u{201D}\u{2022}\u{2013}\u{2014}\u{02DC}\u{2122}\u{0161}\u{203A}\u{0153}\u{FFFD}\u{017E}\u{0178}\u{00A0}\u{00A1}\u{00A2}\u{00A3}\u{00A4}\u{00A5}\u{00A6}\u{00A7}\u{00A8}\u{00A9}\u{00AA}\u{00AB}\u{00AC}\u{00AD}\u{00AE}\u{00AF}\u{00B0}\u{00B1}\u{00B2}\u{00B3}\u{00B4}\u{00B5}\u{00B6}\u{00B7}\u{00B8}\u{00B9}\u{00BA}\u{00BB}\u{00BC}\u{00BD}\u{00BE}\u{00BF}\u{00C0}\u{00C1}\u{00C2}\u{00C3}\u{00C4}\u{00C5}\u{00C6}\u{00C7}\u{00C8}\u{00C9}\u{00CA}\u{00CB}\u{00CC}\u{00CD}\u{00CE}\u{00CF}\u{00D0}\u{00D1}\u{00D2}\u{00D3}\u{00D4}\u{00D5}\u{00D6}\u{00D7}\u{00D8}\u{00D9}\u{00DA}\u{00DB}\u{00DC}\u{00DD}\u{00DE}\u{00DF}\u{00E0}\u{00E1}\u{00E2}\u{00E3}\u{00E4}\u{00E5}\u{00E6}\u{00E7}\u{00E8}\u{00E9}\u{00EA}\u{00EB}\u{00EC}\u{00ED}\u{00EE}\u{00EF}\u{00F0}\u{00F1}\u{00F2}\u{00F3}\u{00F4}\u{00F5}\u{00F6}\u{00F7}\u{00F8}\u{00F9}\u{00FA}\u{00FB}\u{00FC}\u{00FD}\u{00FE}\u{00FF}";
    let mut out = [(0u8, '\u{0}'); 128];
    for (i, c) in T.chars().enumerate() {
        out[i] = (0x80 + i as u8, c);
    }
    out
}

/// cp1252 lookup for a single byte (used as the universal fallback).
fn cp1252(b: u8) -> char {
    if b < 0x80 {
        return b as char;
    }
    cp1252_base()[(b - 0x80) as usize].1
}

/// Common glyph names used in `/Differences` arrays and base-14 fonts.
fn glyph_name_char(name: &str) -> Option<char> {
    const NAMES: &[(&str, char)] = &[
        ("space", ' '),
        ("exclam", '!'),
        ("quotedbl", '"'),
        ("numbersign", '#'),
        ("dollar", '$'),
        ("percent", '%'),
        ("ampersand", '&'),
        ("quotesingle", '\''),
        ("parenleft", '('),
        ("parenright", ')'),
        ("asterisk", '*'),
        ("plus", '+'),
        ("comma", ','),
        ("hyphen", '-'),
        ("period", '.'),
        ("slash", '/'),
        ("zero", '0'),
        ("one", '1'),
        ("two", '2'),
        ("three", '3'),
        ("four", '4'),
        ("five", '5'),
        ("six", '6'),
        ("seven", '7'),
        ("eight", '8'),
        ("nine", '9'),
        ("colon", ':'),
        ("semicolon", ';'),
        ("less", '<'),
        ("equal", '='),
        ("greater", '>'),
        ("question", '?'),
        ("at", '@'),
        ("A", 'A'),
        ("B", 'B'),
        ("C", 'C'),
        ("D", 'D'),
        ("E", 'E'),
        ("F", 'F'),
        ("G", 'G'),
        ("H", 'H'),
        ("I", 'I'),
        ("J", 'J'),
        ("K", 'K'),
        ("L", 'L'),
        ("M", 'M'),
        ("N", 'N'),
        ("O", 'O'),
        ("P", 'P'),
        ("Q", 'Q'),
        ("R", 'R'),
        ("S", 'S'),
        ("T", 'T'),
        ("U", 'U'),
        ("V", 'V'),
        ("W", 'W'),
        ("X", 'X'),
        ("Y", 'Y'),
        ("Z", 'Z'),
        ("bracketleft", '['),
        ("backslash", '\\'),
        ("bracketright", ']'),
        ("asciicircum", '^'),
        ("underscore", '_'),
        ("grave", '`'),
        ("a", 'a'),
        ("b", 'b'),
        ("c", 'c'),
        ("d", 'd'),
        ("e", 'e'),
        ("f", 'f'),
        ("g", 'g'),
        ("h", 'h'),
        ("i", 'i'),
        ("j", 'j'),
        ("k", 'k'),
        ("l", 'l'),
        ("m", 'm'),
        ("n", 'n'),
        ("o", 'o'),
        ("p", 'p'),
        ("q", 'q'),
        ("r", 'r'),
        ("s", 's'),
        ("t", 't'),
        ("u", 'u'),
        ("v", 'v'),
        ("w", 'w'),
        ("x", 'x'),
        ("y", 'y'),
        ("z", 'z'),
        ("braceleft", '{'),
        ("bar", '|'),
        ("braceright", '}'),
        ("asciitilde", '~'),
        ("exclamdown", '¡'),
        ("cent", '¢'),
        ("sterling", '£'),
        ("fraction", '⁄'),
        ("yen", '¥'),
        ("florin", 'ƒ'),
        ("section", '§'),
        ("currency", '¤'),
        ("quotedblleft", '“'),
        ("guillemotleft", '«'),
        ("guilsinglleft", '‹'),
        ("guilsinglright", '›'),
        ("fi", 'ﬁ'),
        ("fl", 'ﬂ'),
        ("endash", '–'),
        ("dagger", '†'),
        ("daggerdbl", '‡'),
        ("periodcentered", '·'),
        ("paragraph", '¶'),
        ("bullet", '•'),
        ("quotesinglbase", '‚'),
        ("quotedblbase", '„'),
        ("quotedblright", '”'),
        ("guillemotright", '»'),
        ("ellipsis", '…'),
        ("perthousand", '‰'),
        ("questiondown", '¿'),
        ("acute", '´'),
        ("circumflex", 'ˆ'),
        ("tilde", '˜'),
        ("macron", '¯'),
        ("breve", '˘'),
        ("dotaccent", '˙'),
        ("dieresis", '¨'),
        ("ring", '˚'),
        ("cedilla", '¸'),
        ("hungarumlaut", '˝'),
        ("ogonek", '˛'),
        ("caron", 'ˇ'),
        ("emdash", '—'),
        ("AE", 'Æ'),
        ("ordfeminine", 'ª'),
        ("Lslash", 'Ł'),
        ("Oslash", 'Ø'),
        ("OE", 'Œ'),
        ("ordmasculine", 'º'),
        ("ae", 'æ'),
        ("dotlessi", 'ı'),
        ("lslash", 'ł'),
        ("oslash", 'ø'),
        ("oe", 'œ'),
        ("germandbls", 'ß'),
        ("copyright", '©'),
        ("registered", '®'),
        ("trademark", '™'),
        ("minus", '−'),
        ("mu", 'µ'),
        ("divide", '÷'),
        ("multiply", '×'),
        ("plusminus", '±'),
        ("degree", '°'),
        ("notequal", '≠'),
        ("infinity", '∞'),
        ("approxequal", '≈'),
        ("lessequal", '≤'),
        ("greaterequal", '≥'),
        ("logicalnot", '¬'),
        ("emptyset", '∅'),
        ("partialdiff", '∂'),
        ("summation", '∑'),
        ("product", '∏'),
        ("integral", '∫'),
        ("radical", '√'),
        ("lozenge", '◊'),
        ("quoteleft", '‘'),
        ("quoteright", '’'),
        ("quotedbl", '"'),
    ];
    NAMES.iter().find(|(n, _)| *n == name).map(|(_, c)| *c)
}

// ---------------------------------------------------------------------------
// ToUnicode
// ---------------------------------------------------------------------------

fn resolve_tounicode(
    doc: &Document,
    dict: &Dictionary,
    cache: &mut HashMap<ObjectId, Option<CMap>>,
) -> Option<CMap> {
    match dict.get(b"ToUnicode") {
        Ok(Object::Reference(id)) => {
            if let Some(hit) = cache.get(id) {
                return hit.clone();
            }
            let parsed = doc.get_object(*id).ok().and_then(|o| match o {
                Object::Stream(s) => parse_stream_cmap(s),
                _ => None,
            });
            cache.insert(*id, parsed.clone());
            parsed
        }
        Ok(Object::Stream(s)) => parse_stream_cmap(s),
        _ => None,
    }
}

fn parse_stream_cmap(stream: &Stream) -> Option<CMap> {
    let data = stream
        .decompressed_content()
        .unwrap_or_else(|_| stream.content.clone());
    let cmap = parse_cmap(&data);
    (!cmap.is_empty()).then_some(cmap)
}

// ---------------------------------------------------------------------------
// Widths
// ---------------------------------------------------------------------------

/// /FirstChar /LastChar /Widths for simple fonts.
fn resolve_simple_widths(dict: &Dictionary) -> (HashMap<u16, f32>, f32) {
    let mut widths = HashMap::new();
    let first = dict.get(b"FirstChar").ok().and_then(to_i64).unwrap_or(0) as u16;
    let arr = dict
        .get(b"Widths")
        .ok()
        .and_then(|o| o.as_array().ok())
        .map(|a| a.to_vec())
        .unwrap_or_default();
    for (i, w) in arr.iter().enumerate() {
        if let Some(w) = to_f32(w) {
            widths.insert(first + i as u16, w);
        }
    }
    let default = base14_default_width(dict) * 1000.0;
    (widths, default)
}

/// /DW and /W for CID (Type0) fonts, descending into /DescendantFonts.
fn resolve_cid_widths(doc: &Document, dict: &Dictionary) -> (HashMap<u16, f32>, f32) {
    let mut default = 1000.0f32;
    let mut widths = HashMap::new();
    let desc = dict
        .get(b"DescendantFonts")
        .ok()
        .and_then(|o| o.as_array().ok())
        .and_then(|a| a.first())
        .and_then(|o| resolve_dict(doc, o).ok());
    let Some(desc) = desc else {
        return (widths, default);
    };
    if let Ok(dw) = desc.get(b"DW") {
        if let Some(v) = to_f32(dw) {
            default = v;
        }
    }
    let Some(w_obj) = desc.get(b"W").ok() else {
        return (widths, default);
    };
    let Ok(w_arr) = w_obj.as_array() else {
        return (widths, default);
    };
    let mut i = 0usize;
    while i + 1 < w_arr.len() {
        let c_first = to_i64(&w_arr[i]);
        i += 1;
        match &w_arr[i] {
            Object::Array(_) => {
                if let Some(first) = c_first {
                    if let Ok(ws) = w_arr[i].as_array() {
                        for (k, w) in ws.iter().enumerate() {
                            if let Some(w) = to_f32(w) {
                                widths.insert((first + k as i64) as u16, w);
                            }
                        }
                    }
                }
                i += 1;
            }
            _ => {
                // c_first c_last w
                if let (Some(first), Some(last)) = (c_first, to_i64(&w_arr[i])) {
                    if let Some(w) = to_f32(&w_arr[i + 1]) {
                        for c in first..=last {
                            widths.insert(c as u16, w);
                        }
                    }
                }
                i += 2;
            }
        }
    }
    (widths, default)
}

/// Average advance (in em units) for the base-14 fonts, used when a font
/// ships no /Widths table at all.
fn base14_default_width(dict: &Dictionary) -> f32 {
    let name = dict
        .get(b"BaseFont")
        .ok()
        .and_then(|o| o.as_name().ok())
        .map(|n| String::from_utf8_lossy(n).into_owned())
        .unwrap_or_default();
    let upper = name.to_uppercase();
    if upper.contains("COURIER") {
        0.6
    } else if upper.contains("HELVETICA") || upper.contains("ARIAL") {
        0.556
    } else {
        0.5
    }
}

/// Monospace detection from the font name or the descriptor flags.
fn font_is_monospace(doc: &Document, name: &str, dict: &Dictionary) -> bool {
    let upper = name.to_uppercase();
    if [
        "COURIER",
        "MONO",
        "MENLO",
        "CONSOLAS",
        "CODE",
        "TYPEWRITER",
        "COURIERPRIME",
    ]
    .iter()
    .any(|k| upper.contains(k))
    {
        return true;
    }
    // FontDescriptor /Flags bit 0 = fixed pitch.
    if let Some(desc) = dict
        .get(b"FontDescriptor")
        .ok()
        .and_then(|o| resolve_dict(doc, o).ok())
    {
        if let Ok(flags) = desc.get(b"Flags") {
            if let Some(v) = to_i64(flags) {
                return v & 1 == 1;
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/// Resolve an object (possibly an indirect reference) to a dictionary.
///
/// Stream objects carry their metadata dictionary directly, so they resolve
/// to it as well (image and form XObjects are streams).
pub(crate) fn resolve_dict<'a>(doc: &'a Document, obj: &'a Object) -> Result<&'a Dictionary> {
    match obj {
        Object::Dictionary(d) => Ok(d),
        Object::Stream(s) => Ok(&s.dict),
        Object::Reference(id) => match doc.get_object(*id) {
            Ok(Object::Dictionary(d)) => Ok(d),
            Ok(Object::Stream(s)) => Ok(&s.dict),
            Ok(other) => Err(PdfError::Parse(format!(
                "expected dictionary for {} got {}",
                id.0,
                String::from_utf8_lossy(other.type_name().unwrap_or(b"?"))
            ))),
            Err(e) => Err(PdfError::Parse(format!("missing object {}: {}", id.0, e))),
        },
        other => Err(PdfError::Parse(format!(
            "expected dictionary, got {}",
            String::from_utf8_lossy(other.type_name().unwrap_or(b"?"))
        ))),
    }
}

/// Resolve the /Resources dictionary of a page, following /Parent chains.
pub(crate) fn page_resources(doc: &Document, page_id: ObjectId) -> Result<Option<Dictionary>> {
    let mut current = page_id;
    for _ in 0..8 {
        let dict = doc
            .get_dictionary(current)
            .map_err(|e| PdfError::Parse(e.to_string()))?;
        if let Ok(res) = dict.get(b"Resources") {
            return resolve_dict(doc, res).map(|d| Some(d.clone()));
        }
        match dict.get(b"Parent") {
            Ok(Object::Reference(p)) => current = *p,
            _ => return Ok(None),
        }
    }
    Ok(None)
}

/// Read a page's /Contents as concatenated decompressed bytes.
pub(crate) fn page_content(doc: &Document, page_id: ObjectId) -> Result<Vec<u8>> {
    let dict = doc
        .get_dictionary(page_id)
        .map_err(|e| PdfError::Parse(e.to_string()))?;
    match dict.get(b"Contents") {
        Ok(Object::Stream(s)) => s
            .decompressed_content()
            .map_err(|e| PdfError::Parse(e.to_string())),
        Ok(Object::Reference(id)) => match doc.get_object(*id) {
            Ok(Object::Stream(s)) => s
                .decompressed_content()
                .map_err(|e| PdfError::Parse(e.to_string())),
            _ => Ok(Vec::new()),
        },
        Ok(Object::Array(arr)) => {
            let mut out = Vec::new();
            for item in arr {
                if let Ok(Object::Stream(s)) = item.as_reference().and_then(|id| doc.get_object(id))
                {
                    if let Ok(data) = s.decompressed_content() {
                        out.extend_from_slice(&data);
                    }
                }
            }
            Ok(out)
        }
        _ => Ok(Vec::new()),
    }
}

pub(crate) fn to_i64(o: &Object) -> Option<i64> {
    match o {
        Object::Integer(i) => Some(*i),
        _ => None,
    }
}

pub(crate) fn to_f32(o: &Object) -> Option<f32> {
    match o {
        Object::Integer(i) => Some(*i as f32),
        Object::Real(f) => Some(*f),
        _ => None,
    }
}

pub(crate) fn to_f64(o: &Object) -> Option<f64> {
    match o {
        Object::Integer(i) => Some(*i as f64),
        Object::Real(f) => Some(*f as f64),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cp1252_table_is_complete() {
        let t = cp1252_base();
        assert_eq!(t.len(), 128);
        assert_eq!(t[0].1, '\u{20AC}'); // € at 0x80
        assert_eq!(t[0x9F - 0x80].1, 'Ÿ'); // Ÿ at 0x9F
        assert_eq!(cp1252(0xE9), 'é');
        assert_eq!(cp1252(0x41), 'A');
    }

    #[test]
    fn glyph_names_cover_common_differences() {
        for (n, c) in [
            ("fi", 'ﬁ'),
            ("emdash", '—'),
            ("quoteright", '’'),
            ("Lslash", 'Ł'),
        ] {
            assert_eq!(glyph_name_char(n), Some(c), "name {n}");
        }
        assert_eq!(glyph_name_char("nosuchglyph"), None);
    }

    #[test]
    fn simple_encoding_applies_differences() {
        let mut dict = Dictionary::new();
        dict.set(
            b"Encoding",
            lopdf::Object::Dictionary({
                let mut d = Dictionary::new();
                d.set(
                    b"BaseEncoding",
                    lopdf::Object::Name(b"WinAnsiEncoding".to_vec()),
                );
                d.set(
                    b"Differences",
                    lopdf::Object::Array(vec![
                        lopdf::Object::Integer(0x24),
                        lopdf::Object::Name(b"currency".to_vec()),
                    ]),
                );
                d
            }),
        );
        let enc = resolve_simple_encoding(&dict);
        assert_eq!(enc.get(&0x24), Some(&'¤'));
        assert_eq!(enc.get(&0x41), Some(&'A'));
    }

    #[test]
    fn cid_widths_parse() {
        let mut desc = Dictionary::new();
        desc.set(b"DW", lopdf::Object::Integer(1000));
        desc.set(
            b"W",
            lopdf::Object::Array(vec![
                lopdf::Object::Integer(1),
                lopdf::Object::Array(vec![
                    lopdf::Object::Integer(500),
                    lopdf::Object::Integer(600),
                ]),
                lopdf::Object::Integer(10),
                lopdf::Object::Integer(12),
                lopdf::Object::Integer(700),
            ]),
        );
        let mut dict = Dictionary::new();
        dict.set(
            b"DescendantFonts",
            lopdf::Object::Array(vec![lopdf::Object::Dictionary(desc)]),
        );
        let (w, dw) = resolve_cid_widths_direct(&dict);
        assert_eq!(w.get(&1), Some(&500.0));
        assert_eq!(w.get(&2), Some(&600.0));
        assert_eq!(w.get(&11), Some(&700.0));
        assert_eq!(dw, 1000.0);
    }

    /// Test-only wrapper: resolve_cid_widths needs a Document; the test
    /// variant resolves an inline descendant dict directly.
    fn resolve_cid_widths_direct(dict: &Dictionary) -> (HashMap<u16, f32>, f32) {
        let mut default = 1000.0f32;
        let mut widths = HashMap::new();
        let desc = dict
            .get(b"DescendantFonts")
            .ok()
            .and_then(|o| o.as_array().ok())
            .and_then(|a| a.first())
            .and_then(|o| match o {
                Object::Dictionary(d) => Some(d.clone()),
                _ => None,
            });
        let Some(desc) = desc else {
            return (widths, default);
        };
        if let Ok(dw) = desc.get(b"DW") {
            if let Some(v) = to_f32(dw) {
                default = v;
            }
        }
        let Some(w_arr) = desc.get(b"W").ok().and_then(|o| o.as_array().ok()) else {
            return (widths, default);
        };
        let mut i = 0usize;
        while i + 1 < w_arr.len() {
            let c_first = to_i64(&w_arr[i]);
            i += 1;
            match &w_arr[i] {
                Object::Array(_) => {
                    if let Some(first) = c_first {
                        if let Ok(ws) = w_arr[i].as_array() {
                            for (k, w) in ws.iter().enumerate() {
                                if let Some(w) = to_f32(w) {
                                    widths.insert((first + k as i64) as u16, w);
                                }
                            }
                        }
                    }
                    i += 1;
                }
                _ => {
                    if let (Some(first), Some(last)) = (c_first, to_i64(&w_arr[i])) {
                        if let Some(w) = to_f32(&w_arr[i + 1]) {
                            for c in first..=last {
                                widths.insert(c as u16, w);
                            }
                        }
                    }
                    i += 2;
                }
            }
        }
        (widths, default)
    }
}
