//! Embedded image extraction: JPEG passthrough, FlateDecode + predictor
//! reconstruction re-encoded as PNG for the OCR stage.

use std::io::Write;

use flate2::write::ZlibEncoder;
use flate2::Compression;
use lopdf::{Dictionary, Document, Object, ObjectId};

use super::fonts::{page_resources, resolve_dict, to_i64};
use crate::error::Result;
use crate::types::{ImageItem, ImagePlacement};

/// Build fully-resolved `ImageItem`s for a page from its placements.
///
/// Placements come from the content walker (which owns the CTM); this module
/// resolves the image dictionary, decodes the stream, and produces bytes in a
/// container Vision can decode: JPEG (DCTDecode passthrough) or PNG.
pub fn build_images(
    doc: &Document,
    page_id: ObjectId,
    page_num: u32,
    placements: &[ImagePlacement],
) -> Result<Vec<ImageItem>> {
    let xobjects = page_xobject_map(doc, page_id)?;
    let mut out = Vec::new();
    for p in placements {
        let Ok(obj) = xobjects.get(&p.name) else {
            continue;
        };
        let dict = match resolve_dict(doc, obj) {
            Ok(d) => d,
            Err(_) => continue,
        };
        let Some(xref) = object_number(obj) else {
            continue;
        };
        let width = dict.get(b"Width").ok().and_then(to_i64).unwrap_or(0) as u32;
        let height = dict.get(b"Height").ok().and_then(to_i64).unwrap_or(0) as u32;
        if width == 0 || height == 0 {
            continue;
        }
        let stream = match obj {
            Object::Reference(id) => match doc.get_object(*id) {
                Ok(Object::Stream(s)) => s,
                _ => continue,
            },
            Object::Stream(s) => s,
            _ => continue,
        };
        let filter = filter_name(&stream.dict);

        let (format, data) = match filter.as_deref() {
            Some("DCTDecode") => {
                // JPEG passthrough: the bytes are already a JPEG stream.
                ("jpeg".to_string(), stream.content.clone())
            }
            Some("FlateDecode") => {
                let raw = match stream.decompressed_content() {
                    Ok(r) => r,
                    Err(_) => continue,
                };
                let params = decode_params(&stream.dict);
                let predictor = params.predictor;
                let pixels = if predictor > 1 {
                    apply_predictor(&raw, predictor, params.colors, params.bpc, params.columns)
                } else {
                    raw
                };
                let colorspace = colorspace_name(dict);
                match encode_png(width, height, &colorspace, &pixels) {
                    Some(png) => ("png".to_string(), png),
                    None => continue,
                }
            }
            _ => ("raw".to_string(), stream.content.clone()),
        };

        out.push(ImageItem {
            page: page_num,
            x: p.x,
            y: p.y,
            w: p.w,
            h: p.h,
            xref,
            format,
            width,
            height,
            data,
        });
    }
    Ok(out)
}

/// Page /Resources /XObject map (name → object).
fn page_xobject_map(doc: &Document, page_id: ObjectId) -> Result<Dictionary> {
    let mut out = Dictionary::new();
    if let Some(resources) = page_resources(doc, page_id)? {
        if let Ok(xo) = resources.get(b"XObject") {
            if let Ok(xd) = resolve_dict(doc, xo) {
                for (k, v) in xd.iter() {
                    out.set(k.clone(), v.clone());
                }
            }
        }
    }
    Ok(out)
}

fn object_number(obj: &Object) -> Option<u32> {
    match obj {
        Object::Reference(id) => Some(id.0),
        _ => None,
    }
}

/// First /Filter entry of a stream dictionary.
fn filter_name(dict: &Dictionary) -> Option<String> {
    let f = dict.get(b"Filter").ok()?;
    match f {
        Object::Name(n) => Some(String::from_utf8_lossy(n).into_owned()),
        Object::Array(a) => a
            .first()
            .and_then(|o| o.as_name().ok())
            .map(|n| String::from_utf8_lossy(n).into_owned()),
        _ => None,
    }
}

struct DecodeParams {
    predictor: u32,
    colors: u32,
    bpc: u32,
    columns: u32,
}

fn decode_params(dict: &Dictionary) -> DecodeParams {
    let mut p = DecodeParams {
        predictor: 1,
        colors: 1,
        bpc: 8,
        columns: 0,
    };
    let d = match dict.get(b"DecodeParms") {
        Ok(Object::Dictionary(d)) => d.clone(),
        Ok(Object::Reference(id)) => match dict_ref(dict, *id) {
            Some(d) => d,
            None => return p,
        },
        _ => return p,
    };
    p.predictor = d.get(b"Predictor").ok().and_then(to_i64).unwrap_or(1) as u32;
    p.colors = d.get(b"Colors").ok().and_then(to_i64).unwrap_or(1) as u32;
    p.bpc = d
        .get(b"BitsPerComponent")
        .ok()
        .and_then(to_i64)
        .unwrap_or(8) as u32;
    p.columns = d.get(b"Columns").ok().and_then(to_i64).unwrap_or(1) as u32;
    p
}

