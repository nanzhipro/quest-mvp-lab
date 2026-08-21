# esmvp-swift 统一工程入口。e2e 目标需 sudo（ES client 要求 root）。

.DEFAULT_GOAL := help

.PHONY: help build test package e2e e2e-cache clean

help: ## 显示可用目标
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-10s %s\n", $$1, $$2}'

build: ## debug 构建
	swift build

test: ## XCTest 单元测试 + Mock 集成测试
	swift test

package: ## release 构建 + 签名打包 ESMvpSwift.app
	./scripts/build.sh

e2e: package ## DoD 用例验证（需 sudo）
	sudo ./scripts/test-e2e.sh

e2e-cache: package ## 内核授权缓存语义验证（需 sudo）
	sudo ./scripts/test-cache.sh

clean: ## 清理构建产物
	swift package clean
	rm -rf ESMvpSwift.app
