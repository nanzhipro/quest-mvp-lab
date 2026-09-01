# testdata — Hermes Agent 实测数据

## `hermes-2026-09-01.jsonl`

Hermes Agent（`/Users/nanzhi/.hermes`）真实行为采集，AgentEvent `agent-event/1.0`
格式，一行一条事件（schema 见 `../SPEC.md` §6 / `../README.md`）。

**采集条件**：

- 时间：2026-09-01 18:40–18:42（本地），时长 125s（`--duration 125`）
- 平台：macOS 26.5.2（Apple Silicon），传感器 v0.1.0
- 命令：

  ```bash
  sudo ESAgentSensor.app/Contents/MacOS/es-agent-sensor \
    --agent hermes=$HOME/.hermes --output hermes.jsonl \
    --net-poll-interval 2 --stats-interval 30 --duration 125
  ```

**统计**：

- 事件 **10581** 条，21MB；底层 received 216687，**dropped=0 errors=0**
- verb 分布：open 6768 · write 1313 · delete 638 · spawn 483 · exit 484 ·
  create 482 · truncate 217 · exec 86 · net_close 33 · net_connect 29 · move 48
- 归因分布：actor_process 8213 · both 2354 · target_path 14
- 活跃 actor：node 4317 · chrome-headless-shell 3037（hermes 拉起的无头浏览器）·
  python3.11 2232 · git 960
- 网络：62 条快照事件，主要远端为本机代理 `127.0.0.1:7897` 及本地回环端口

**合规**：已 grep 校验无企业标识；数据含本机用户名路径（`/Users/nanzhi/...`），
外发前请自行评估。

**快速分析**：

```bash
# verb 分布
python3 -c "
import json, collections
print(collections.Counter(json.loads(l)['operation']['verb'] for l in open('hermes-2026-09-01.jsonl')))"

# 某 actor 的行为链
grep '"executable":"[^"]*chrome-headless-shell[^"]*"' hermes-2026-09-01.jsonl | head -5 | python3 -m json.tool
```
