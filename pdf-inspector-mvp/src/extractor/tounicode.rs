//! Minimal ToUnicode CMap support for CID/Type0 fonts.

use std::collections::HashMap;

/// A parsed ToUnicode CMap: character-code → Unicode string.
#[derive(Debug, Clone, Default)]
pub struct CMap {
    /// Exact code → string entries from `bfchar`.
    map: HashMap<Vec<u8>, String>,
    /// `bfrange` entries: (lo, hi, start-string).
    ranges: Vec<(Vec<u8>, Vec<u8>, String)>,
}

/// Parse a ToUnicode CMap stream into a lookup table.
///
/// Handles `beginbfchar`/`endbfchar` and `beginbfrange`/`endbfrange` with
/// both the single-start-string and `[ ... ]` destination-array forms.
/// Unknown sections are skipped.
pub fn parse_cmap(data: &[u8]) -> CMap {
    let text = String::from_utf8_lossy(data);
    let tokens: Vec<&str> = text.split_whitespace().collect();
    let mut cmap = CMap::default();
    let mut i = 0usize;
    while i < tokens.len() {
        match tokens[i] {
            "beginbfchar" => {
                i += 1;
                while i < tokens.len() && tokens[i] != "endbfchar" {
                    let src = hex_of(tokens.get(i).copied());
                    let dst = hex_of(tokens.get(i + 1).copied());
                    if let (Some(src), Some(dst)) = (src, dst) {
                        cmap.map.insert(src, decode_dst(&dst));
                    }
                    i += 2;
                }
            }
            "beginbfrange" => {
                i += 1;
                while i < tokens.len() && tokens[i] != "endbfrange" {
                    let lo = hex_of(tokens.get(i).copied());
                    let hi = hex_of(tokens.get(i + 1).copied());
                    let next = tokens.get(i + 2).copied();
                    match next {
                        Some(tok) if tok.starts_with('<') => {
                            let dst = hex_of(Some(tok));
                            if let (Some(lo), Some(hi), Some(dst)) = (lo, hi, dst) {
                                cmap.ranges.push((lo, hi, decode_dst(&dst)));
                            }
                            i += 3;
                        }
                        Some(tok) if tok.starts_with('[') => {
                            let mut j = i + 2;
                            let mut dsts = Vec::new();
                            while j < tokens.len() && tokens[j] != "]" {
                                if let Some(d) = hex_of(Some(tokens[j])) {
                                    dsts.push(decode_dst(&d));
                                }
                                j += 1;
                            }
                            if let (Some(lo), Some(hi)) = (lo, hi) {
                                for (k, dst) in dsts.into_iter().enumerate() {
                                    let mut code = lo.clone();
                                    inc_code(&mut code, k as u32);
                                    if code <= hi {
                                        cmap.map.insert(code, dst);
                                    }
                                }
                            }
                            i = j + 1;
                        }
                        _ => i += 3,
                    }
                }
            }
            _ => i += 1,
        }
        i += 1;
    }
    cmap
}

impl CMap {
    /// Look up the Unicode string for a character code.
    pub fn get(&self, code: &[u8]) -> Option<String> {
        if let Some(s) = self.map.get(code) {
            return Some(s.clone());
        }
        for (lo, hi, dst) in &self.ranges {
            if code >= lo && code <= hi {
                let diff = code_diff(code, lo);
                if diff == 0 {
                    return Some(dst.clone());
                }
                // Single destination string: successive codes map to the
                // destination with its final UTF-16BE code unit incremented.
                if let Some(s) = increment_dst(dst, diff) {
                    return Some(s);
                }
            }
        }
        None
    }

    /// True when the CMap contains any mapping (used to detect fallbacks).
    pub fn is_empty(&self) -> bool {
        self.map.is_empty() && self.ranges.is_empty()
    }
}

/// Extract a hex string from a CMap token (`<...>`), stripping brackets.
fn hex_of(tok: Option<&str>) -> Option<Vec<u8>> {
    let tok = tok?.trim();
    if tok.len() < 2 || !tok.starts_with('<') {
        return None;
    }
    let inner = tok
        .trim_start_matches('<')
        .trim_end_matches('>')
        .trim_end_matches(']');
    if inner.len() % 2 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(inner.len() / 2);
    for ch in inner.as_bytes().chunks(2) {
        let hex = std::str::from_utf8(ch).ok()?;
        out.push(u8::from_str_radix(hex, 16).ok()?);
    }
    Some(out)
}

