# ai-coding-agent-mvp

A minimal AI coding agent CLI. It drives any OpenAI-compatible chat-completions endpoint
through a tool-use loop: the model reads files, writes files, and runs shell commands inside
a working directory until the task is done.

Default target: Alibaba Cloud Bailian (Model Studio) MaaS gateway, model `qwen3.8-27b`.
The endpoint speaks the standard OpenAI protocol, so any compatible backend works with
`--base-url` / `--model`.

## Background

Validate the core loop of an agentic coding assistant in the smallest possible surface:

1. A chat model with **function calling** (OpenAI `tools` + `tool_calls` protocol).
2. A local **tool runner** (read / write / list / run) executing inside a working directory.
3. A loop that feeds tool results back to the model until it answers.

Constraints: zero runtime dependencies (Python stdlib only), runs on Python 3.9+,
API key stored outside the repository.

## Key decisions

| Decision | Choice | Why |
| -------- | ------ | --- |
| HTTP transport | stdlib `urllib` | Zero deps; the OpenAI protocol is a few JSON POSTs. No `openai` SDK to pin or upgrade. |
| Streaming | Non-streaming | Tool-call parsing is far more reliable; the MVP optimizes for correctness. |
| Tool set | `read_file`, `write_file`, `list_dir`, `run_command` | Minimum viable set for real coding tasks. Verified end-to-end against `qwen3.8-27b`. |
| Key storage | env → macOS Keychain (opt-in) → `~/.config/aicode/api_key` (0600) | Key never enters the repo, shell history, or process argv for reads. |
| Loop budget | `--max-iters` (default 20), then a forced tool-free summary | Prevents runaway loops; the final call summarizes state. |
| Command safety | None — unsandboxed | Personal local CLI, runs with the user's privileges. `--dry-run` logs tool calls without executing them. |

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Store the API key (0600 file; use --keychain for macOS Keychain)
.venv/bin/aicode key --set            # prompts (no echo)
# or: echo "$KEY" | .venv/bin/aicode key --set --stdin

# Verify connectivity + tool calling
.venv/bin/aicode probe
```

## Usage

```bash
aicode "create hello.py that prints 'hi' and run it to verify"   # one-shot agent run
aicode                                                           # interactive REPL
aicode chat "explain the walrus operator"                        # plain chat, no tools
aicode --dry-run "show how you would create xyz.txt"             # log tools, execute nothing
aicode --reasoning "..."                                         # show the model's thinking trace
aicode models                                                    # list models on the endpoint
aicode key --show | --set | --clear                              # key management
```

Useful flags (available on every subcommand): `--model`, `--base-url`, `--max-iters`,
`--max-tokens`, `--temperature`, `--dir` (working directory), `--dry-run`, `--reasoning`,
`--verbose` (per-iteration token usage + tool results).

## Verification

Local (offline, 54 tests — no API key needed):

```bash
.venv/bin/pytest                    # unit tests: config, tools, api normalization, agent loop, CLI routing
.venv/bin/ruff check .              # lint
.venv/bin/ruff format --check .     # format
```

Live (requires the key):

```bash
.venv/bin/aicode probe               # models list + chat ping + tool-call roundtrip
```

Verified E2E runs (sandbox/):

- "create hello.py … run it" — 3 iterations: `write_file` → `run_command` (exit 0, correct output) → summary.
- Multi-file: `utils.py` + `main.py` — the model issued two parallel `write_file` calls in one
  iteration, then verified by running.
- `--dry-run`: tools logged, nothing on disk; the model correctly reported that the file was
  not created.

## Architecture

```
aicode "task"
   │
   ▼
cli.py ──► Agent (agent.py) ──loop──► ChatClient (api.py) ──HTTP──► OpenAI-compatible endpoint
   │            │                          ▲                                  (Bailian MaaS)
   │            │ tool_calls              │ normalized message
   │            ▼                          │
   ▼      ToolRunner (tools.py) ───────────┘ tool results
config.py   read_file / write_file / list_dir / run_command
(key, defaults)        ▼
                  working directory
```

- `config.py` — defaults + 3-layer key resolution (env → Keychain → 0600 file).
- `api.py` — stdlib client; normalizes `reasoning_content` and parses tool-call argument JSON.
- `tools.py` — tool specs + handlers with output caps (64 KB read, 1 MB write, 24 KB command output).
- `agent.py` — the loop: model turn → execute tool calls → feed results back; budget-exhausted
  final summary; multi-turn `AgentSession` for the REPL.
- `cli.py` — argparse with `agent` / `chat` / `models` / `probe` / `key` subcommands and
  argv routing so bare task text hits the agent.

## Conclusion

The MVP validates the whole chain against the real service: `qwen3.8-27b` on the Bailian
OpenAI-compatible gateway performs reliable function calling, including parallel tool calls,
and completes file-creation and verification tasks autonomously. A ~500-line stdlib-only CLI
is sufficient for a working agent loop; the remaining effort for a production tool is
incremental UX and safety, not in the core architecture.

## Limitations

- Non-streaming (per-iteration latency), single workspace context, no git integration.
- `run_command` is unsandboxed — intended for trusted local use only.
- No retry/backoff on transient API errors; single model per invocation.
