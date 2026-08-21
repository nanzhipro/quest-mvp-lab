# rust-appkit-bridge-mvp — 一键构建 / 验证
# 用法: make           构建全部（swift-lib + rust demo + swift host）
#       make run       构建 + 运行两个 demo + 断言
#       make verify    同上（别名）
#       make clean     清理产物

.PHONY: all run verify clean swift-lib rust-demo rust-core swift-host

all: swift-lib rust-demo rust-core swift-host

swift-lib:
	./swift-lib/build.sh

rust-demo: swift-lib
	cd rust && cargo build --release

rust-core:
	cd swift-host/rust-core && cargo build --release

swift-host: rust-core
	./swift-host/build.sh

run: all
	./scripts/verify.sh

verify: run

clean:
	rm -f swift-lib/libSwiftBridge.dylib
	rm -rf rust/target
	rm -rf swift-host/rust-core/target
	rm -f swift-host/rust-host-demo
