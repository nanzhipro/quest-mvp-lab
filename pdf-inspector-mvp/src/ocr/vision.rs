//! macOS Vision OCR backend, implemented with pure-Rust objc2 bindings.

use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::AnyThread;
use objc2_foundation::{NSArray, NSData, NSDictionary, NSString};
use objc2_vision::{
    VNImageOption, VNImageRequestHandler, VNRecognizeTextRequest, VNRequest,
    VNRequestTextRecognitionLevel,
};

use super::{OcrBackend, OcrText};
use crate::error::{PdfError, Result};

/// Vision-backed OCR: `VNRecognizeTextRequest` with accurate recognition.
pub struct VisionOcr {
    recognition_level: VNRequestTextRecognitionLevel,
}

impl VisionOcr {
    /// Create a backend with accurate recognition + language correction.
    pub fn new() -> Self {
        Self {
            recognition_level: VNRequestTextRecognitionLevel::Accurate,
        }
    }
}

impl Default for VisionOcr {
    fn default() -> Self {
        Self::new()
    }
}

impl OcrBackend for VisionOcr {
    fn name(&self) -> &'static str {
        "vision"
    }

    fn recognize(&self, format: &str, data: &[u8]) -> Result<Vec<OcrText>> {
        if !matches!(format, "jpeg" | "png") {
            return Err(PdfError::Ocr(format!(
                "Vision backend accepts jpeg/png, got {format}"
            )));
        }

        // 1. Wrap bytes in NSData and build the request handler.
        let nsdata = NSData::from_vec(data.to_vec());
        let opts: Retained<NSDictionary<VNImageOption, AnyObject>> = NSDictionary::dictionary();
        let handler = VNImageRequestHandler::initWithData_options(
            VNImageRequestHandler::alloc(),
            &nsdata,
            &opts,
        );

        // 2. Configure the text-recognition request.
        let request = VNRecognizeTextRequest::new();
        request.setRecognitionLevel(self.recognition_level);
        request.setUsesLanguageCorrection(true);

        // 3. Perform synchronously.
        let typed: Retained<NSArray<VNRecognizeTextRequest>> =
            NSArray::from_retained_slice(std::slice::from_ref(&request));
        // Generic parameters are compile-time only; the ObjC class is NSArray
        // in both cases, so an unchecked cast is safe here.
        let requests: Retained<NSArray<VNRequest>> =
            unsafe { Retained::<NSArray<VNRecognizeTextRequest>>::cast_unchecked(typed) };
        handler
            .performRequests_error(&requests)
            .map_err(|e| PdfError::Ocr(format!("Vision request failed: {e}")))?;

        // 4. Collect recognized strings.
        let mut out = Vec::new();
        if let Some(results) = request.results() {
            for observation in results.iter() {
                let candidates = observation.topCandidates(1);
                for candidate in candidates.iter() {
                    let text: Retained<NSString> = candidate.string();
                    out.push(OcrText {
                        text: text.to_string(),
                        x: 0.0,
                        y: 0.0,
                        w: 0.0,
                        h: 0.0,
                    });
                }
            }
        }
        Ok(out)
    }
}
