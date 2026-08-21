//! Tests for image format detection and base64 data-URL encoding.

use ds_vision::image::{detect_format, to_data_url, ImageFormat};

#[test]
fn detects_jpeg_magic_bytes() {
    assert_eq!(
        detect_format(&[0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10]),
        Some(ImageFormat::Jpeg)
    );
}

#[test]
fn detects_png_magic_bytes() {
    assert_eq!(detect_format(b"\x89PNG\r\n\x1a\n"), Some(ImageFormat::Png));
}

#[test]
fn detects_gif_magic_bytes() {
    assert_eq!(detect_format(b"GIF89a"), Some(ImageFormat::Gif));
    assert_eq!(detect_format(b"GIF87a"), Some(ImageFormat::Gif));
}

#[test]
fn detects_webp_magic_bytes() {
    assert_eq!(
        detect_format(b"RIFF\x00\x00\x00\x00WEBPVP8 "),
        Some(ImageFormat::WebP)
    );
}

#[test]
fn unknown_bytes_return_none() {
    assert_eq!(detect_format(b"hello world"), None);
    assert_eq!(detect_format(b""), None);
    assert_eq!(detect_format(b"RIFFWEBPVP8"), None); // 缺 RIFF 长度字段的残缺头
}

#[test]
fn to_data_url_builds_correct_prefix_and_roundtrips() {
    let raw = [0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x01, 0x02, 0x03];
    let url = to_data_url(&raw).unwrap();
    assert!(url.starts_with("data:image/jpeg;base64,"));
    let encoded = url.strip_prefix("data:image/jpeg;base64,").unwrap();
    use base64::Engine;
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .unwrap();
    assert_eq!(decoded, raw);
}

#[test]
fn webp_gets_correct_mime() {
    let raw = b"RIFF\x24\x00\x00\x00WEBPVP8X";
    let url = to_data_url(raw).unwrap();
    assert!(url.starts_with("data:image/webp;base64,"));
}

#[test]
fn unknown_format_data_url_is_rejected() {
    let err = to_data_url(b"not an image").unwrap_err();
    assert!(
        err.to_string().contains("不支持"),
        "error should name the unsupported format: {err}"
    );
}
