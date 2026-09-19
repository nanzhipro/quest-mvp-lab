# AI 辅助工程实训

用四个纯本地、合成数据练习训练 AI 编程中的可验证交付。Python 3.10+，仅标准库，不需要 API Key、网络、系统权限或安装额外依赖。

## 背景与目标

虚构的“帆流”工具需要增加提醒、迁移旧队列、可靠上报并验证证据。每个任务先给旧行为、完成条件和待修复代码，再运行相同的契约测试。

`flowlab/starter.py` 是刻意错误的学员版本；`flowlab/reference.py` 是参考实现。参考实现只定义课堂语义，不用于生产。

## 运行与验证

从本目录执行：

```bash
python3 -m unittest discover -s tests -v
FLOWLAB_CANDIDATE=starter python3 -m unittest discover -s tests -v
```

第一条检查参考实现，应通过。第二条检查待修复版本，初始应失败；学员修复后两者都应通过。Windows PowerShell 先执行 `$env:FLOWLAB_CANDIDATE='starter'` 再运行第二条的 Python 部分；恢复默认时移除该变量。

按课选择任务：

```bash
FLOWLAB_CANDIDATE=starter python3 -m unittest discover -s tests -k DecisionContract -v
FLOWLAB_CANDIDATE=starter python3 -m unittest discover -s tests -k MigrationContract -v
FLOWLAB_CANDIDATE=starter python3 -m unittest discover -s tests -k DeliveryContract -v
FLOWLAB_CANDIDATE=starter python3 -m unittest discover -s tests -k EvidenceContract -v
```

## 四个任务的契约

1. **新增提醒**：优先级 deny > warn > audit；audit 只上报，warn 提醒并上报，deny 阻断、提醒并上报；空列表放行且不报告；未知动作报 ValueError。保留旧 audit 不提醒的行为。
2. **队列迁移**：旧状态是 capacity/head/count/slots 的环形布局。必须按旧模数解释活跃槽位；扩容保持 FIFO，缩容保留最新事件；输出规范布局 head=0；不修改输入；空队列有效；元数据和容量必须是合法整数，不能接受 bool。`save_state` 作为给定文件写入工具，注入替换前故障应保留旧文件。
3. **重试上报**：业务 event_id 跨尝试稳定，attempt 从 1 递增。只重试 TimeoutError；业务错误直接返回；重试次数有限。对端用 event_id 去重，演示“已接受但响应丢失”。这要求客户端与对端共同约定，客户端本身不能保证全局恰好一次。
4. **证据验收**：限定单个最终成功事件、顺序记录。每次有 send；未成功尝试以 timeout 收尾；最终尝试以 ack 收尾；整条链恰好一次 accepted 和一次 ack；accepted 先于 ack；attempt 连续。同一事件的客户端与对端标识一致；重复、缺失、错序或混入其他事件拒绝。

## 学员交付

每个任务交一页任务卡、修改差异、实际测试输出、未验证项及下一步。先解释失败，再修改。不要改测试或 fixture 迎合答案；可以添加与契约一致的测试。参考代码可用于自学核对，授课时讲师可保留包结构，分发 starter、storage、空的 __init__、README 与测试，移除 reference 后显式使用 FLOWLAB_CANDIDATE=starter。

## 关键决定与局限

- 合成旧数据由测试动态生成，避免只会通过一份固定 fixture。
- 同一组测试运行参考实现与待修复实现；坏版本失败是负控证据。
- `save_state` 只做一个文件替换；没有模拟断电、目录 fsync、跨文件事务或多个写者。
- 重试没有真实网络、持久去重、退避或重启恢复；增加这些是进阶迁移题。
- 证据验收只适用上述课堂格式，不保证日志真实、来源完整或不被篡改。
- Python 教学对象不复现底层指针所有权；异步生命周期课需要独立扩展。

## 结论

这套练习把业务契约、旧数据、故障和证据放到同一学习路径。通过测试是最低验证；能解释为什么通过、还有什么未证明，才是课程目标。
