//! 桥接层模块集合：每一层对应 platform-macos 中的一类桥接手法。

pub mod l1_objc2_safe;
pub mod l2_raw_msg_send;
pub mod l3_blocks;
pub mod l4_c_ffi;
pub mod l5_swift_dylib;
pub mod l6_main_thread;
