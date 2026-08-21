//! Contract tests for the OpenAI-compatible wire protocol types.

use ds_vision::protocol::{
    ApiErrorResponse, ChatMessage, ChatRequest, ChatResponse, ContentBlock, FileObject,
};

#[test]
fn chat_request_serializes_text_block() {
    let req = ChatRequest::new(
        "deepseek-v4-flash-vision-exp",
        vec![ChatMessage::user_text("你好")],
    );
    let json = serde_json::to_value(&req).unwrap();
    assert_eq!(json["model"], "deepseek-v4-flash-vision-exp");
    assert_eq!(json["messages"][0]["role"], "user");
    assert_eq!(json["messages"][0]["content"][0]["type"], "text");
    assert_eq!(json["messages"][0]["content"][0]["text"], "你好");
}

#[test]
fn chat_request_serializes_image_url_block_with_detail() {
    let block = ContentBlock::image_url("data:image/jpeg;base64,AAAA", Some("low".into()));
    let msg = ChatMessage::user(vec![block]);
    let req = ChatRequest::new("m", vec![msg]);
    let json = serde_json::to_value(&req).unwrap();
    let c = &json["messages"][0]["content"][0];
    assert_eq!(c["type"], "image_url");
    assert_eq!(c["image_url"]["url"], "data:image/jpeg;base64,AAAA");
    assert_eq!(c["image_url"]["detail"], "low");
}

#[test]
fn chat_request_serializes_file_block() {
    let block = ContentBlock::file("file-api-abc123");
    let req = ChatRequest::new("m", vec![ChatMessage::user(vec![block])]);
    let json = serde_json::to_value(&req).unwrap();
    let c = &json["messages"][0]["content"][0];
    assert_eq!(c["type"], "file");
    assert_eq!(c["file_id"], "file-api-abc123");
    // 无 detail / 无关字段不得出现
    assert!(c.get("detail").is_none());
}

#[test]
fn image_url_without_detail_omits_the_field() {
    let block = ContentBlock::image_url("data:image/png;base64,BBBB", None);
    let req = ChatRequest::new("m", vec![ChatMessage::user(vec![block])]);
    let json = serde_json::to_value(&req).unwrap();
    let c = &json["messages"][0]["content"][0];
    assert!(c["image_url"].get("detail").is_none());
}

#[test]
fn chat_response_parses_usage_and_content() {
    let body = r#"{
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "deepseek-v4-flash-vision-exp",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "这是漫画页", "reasoning_content": "先看画面"},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 812, "completion_tokens": 120, "total_tokens": 932}
    }"#;
    let resp: ChatResponse = serde_json::from_str(body).unwrap();
    assert_eq!(resp.choices.len(), 1);
    assert_eq!(
        resp.choices[0].message.content.as_deref(),
        Some("这是漫画页")
    );
    assert_eq!(
        resp.choices[0].message.reasoning_content.as_deref(),
        Some("先看画面")
    );
    assert_eq!(resp.usage.total_tokens, 932);
    assert_eq!(resp.text(), Some("这是漫画页"));
}

#[test]
fn chat_response_may_have_no_content() {
    let body = r#"{"id":"c","object":"chat.completion","created":1,"model":"m",
        "choices":[{"index":0,"message":{"role":"assistant","content":null},"finish_reason":"stop"}],
        "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}"#;
    let resp: ChatResponse = serde_json::from_str(body).unwrap();
    assert_eq!(resp.text(), None);
}

#[test]
fn error_response_parses_code_and_message() {
    let body = r#"{"error":{"message":"This model does not support image","type":"invalid_request_error","code":"invalid_request_error"}}"#;
    let err: ApiErrorResponse = serde_json::from_str(body).unwrap();
    assert_eq!(err.error.code.as_deref(), Some("invalid_request_error"));
    assert!(err.error.message.contains("does not support image"));
}

#[test]
fn file_object_parses_full_fields() {
    let body = r#"{"id":"file-api-0a1b2c3d4e5f60718293a4b5c6d7e8f9","object":"file","bytes":102400,
        "created_at":1700000000,"filename":"image.jpg","purpose":"user_data","expires_at":1702600000}"#;
    let f: FileObject = serde_json::from_str(body).unwrap();
    assert_eq!(f.id, "file-api-0a1b2c3d4e5f60718293a4b5c6d7e8f9");
    assert_eq!(f.filename, "image.jpg");
    assert_eq!(f.purpose, "user_data");
    assert_eq!(f.bytes, 102400);
    assert_eq!(f.expires_at, Some(1702600000));
}

#[test]
fn file_object_expires_at_optional() {
    let body = r#"{"id":"file-api-x","object":"file","bytes":1,"created_at":1,"filename":"a.jpg","purpose":"user_data"}"#;
    let f: FileObject = serde_json::from_str(body).unwrap();
    assert_eq!(f.expires_at, None);
}