// Resolve a DecodeParms reference without a Document handle (we only need the
// dict when it is inline; references are rare and skipped).
fn dict_ref(_dict: &Dictionary, _id: ObjectId) -> Option<Dictionary> {
    None
}

/// Normalize the /ColorSpace name to one of gray / rgb / rgba / cmyk.
fn colorspace_name(dict: &Dictionary) -> String {
    let cs = match dict.get(b"ColorSpace") {
        Ok(Object::Name(n)) => String::from_utf8_lossy(n).into_owned(),
        Ok(Object::Array(a)) => a
            .first()
            .and_then(|o| o.as_name().ok())
            .map(|n| String::from_utf8_lossy(n).into_owned())
            .unwrap_or_default(),
        _ => return "rgb".to_string(),
    };
    match cs.as_str() {
        "DeviceGray" | "CalGray" => "gray".to_string(),
        "DeviceRGB" | "CalRGB" => "rgb".to_string(),
        "DeviceCMYK" => "cmyk".to_string(),
        "ICCBased" => "rgb".to_string(),
        _ => "rgb".to_string(),
    }
}

/// Undo PNG/TIFF predictors from a decoded FlateDecode image stream.
///
/// Predictor 2 is TIFF horizontal differencing; 10–15 are PNG filters
/// (None/Sub/Up/Average/Paeth). Byte-aligned, bpc=8 only.
fn apply_predictor(data: &[u8], predictor: u32, colors: u32, bpc: u32, columns: u32) -> Vec<u8> {
    if bpc != 8 {
        return data.to_vec();
    }
    let bpp = (colors * bpc / 8).max(1) as usize;
    let stride = (colors * bpc * columns / 8) as usize;
    if stride == 0 {
        return data.to_vec();
    }
    match predictor {
        2 => {
            let mut out = data.to_vec();
            for row in 0..out.len() / stride {
                for i in 0..stride {
                    let idx = row * stride + i;
                    let left = if i >= bpp { out[idx - bpp] as i32 } else { 0 };
                    out[idx] = (out[idx] as i32 + left).clamp(0, 255) as u8;
                }
            }
            out
        }
        10..=15 => {
            let mut out = Vec::with_capacity(data.len());
            let mut prev: Vec<u8> = vec![0; stride];
            let mut pos = 0usize;
            while pos < data.len() {
                let ft = data[pos];
                pos += 1;
                let mut row = data[pos..(pos + stride).min(data.len())].to_vec();
                pos += stride;
                if row.len() < stride {
                    break;
                }
                for i in 0..stride {
                    let a = if i >= bpp { row[i - bpp] as i32 } else { 0 };
                    let b = prev[i] as i32;
                    let c = if i >= bpp { prev[i - bpp] as i32 } else { 0 };
                    let val = match ft {
                        0 => row[i] as i32,
                        1 => row[i] as i32 + a,
                        2 => row[i] as i32 + b,
                        3 => row[i] as i32 + (a + b) / 2,
                        4 => {
                            let p = a + b - c;
                            let pa = (p - a).abs();
                            let pb = (p - b).abs();
                            let pc = (p - c).abs();
                            row[i] as i32
                                + if pa <= pb && pa <= pc {
                                    a
                                } else if pb <= pc {
                                    b
                                } else {
                                    c
                                }
                        }
                        _ => row[i] as i32,
                    };
                    row[i] = val.clamp(0, 255) as u8;
                }
                out.extend_from_slice(&row);
                prev = row;
            }
            out
        }
        _ => data.to_vec(),
    }
}

// ---------------------------------------------------------------------------
// Minimal PNG encoder (RGB / RGBA / gray / naive CMYK→RGB)
// ---------------------------------------------------------------------------

