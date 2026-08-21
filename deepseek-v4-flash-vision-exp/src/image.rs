//! Image handling: magic-byte format detection and base64 data-URL encoding.

use std::path::Path;

/// Image formats accepted by the DeepSeek vision API.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ImageFormat {
    Jpeg,
    Png,
    Gif,
    WebP,
}

impl ImageFormat {
    /// MIME type used in `data:` URLs and multipart uploads.
    pub fn mime(&self) -> &'static str {
        match self {
            ImageFormat::Jpeg => "image/jpeg",
            ImageFormat::Png => "image/png",
            ImageFormat::Gif => "image/gif",
            ImageFormat::WebP => "image/webp",
        }
    }
}

/// Detect the image format from magic bytes (content sniffing, not filename).
///
/// The API judges format by actual content, so we do the same.
pub fn detect_format(bytes: &[u8]) -> Option<ImageFormat> {
    if bytes.len() >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF {
        return Some(ImageFormat::Jpeg);
    }
    if bytes.len() >= 8 && bytes[..8] == *b"\x89PNG\r\n\x1a\n" {
        return Some(ImageFormat::Png);
    }
    if bytes.len() >= 6
        && &bytes[..4] == b"GIF8"
        && (bytes[4] == b'7' || bytes[4] == b'9')
        && bytes[5] == b'a'
    {
        return Some(ImageFormat::Gif);
    }
    // RIFF<size>WEBP (12-byte header)
    if bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        return Some(ImageFormat::WebP);
    }
    None
}

/// Encode raw image bytes into a `data:<mime>;base64,<payload>` URL.
pub fn to_data_url(bytes: &[u8]) -> anyhow::Result<String> {
    use base64::Engine;
    let format = detect_format(bytes).ok_or_else(|| {
        anyhow::anyhow!("不支持的图片格式：无法从内容识别（支持 JPEG/PNG/GIF/WebP）")
    })?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
    Ok(format!("data:{};base64,{}", format.mime(), encoded))
}

/// Inline (base64) size cap: 32 MiB per image, 48 MiB total request body.
pub const MAX_INLINE_BYTES: usize = 32 * 1024 * 1024;

/// Read a file from disk and encode it as a base64 data URL.
///
/// Enforces the 32 MiB inline limit with a clear error before any upload.
pub fn file_to_data_url(path: &Path) -> anyhow::Result<String> {
    let bytes =
        std::fs::read(path).map_err(|e| anyhow::anyhow!("读取图片失败 {}: {e}", path.display()))?;
    if bytes.len() > MAX_INLINE_BYTES {
        anyhow::bail!(
            "图片 {} 大小 {:.1} MiB 超过内联限制 32 MiB，请改用 Files API 路径",
            path.display(),
            bytes.len() as f64 / (1024.0 * 1024.0)
        );
    }
    to_data_url(&bytes)
}
