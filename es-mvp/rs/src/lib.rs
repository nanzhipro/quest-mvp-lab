//! esmvp-rs — 目录级 AUTH_OPEN 管控的 Rust 实现。
//!
//! 基于 Endpoint Security 静音反转（`es_invert_muting`）：只接收 watch 目录内的
//! AUTH_OPEN 事件，按 MIME 裁决（png/jpg 拒绝），可选内核授权缓存（`--cache`）。
//! 设计背景与实测结论见 ../SPEC.md。

pub mod app;
pub mod backend;
pub mod cli;
pub mod config;
pub mod decision;
pub mod ffi;
pub mod stats;
