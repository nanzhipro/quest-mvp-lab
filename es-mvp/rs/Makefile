# esmvp-rs 统一工程入口。e2e 目标需 sudo（ES client 要求 root）。

.DEFAULT_GOAL := help

.PHONY: help build test lint fmt package e2e e2e-cache clean

help: ## 显示可用目标
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-10s %s\n", $$1, $$2}'

build: ## debug 构建
	cargo build

test: ## 单元测试 + Mock 集成测试
	cargo test

lint: ## clippy 静态检查（告警即失败）
	cargo clippy --all-targets -- -D warnings

fmt: ## 格式化全部源码
	cargo fmt

package: ## release 构建 + 签名打包 ESMvpRs.app
	./scripts/build.sh

e2e: package ## DoD 用例验证（需 sudo）
	sudo ./scripts/test-e2e.sh

e2e-cache: package ## 内核授权缓存语义验证（需 sudo）
	sudo ./scripts/test-cache.sh

clean: ## 清理构建产物
	cargo clean
	rm -rf ESMvpRs.app
