//! Integration tests for `DeepSeekClient` against a mock HTTP server.
//! Covers both image input paths: base64 inline (chat) and Files API upload.
//!
//! httpmock 0.7 dropped request-body introspection (`mock.matches()`), so the
//! tests assert request payloads declaratively via `when.json_body(...)` /
//! `when.body_contains(...)` matchers. `json_body` expects a
//! `serde_json::Value` — passing a `&str` double-encodes it as a JSON string.

use ds_vision::client::{DeepSeekClient, ImageInput};
use ds_vision::config::Config;
use ds_vision::protocol::{ChatMessage, ChatRequest, ContentBlock};
use httpmock::prelude::*;
use std::path::PathBuf;

fn test_config(base_url: &str) -> Config {
    Config::new(
        "sk-test-123".into(),
        "deepseek-v4-flash-vision-exp".into(),
        base_url.into(),
    )
}

fn sample_jpeg() -> Vec<u8> {
    // 合法 JPEG 魔数 + 少量字节，用于上传/内联测试
    vec![
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00,
    ]
}

fn ok_response() -> serde_json::Value {
    serde_json::json!({
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    })
}

#[test]
fn chat_sends_authorization_and_parses_response() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/chat/completions")
            .header("authorization", "Bearer sk-test-123")
            .header("content-type", "application/json");
        then.status(200).json_body(serde_json::json!({
            "id": "chatcmpl-x",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek-v4-flash-vision-exp",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "画面里有一条龙"},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 400, "completion_tokens": 30, "total_tokens": 430}
        }));
    });

    let client = DeepSeekClient::new(test_config(&server.base_url()));
    let req = ChatRequest::new(
        "deepseek-v4-flash-vision-exp",
        vec![ChatMessage::user_text("这张图里有什么？")],
    );
    let resp = client.chat(&req).unwrap();

    mock.assert();
    assert_eq!(resp.text(), Some("画面里有一条龙"));
    assert_eq!(resp.usage.total_tokens, 430);
}

#[test]
fn chat_with_inline_image_sends_image_url_block() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/chat/completions")
            .json_body(serde_json::json!({
                "model": "deepseek-v4-flash-vision-exp",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "描述一下"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
                    ]
                }]
            }));
        then.status(200).json_body(ok_response());
    });

    let client = DeepSeekClient::new(test_config(&server.base_url()));
    let resp = client
        .chat_with_images(
            "描述一下",
            &[ImageInput::DataUrl {
                data_url: "data:image/jpeg;base64,AAAA".into(),
            }],
            None,
            false,
        )
        .unwrap();

    mock.assert();
    assert_eq!(resp.text(), Some("ok"));
}

#[test]
fn chat_with_file_id_sends_file_block() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/chat/completions")
            .json_body(serde_json::json!({
                "model": "deepseek-v4-flash-vision-exp",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看看"},
                        {"type": "file", "file_id": "file-api-xyz"}
                    ]
                }]
            }));
        then.status(200).json_body(ok_response());
    });

    let client = DeepSeekClient::new(test_config(&server.base_url()));
    let resp = client
        .chat_with_images(
            "看看",
            &[ImageInput::FileId {
                file_id: "file-api-xyz".into(),
            }],
            None,
            false,
        )
        .unwrap();

    mock.assert();
    assert_eq!(resp.text(), Some("ok"));
}

#[test]
fn chat_error_surfaces_api_message_without_key() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST).path("/chat/completions");
        then.status(400).json_body(serde_json::json!({
            "error": {
                "message": "This model does not support image",
                "type": "invalid_request_error",
                "code": "invalid_request_error"
            }
        }));
    });

    let client = DeepSeekClient::new(test_config(&server.base_url()));
    let req = ChatRequest::new("m", vec![ChatMessage::user_text("hi")]);
    let err = client.chat(&req).unwrap_err().to_string();

    mock.assert();
    assert!(
        err.contains("does not support image"),
        "should surface API message: {err}"
    );
    assert!(
        !err.contains("sk-test-123"),
        "must never leak the key: {err}"
    );
}

#[test]
fn upload_file_sends_multipart_and_parses_file_object() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/files")
            .header("authorization", "Bearer sk-test-123")
            // multipart 原始体中必须携带 purpose 与文件名
            .body_contains("user_data")
            .body_contains("sample.jpg");
        then.status(200).json_body(serde_json::json!({
            "id": "file-api-0a1b2c3d4e5f60718293a4b5c6d7e8f9",
            "object": "file",
            "bytes": 11,
            "created_at": 1700000000,
            "filename": "sample.jpg",
            "purpose": "user_data"
        }));
    });

    // 临时 JPEG 文件
    let dir = std::env::temp_dir().join(format!("ds-vision-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path: PathBuf = dir.join("sample.jpg");
    std::fs::write(&path, sample_jpeg()).unwrap();

    let client = DeepSeekClient::new(test_config(&server.base_url()));
    let file = client.upload_file(&path).unwrap();

    mock.assert();
    assert_eq!(file.id, "file-api-0a1b2c3d4e5f60718293a4b5c6d7e8f9");
    assert_eq!(file.filename, "sample.jpg");
    assert_eq!(file.purpose, "user_data");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn chat_with_images_builds_text_plus_image_blocks_with_max_tokens() {
    let server = MockServer::start();
    let mock = server.mock(|when, then| {
        when.method(POST)
            .path("/chat/completions")
            .json_body(serde_json::json!({
                "model": "deepseek-v4-flash-vision-exp",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "对比"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
                        {"type": "file", "file_id": "file-api-xyz"}
                    ]
                }]
            }));
        then.status(200).json_body(ok_response());
    });

    let client = DeepSeekClient::new(test_config(&server.base_url()));
    let resp = client
        .chat_with_images(
            "对比",
            &[
                ImageInput::DataUrl {
                    data_url: "data:image/jpeg;base64,AAAA".into(),
                },
                ImageInput::FileId {
                    file_id: "file-api-xyz".into(),
                },
            ],
            Some(1024),
            false,
        )
        .unwrap();

    mock.assert();
    assert_eq!(resp.text(), Some("ok"));
}

#[test]
fn content_block_constructors_produce_expected_json() {
    let text = ContentBlock::text("你好");
    let img = ContentBlock::image_url("data:image/webp;base64,BBBB", Some("high".into()));
    let file = ContentBlock::file("file-api-1");
    let msg = ChatMessage::user(vec![text, img, file]);
    let v = serde_json::to_value(msg).unwrap();
    assert_eq!(v["content"][0]["type"], "text");
    assert_eq!(v["content"][1]["type"], "image_url");
    assert_eq!(v["content"][1]["image_url"]["detail"], "high");
    assert_eq!(v["content"][2]["type"], "file");
}
