SHELL := /bin/bash
PROJECT := $(shell cd "$(dir $(lastword $(MAKEFILE_LIST)))" && pwd)
CORE := $(PROJECT)/core
APP := $(PROJECT)/app
APP_HOME ?= $(PROJECT)/.verify/app-home

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: build
build: build-core build-app ## Build the Rust core and install the Electron shell

.PHONY: build-core
build-core: ## Build sandbox-center + sandbox-cli (release)
	cd $(CORE) && cargo build --release --offline

.PHONY: build-app
build-app: ## Install the Electron shell's dependencies
	cd $(APP) && npm install --no-audit --no-fund

.PHONY: test
test: test-unit test-e2e ## Run every automated test

.PHONY: test-unit
test-unit: ## Rust unit tests (policy, SBPL generation, protocol, violations, audit)
	cd $(CORE) && cargo test --offline --workspace --lib --bins

.PHONY: test-e2e
test-e2e: ## Rust e2e tests: real sandbox-center + sandbox-cli + sandbox-exec
	cd $(CORE) && cargo test --offline --workspace --test e2e -- --test-threads=4

.PHONY: verify-app
verify-app: build-core ## Electron self-test (renderer → preload → main → center → sandbox-exec)
	rm -rf $(APP_HOME)
	cd $(APP) && SC_APP_HOME=$(APP_HOME) npx electron . --self-test

.PHONY: verify
verify: test verify-app ## Full verification: unit + e2e + Electron self-test

.PHONY: demo
demo: build-core ## Terminal demo: one tool call per sandbox rule, no GUI
	./scripts/demo.sh

.PHONY: fmt
fmt: ## Format the Rust sources
	cd $(CORE) && cargo fmt --all

.PHONY: clean
clean: ## Remove build products and verification artifacts
	cd $(CORE) && cargo clean
	rm -rf $(PROJECT)/.verify
