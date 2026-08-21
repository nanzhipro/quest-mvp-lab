//! Layer 3 — ObjC Block 桥接（block2::RcBlock）
//!
//! AppKit/Foundation 大量 API 用 Block 做回调（completion handler、通知观察者）。
//! Rust 侧用 block2::RcBlock 构造等价 Block。本层演示两种模式：
//!
//! a) NSNotificationCenter 块观察者：block 在通知投递时被同步调用
//!    （对应 platform-macos 对 NSNotification 的监听类用法）；
//! b) NSWorkspace 启动应用 completion handler：异步回调 → std channel
//!    同步化 + 超时（逐行对应 apps/nsworkspace.rs 的完整模式，
//!    含 30s 超时与进程对账 fallback 的核心思路）。

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use block2::RcBlock;
use objc2::runtime::AnyObject;
use objc2::{class, msg_send};
use objc2_app_kit::{NSRunningApplication, NSWorkspace, NSWorkspaceOpenConfiguration};
use objc2_foundation::{MainThreadMarker, NSString};
use serde_json::json;

/// (a) 通知观察者：注册 block → 投递通知 → block 内写结果。
fn notification_observer() -> (bool, i64) {
    let center: *mut AnyObject = unsafe { msg_send![class!(NSNotificationCenter), defaultCenter] };
    if center.is_null() {
        return (false, -1);
    }
    let name = NSString::from_str("com.bridge.demo.layer3");

    // block2：RcBlock 是 Copy 语义的引用计数包装。参数类型用 *mut AnyObject
    // 匹配 ObjC 编码 `@`（nsworkspace.rs make_completion_block 同款手法）。
    // 注意：RcBlock::new 要求 'static，必须 move 捕获 → 用 Arc 共享计数器。
    let fired = std::sync::Arc::new(AtomicI64::new(0));
    let fired_shared = fired.clone();
    let block = RcBlock::new(move |_note: *mut AnyObject| {
        fired_shared.fetch_add(1, Ordering::SeqCst);
    });

    let observer: *mut AnyObject = unsafe {
        msg_send![center,
            addObserverForName: &*name
            object: std::ptr::null_mut::<AnyObject>()
            queue: std::ptr::null_mut::<AnyObject>()
            usingBlock: &*block
        ]
    };
    if observer.is_null() {
        return (false, -1);
    }

    // 投递；nil queue 时 block 在投递线程同步执行
    unsafe {
        let _: () = msg_send![center,
            postNotificationName: &*name
            object: std::ptr::null_mut::<AnyObject>()
        ];
    }

    let count = fired.load(Ordering::SeqCst);
    // 反注册（真实代码 teardown 必备）。
    //
    // 坑（本 MVP 实测踩中）：`addObserverForName:object:queue:usingBlock:` 返回
    // 的是 +0 借用指针（头文件无 NS_RETURNS_RETAINED，MRC 语义下为 autoreleased），
    // 强引用只存在于通知中心内部。removeObserver: 会释放中心的引用——若在此之后
    // 再对返回的 token 手动 release，就是对已释放对象发消息 → EXC_BREAKPOINT
    // (brk #0xc472 in object_getClass)。正确做法：只 removeObserver，不 release。
    unsafe {
        let _: () = msg_send![center, removeObserver: observer];
    }
    (count == 1, count)
}
/// (b) NSWorkspace 启动应用 + completion handler 同步化。
///
/// 完整复刻 nsworkspace.rs 的骨架：
/// 1. 构造 NSWorkspaceOpenConfiguration（activates=false 后台启动）；
/// 2. RcBlock 捕获 mpsc 发送端，Cocoa 在任意线程回调；
/// 3. 主线程轮询 channel + 超时（30s）；
/// 4. 回调未达时用 runningApplicationsWithBundleIdentifier 对账。
fn launch_with_completion() -> Result<i64, String> {
    let ws = unsafe { NSWorkspace::sharedWorkspace() };

    // bundle id 解析成 NSURL（Cryptex 应用必须保留 LaunchServices 返回的 NSURL）
    let bid = NSString::from_str("com.apple.calculator");
    let url = unsafe { ws.URLForApplicationWithBundleIdentifier(&bid) }
        .ok_or_else(|| "Calculator 不存在（不该发生）".to_string())?;

    let config = unsafe { NSWorkspaceOpenConfiguration::configuration() };
    unsafe {
        config.setActivates(false); // 后台启动，不抢焦点
        config.setAddsToRecentItems(false);
    }

    let (tx, rx) = mpsc::sync_channel::<Result<i64, String>>(1);
    // Cocoa 回调可能在其他线程：Arc<Mutex<Option<Sender>>>，发一次即关闭
    let tx = std::sync::Arc::new(std::sync::Mutex::new(Some(tx)));

    let block = RcBlock::new(
        move |app_ptr: *mut NSRunningApplication, err_ptr: *mut objc2_foundation::NSError| {
            let result = unsafe {
                if !err_ptr.is_null() {
                    let desc = (*err_ptr).localizedDescription().to_string();
                    Err(format!("Cocoa error: {desc}"))
                } else if !app_ptr.is_null() {
                    Ok((*app_ptr).processIdentifier() as i64)
                } else {
                    Err("no app, no error".into())
                }
            };
            if let Some(sender) = tx.lock().ok().and_then(|mut g| g.take()) {
                let _ = sender.send(result);
            }
        },
    );

    unsafe {
        ws.openApplicationAtURL_configuration_completionHandler(&url, &config, Some(&block));
    }

    // 同步等待：30s 超时，50ms 轮询 + 进程对账
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        match rx.try_recv() {
            Ok(r) => return r,
            Err(mpsc::TryRecvError::Empty) => {}
            Err(mpsc::TryRecvError::Disconnected) => {
                return Err("callback channel closed".into());
            }
        }
        // 对账 fallback：LaunchServices 接受请求但不回调后台进程时，
        // 直接查运行中的应用（nsworkspace.rs Reconciliation 简化版）
        let running =
            unsafe { NSRunningApplication::runningApplicationsWithBundleIdentifier(&bid) };
        let pid = unsafe {
            (0..running.count())
                .map(|i| running.objectAtIndex(i).processIdentifier())
                .find(|p| *p > 0)
        };
        if let Some(p) = pid {
            return Ok(p as i64);
        }
        if Instant::now() >= deadline {
            return Err("30s 超时，启动未完成".into());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

pub fn run() -> serde_json::Value {
    let _mtm = MainThreadMarker::new().expect("Layer3 必须在主线程运行");

    let (notif_ok, notif_count) = notification_observer();

    // 用 NSURL 引用而非 bundle id 再走一遍 openURLs 路径？——保持单一路径即可
    let launch = match launch_with_completion() {
        Ok(pid) => json!({ "ok": true, "calculator_pid": pid }),
        Err(e) => json!({ "ok": false, "error": e }),
    };

    let ok = notif_ok && launch["ok"] == true;
    json!({
        "ok": ok,
        "notification_block": { "ok": notif_ok, "fired_count": notif_count },
        "workspace_launch": launch,
    })
}
