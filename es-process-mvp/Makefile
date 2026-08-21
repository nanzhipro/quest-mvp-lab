# es-process-mvp 统一工程入口。e2e 目标需 sudo（ES client 要求 root）。

.DEFAULT_GOAL := help

.PHONY: help build test package e2e clean

help: ## 显示可用目标
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-10s %s\n", $$1, $$2}'

build: ## debug 构建
	swift build

test: ## XCTest 单元测试（无需 root）
	swift test

package: ## release 构建 + 签名打包 ESProcessMvp.app
	./scripts/build.sh

e2e: package ## 真机 DoD 用例验证（需 sudo）
	sudo ./scripts/test-e2e.sh

clean: ## 清理构建产物
	swift package clean
	rm -rf ESProcessMvp.app