fn encode_png(width: u32, height: u32, colorspace: &str, pixels: &[u8]) -> Option<Vec<u8>> {
    let (channels, color_type) = match colorspace {
        "gray" => (1usize, 0u8),
        "rgba" => (4, 6),
        "cmyk" => (3, 2), // converted to RGB below
        _ => (3, 2),
    };
    let stride = (width as usize) * channels;
    if pixels.len() < stride * (height as usize) {
        return None;
    }

    // Build RGB(A) rows with a leading filter byte (0 = None).
    let mut raw = Vec::with_capacity(stride * height as usize + height as usize);
    for y in 0..height as usize {
        raw.push(0u8);
        let row = &pixels[y * stride..(y + 1) * stride];
        if colorspace == "cmyk" {
            for c in row.chunks(4) {
                let (c, m, yk, k) = (
                    c[0] as f32 / 255.0,
                    c[1] as f32 / 255.0,
                    c[2] as f32 / 255.0,
                    c[3] as f32 / 255.0,
                );
                let r = ((1.0 - c) * (1.0 - k) * 255.0) as u8;
                let g = ((1.0 - m) * (1.0 - k) * 255.0) as u8;
                let b = ((1.0 - yk) * (1.0 - k) * 255.0) as u8;
                raw.extend_from_slice(&[r, g, b]);
            }
        } else {
            raw.extend_from_slice(row);
        }
    }

    let mut enc = ZlibEncoder::new(Vec::new(), Compression::default());
    enc.write_all(&raw).ok()?;
    let idat = enc.finish().ok()?;

    let mut png = Vec::new();
    png.extend_from_slice(&[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]);
    chunk(&mut png, b"IHDR", &{
        let mut h = Vec::with_capacity(13);
        h.extend_from_slice(&width.to_be_bytes());
        h.extend_from_slice(&height.to_be_bytes());
        h.push(8); // bit depth
        h.push(color_type);
        h.extend_from_slice(&[0, 0, 0]); // compression, filter, interlace
        h
    });
    chunk(&mut png, b"IDAT", &idat);
    chunk(&mut png, b"IEND", &[]);
    Some(png)
}

fn chunk(out: &mut Vec<u8>, tag: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(tag);
    out.extend_from_slice(data);
    out.extend_from_slice(&crc32(tag.iter().chain(data.iter()).copied()).to_be_bytes());
}

/// CRC-32 (IEEE) for PNG chunk integrity.
fn crc32(bytes: impl Iterator<Item = u8>) -> u32 {
    let mut table = [0u32; 256];
    for (i, slot) in table.iter_mut().enumerate() {
        let mut c = i as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 {
                0xEDB8_8320 ^ (c >> 1)
            } else {
                c >> 1
            };
        }
        *slot = c;
    }
    let mut crc = 0xFFFF_FFFFu32;
    for b in bytes {
        crc = table[((crc ^ b as u32) & 0xFF) as usize] ^ (crc >> 8);
    }
    crc ^ 0xFFFF_FFFF
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn png_encoder_roundtrip_signature() {
        let px: Vec<u8> = (0..(3 * 4)).map(|i| (i * 40) as u8).collect();
        let png = encode_png(2, 2, "rgb", &px).unwrap();
        assert_eq!(&png[..8], &[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]);
        assert!(png.windows(4).any(|w| w == b"IHDR"));
        assert!(png.windows(4).any(|w| w == b"IDAT"));
        assert!(png.windows(4).any(|w| w == b"IEND"));
    }

    #[test]
    fn predictor_none_passthrough() {
        let data = vec![1u8, 2, 3, 4];
        assert_eq!(apply_predictor(&data, 1, 3, 8, 4), data);
    }

    #[test]
    fn predictor_tiff_reverses() {
        // One row, 4 columns, gray: [10, 15, 18, 20] differenced → [10,5,3,2].
        let diff = vec![10u8, 5, 3, 2];
        let out = apply_predictor(&diff, 2, 1, 8, 4);
        assert_eq!(out, vec![10, 15, 18, 20]);
    }

    #[test]
    fn predictor_png_sub_reverses() {
        // Row with filter byte 1 (Sub) then differenced bytes.
        let encoded = vec![1u8, 10, 5, 3, 2];
        let out = apply_predictor(&encoded, 12, 1, 8, 4);
        assert_eq!(out, vec![10, 15, 18, 20]);
    }

    #[test]
    fn predictor_png_up_reverses() {
        // Two rows: second row filtered with Up referencing the first.
        let first = [10u8, 15, 18, 20];
        let second_raw = [11u8, 16, 20, 22];
        let second_up: Vec<u8> = second_raw
            .iter()
            .zip(first.iter())
            .map(|(a, b)| a - b)
            .collect();
        let mut encoded = vec![0u8];
        encoded.extend_from_slice(&first);
        encoded.push(2u8); // Up
        encoded.extend_from_slice(&second_up);
        let out = apply_predictor(&encoded, 15, 1, 8, 4);
        assert_eq!(out, vec![10, 15, 18, 20, 11, 16, 20, 22]);
    }
}
