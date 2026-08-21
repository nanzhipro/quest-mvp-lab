# es-mvp（ObjC 版）统一工程入口。e2e 目标需 sudo（ES client 要求 root）。

.DEFAULT_GOAL := help

.PHONY: help build package e2e e2e-cache clean

help: ## 显示可用目标
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-10s %s\n", $$1, $$2}'

build: ## 快速编译检查（只语法检查，不签名不出产物）
	clang -fobjc-arc -O0 -Wall -fsyntax-only \
	  -framework Foundation -framework UniformTypeIdentifiers \
	  src/main.m

package: ## 编译 + 签名打包 ESMvp.app
	./scripts/build.sh

e2e: package ## DoD 用例验证（需 sudo）
	sudo ./scripts/test-e2e.sh

e2e-cache: package ## 内核授权缓存语义验证（需 sudo）
	sudo ./scripts/test-cache.sh

clean: ## 清理构建产物
	rm -rf ESMvp.app
