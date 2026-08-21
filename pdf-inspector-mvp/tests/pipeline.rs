//! Integration tests: run the pipeline over a programmatically generated PDF.

use lopdf::{Dictionary, Document, Object, Stream};

/// Build a minimal one-page PDF with text + an embedded JPEG-ish image.
fn build_pdf() -> Vec<u8> {
    let mut doc = Document::with_version("1.7");

    // Font: base-14 Helvetica with WinAnsi encoding.
    let font_id = doc.add_object(Object::Dictionary({
        let mut d = Dictionary::new();
        d.set(b"Type", Object::Name(b"Font".to_vec()));
        d.set(b"Subtype", Object::Name(b"Type1".to_vec()));
        d.set(b"BaseFont", Object::Name(b"Helvetica".to_vec()));
        d.set(b"Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
        d
    }));

    // A tiny fake JPEG (DCTDecode stream) — only metadata is inspected here.
    let image_id = doc.add_object(Object::Stream(Stream::new(
        {
            let mut d = Dictionary::new();
            d.set(b"Type", Object::Name(b"XObject".to_vec()));
            d.set(b"Subtype", Object::Name(b"Image".to_vec()));
            d.set(b"Width", Object::Integer(2));
            d.set(b"Height", Object::Integer(2));
            d.set(b"ColorSpace", Object::Name(b"DeviceGray".to_vec()));
            d.set(b"BitsPerComponent", Object::Integer(8));
            d.set(b"Filter", Object::Name(b"DCTDecode".to_vec()));
            d
        },
        vec![0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0xFF, 0xD9],
    )));

    let fonts = doc.add_object(Object::Dictionary({
        let mut d = Dictionary::new();
        d.set(b"F1", Object::Reference(font_id));
        d
    }));
    let xobjects = doc.add_object(Object::Dictionary({
        let mut d = Dictionary::new();
        d.set(b"Im1", Object::Reference(image_id));
        d
    }));
    let resources = doc.add_object(Object::Dictionary({
        let mut d = Dictionary::new();
        d.set(b"Font", Object::Reference(fonts));
        d.set(b"XObject", Object::Reference(xobjects));
        d
    }));

    let content = concat!(
        "BT /F1 24 Tf 72 720 Td (Hello PDF) Tj ET\n",
        "BT /F1 12 Tf 72 690 Td (Body line one) Tj 0 -14 Td (Body line two) Tj ET\n",
        "q 100 0 0 100 72 400 cm /Im1 Do Q\n",
    );

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

    let mut bytes = Vec::new();
    doc.save_to(&mut bytes).unwrap();
    bytes
}

#[test]
fn full_pipeline_on_generated_pdf() {
    let bytes = build_pdf();
    let doc = Document::load_mem(&bytes).unwrap();

    // Classification.
    let detection = pdf_inspector_mvp::detector::detect_document(&doc).unwrap();
    assert_eq!(detection.pdf_type, pdf_inspector_mvp::PdfType::TextBased);
    assert_eq!(detection.page_count, 1);
    assert!(detection.ocr_recommended, "has an image → OCR may matter");

    // Extraction: heading + two body lines + one image.
    let pages = pdf_inspector_mvp::extractor::extract_pages(&doc).unwrap();
    assert_eq!(pages.len(), 1);
    let page = &pages[0];
    assert_eq!(page.text_op_count, 3);
    assert_eq!(page.image_op_count, 1);
    let text = pdf_inspector_mvp::extractor::page_text(page);
    assert!(text.contains("Hello PDF"));
    assert!(text.contains("Body line one"));
    assert_eq!(page.images.len(), 1);
    assert_eq!(page.images[0].format, "jpeg");
    assert_eq!(page.images[0].width, 2);
    assert!(
        (page.images[0].w - 100.0).abs() < 0.1,
        "ctm-scaled placement"
    );

    // Markdown: heading via font-size ratio, image placeholder.
    let report =
        pdf_inspector_mvp::process_document(&doc, &pdf_inspector_mvp::PdfOptions::default())
            .unwrap();
    let md = report.markdown.unwrap();
    assert!(md.contains("# Hello PDF"), "{md}");
    assert!(md.contains("Body line one"), "{md}");
    assert!(md.contains("Body line two"), "{md}");
    assert!(md.contains("!["), "{md}");
    assert!(md.contains("page-1-"), "{md}");
}