/// Decode a CMap destination hex string into Unicode.
///
/// Order of attempts: UTF-16BE (with optional BOM), UTF-8, Latin-1.
fn decode_dst(bytes: &[u8]) -> String {
    if bytes.len() >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF {
        return utf16be(&bytes[2..]);
    }
    if bytes.len().is_multiple_of(2) && looks_utf16be(bytes) {
        return utf16be(bytes);
    }
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => bytes.iter().map(|&b| b as char).collect(),
    }
}

/// Heuristic: even-length byte strings whose high bytes are mostly zero are
/// ASCII encoded as UTF-16BE (the common producer output). Leading UTF-16
/// surrogate pairs also mark the string as UTF-16BE.
fn looks_utf16be(bytes: &[u8]) -> bool {
    let has_high_surrogate = bytes
        .chunks(2)
        .any(|c| c.len() == 2 && (0xD800..=0xDBFF).contains(&u16::from_be_bytes([c[0], c[1]])));
    if has_high_surrogate {
        return true;
    }
    bytes
        .chunks(2)
        .filter(|c| c.len() == 2 && c[0] == 0)
        .count()
        >= bytes.len() / 2
}

/// Decode big-endian UTF-16, handling surrogate pairs.
fn utf16be(bytes: &[u8]) -> String {
    let units: Vec<u16> = bytes
        .chunks(2)
        .filter(|c| c.len() == 2)
        .map(|c| u16::from_be_bytes([c[0], c[1]]))
        .collect();
    String::from_utf16_lossy(&units)
}

/// Increment a big-endian byte code by `n` (base-256 arithmetic, in place).
fn inc_code(code: &mut [u8], n: u32) {
    let mut carry = n;
    for b in code.iter_mut().rev() {
        let v = *b as u32 + (carry & 0xFF);
        *b = (v & 0xFF) as u8;
        carry = (carry >> 8) + (v >> 8);
        if carry == 0 {
            break;
        }
    }
}

/// Big-endian difference between two equal-length codes.
fn code_diff(code: &[u8], lo: &[u8]) -> u32 {
    let mut diff = 0u32;
    for (a, b) in code.iter().zip(lo.iter()) {
        diff = diff
            .wrapping_mul(256)
            .wrapping_add((*a as u32).wrapping_sub(*b as u32));
    }
    diff
}

/// Increment the final UTF-16BE code unit of a destination string.
fn increment_dst(dst: &str, diff: u32) -> Option<String> {
    if diff == 0 {
        return Some(dst.to_string());
    }
    let units: Vec<u16> = dst.encode_utf16().collect();
    let last = *units.last()? as u32 + diff;
    if last > 0xFFFF {
        return None; // crossed into surrogate territory — out of scope
    }
    let mut out: Vec<u16> = units;
    *out.last_mut()? = last as u16;
    Some(String::from_utf16_lossy(&out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_bfchar_utf16be() {
        let cmap = parse_cmap(b"beginbfchar\n<0001> <0041>\n<0002> <00E9>\nendbfchar".as_slice());
        assert_eq!(cmap.get(&[0x00, 0x01]), Some("A".into()));
        assert_eq!(cmap.get(&[0x00, 0x02]), Some("é".into()));
        assert_eq!(cmap.get(&[0x00, 0x03]), None);
    }

    #[test]
    fn parses_bfchar_utf8_dst() {
        // Some producers write UTF-8 destinations (e.g. <E9> for é).
        let cmap = parse_cmap(b"beginbfchar\n<0001> <E9>\nendbfchar".as_slice());
        assert_eq!(cmap.get(&[0x00, 0x01]), Some("é".into()));
    }

    #[test]
    fn parses_bfrange_incrementing() {
        let cmap = parse_cmap(b"beginbfrange\n<0020> <0022> <0030>\nendbfrange".as_slice());
        assert_eq!(cmap.get(&[0x00, 0x20]), Some("0".into()));
        assert_eq!(cmap.get(&[0x00, 0x21]), Some("1".into()));
        assert_eq!(cmap.get(&[0x00, 0x22]), Some("2".into()));
    }

    #[test]
    fn parses_bfrange_dst_array() {
        let cmap = parse_cmap(
            b"beginbfrange\n<0100> <0102> [ <0041> <0042> <0043> ]\nendbfrange".as_slice(),
        );
        assert_eq!(cmap.get(&[0x01, 0x00]), Some("A".into()));
        assert_eq!(cmap.get(&[0x01, 0x02]), Some("C".into()));
        assert_eq!(cmap.get(&[0x01, 0x03]), None);
    }

    #[test]
    fn surrogate_pair_dst() {
        // U+1F600 (😀) encoded as a UTF-16BE surrogate pair.
        let cmap = parse_cmap(b"beginbfchar\n<0001> <D83DDE00>\nendbfchar".as_slice());
        assert_eq!(cmap.get(&[0x00, 0x01]), Some("😀".into()));
    }
}
