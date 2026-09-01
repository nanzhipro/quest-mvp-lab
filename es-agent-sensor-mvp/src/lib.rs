//! es-agent-sensor — Endpoint Security（仅 NOTIFY）AI Agent 行为感知传感器。
//!
//! lib 承载全部可测逻辑；bin（main.rs）只做 CLI 解析与组装。
//!
//! 数据流：ES 内核事件 → C shim 字段提取（扁平 C 结构）→ FFI 拷贝 →
//! crossbeam 有界 channel（背压丢弃计数）→ 写线程 decode/进程树/匹配 →
//! AgentEvent JSONL（stdout/文件）。net-poller 轮询 socket 快照补偿
//! ESF 的 TCP/UDP 盲区。

pub mod backend;
pub mod cli;
pub mod decode;
pub mod error;
pub mod ffi;
pub mod matcher;
pub mod metrics;
pub mod netpoll;
pub mod pipeline;
pub mod proctree;
pub mod schema;
pub mod sink;
pub mod sysproc;
