# D4 Agent - 深化方案

> 面向开发会话(session / goal)的可执行方案文档。目标:把当前"一次性 if-else 规划 + 单次工具执行"的最小闭环,深化为「LLM 驱动的多步推理循环 + 只读工具集 + 会话摘要压缩 + 规则兜底」的完整 Agent。
> 状态:已与负责人确认决策(见「决策记录」)。开发顺序:D3 → D4 → D2,本文件是第二个执行。

## 1. 现状对照

### 1.1 文档要求(团队分工 D4 / 产品路线图阶段二)
- Agent 对话接口、会话记忆、工具读取项目事实、问题规划、LLM 调用、失败兜底。
- 验收:只基于项目事实回答;不输出排名或人格攻击;会话上下文可用;LLM 失败返回规则答案;API Key 不暴露。

### 1.2 当前实现(backend/agent/)
| 文件 | 现状 | 差距 |
| --- | --- | --- |
| `config.py` | OpenAI 兼容配置,密钥只从环境读取,`public_dict()` 脱敏 | ✅ 满足 |
| `llm.py` | `LLMClient.complete()` 标准 Chat Completions | 可复用;需支持 JSON 结构化返回 |
| `memory.py` | `agent_memory` 表,recent 8 条(上限 30) | 无长对话摘要压缩 |
| `plan.py` | `AgentPlanner.build()` 仅一个 if-else 意图分支 | 无真推理、无动态工具选择 |
| `tools.py` | 只有 `snapshot` / `recommend` 两个工具 | 工具集太薄,不能针对具体问题只读查询 |
| `runtime.py` | 一次性把全部 facts 塞给 LLM,失败走 `_fallback` | 无多步循环、无来源引用 |

### 1.3 差距结论
- **规划**:当前是先规划再一次性全部执行;没有"下一工具动态选择、循环直到可回答"。
- **工具**:缺单点查询(单任务、单风险、周报、负载等)。
- **会话**:长对话没有摘要压缩,超出 recent 上限就丢上下文。
- **可追溯**:回答没有附带来源引用。

## 2. 决策记录(已确认)
| 项 | 决策 |
| --- | --- |
| 深化级别 | C 级:完整 ReAct + 会话摘要压缩 ✅ |
| 工具集 | 新增 3 个以上只读工具,保留 snapshot/recommend ✅ |
| 事实来源引用 | 回答附带引用条目(具体任务/风险/成员) ✅ |
| 会话记忆 | 做长对话摘要压缩;保留现有 recent 8 条语义 ✅ |
| 失败兜底 | LLM 任一环节失败 → 规则回退,接口不报错 ✅ |

## 3. 深化设计

### 3.1 推理循环(ReAct 简化版)
`runtime.py` 主循环改为:

```text
输入 message
plan = planner.build(message)          # 初始动作规划(规则或用 LLM)
执行 plan 中的工具,收集 facts(只读)

loop 直到 max_steps(默认 4):
    将 {消息, 已收集 facts, 最近记忆} 交给 LLM
    LLM 决策:
      - 若需要更多事实 → 指定 next_tool + 参数(白名单工具)
      - 若可回答 → 返回最终 answer
    执行 next_tool,追加 facts
    达到 max_steps 仍无答案 → 用已有 facts 走规则兜底
```

- 每轮把工具结果结构化注入 prompt,禁止 LLM 编造。
- `next_tool` 白名单:`snapshot` / `recommend` / `task_detail` / `risk_detail` / `weekly_report` / `member_load`。
- 工具执行失败(如任务不存在)返回明确错误事实,不中断循环。
- `max_steps` 由环境变量 `AGENT_MAX_STEPS`(默认 4)控制,防止死循环与超时。

### 3.2 结构化 LLM 决策
复用 `backend/services/recommend.py` 的 `llm_json(prompt, timeout)`(团队已验证的 JSON 提取链路),在 `backend/agent/llm.py` 增加 `complete_json(messages, timeout)`:

- 请求 `{"action": "tool", "tool": "...", "args": {...}}` 或 `{"action": "answer", "answer": "..."}`。
- 解析失败 / 工具名不在白名单 / LLM 失败 → 走规则兜底(保留现有 `_fallback` 逻辑)。
- system 约束:只依据注入事实;不得输出排名、摸鱼判断、人格攻击;事实不足要明说。

### 3.3 工具扩展(tools.py)
保留现有 `snapshot` / `recommend`,新增只读工具(在 `internal_*` 或 `recommend.py` 已有内部函数上薄封装,禁止绕过路由直接对外写):

| 工具 | 说明 | 装载自 |
| --- | --- | --- |
| `snapshot`(已有) | 项目全量事实 | `internal_project_snapshot` |
| `recommend`(已有) | 负责人推荐 | `internal_recommendations` |
| `task_detail`(新) | 查单任务(标题/状态/负责人/截止/工时/打卡/评价) | `internal_task_detail`(新增或复用) |
| `risk_detail`(新) | 项目风险列表(按严重度) | `internal_project_risks` |
| `weekly_report`(新) | 本周/指定周报(落库后) | `analytics.internal_weekly_report` 包装 |
| `member_load`(新) | 成员负载与健康度 | `internal_member_load` |

- 所有工具只读;进入 Agent 后一律用内部 helper,不接收 HTTP `Request`。
- 工具参数统一从 LLM 决策的 `args` 解析,缺失用默认值;`task_detail` 需 `task_id`。

### 3.4 会话摘要压缩(memory.py)
在 `agent_memory` 表已有 `role` 列上扩展 `role='summary'`,不改表结构:

- 当会话消息(role in user/assistant)累计超过阈值(默认 8 条)时,把旧消息压缩成 1 条 `role='summary'` 摘要。
- `recent(project_id, session_id)` 改为:返回最新 `role!='summary'` 的若干条(默认 8)+ 若存在最近摘要则前插 `role='summary'` 一条。
- 摘要生成复用 LLM:把旧消息交给 LLM 提炼关键事实(用户意图、任务/成员/日期、已给结论),失败则**保留原消息不压缩**(不丢信息)。
- 阈值环境变量 `AGENT_SUMMARY_THRESHOLD`(默认 8),`AGENT_SUMMARY_LIMIT`(默认建议保留条数)。

### 3.5 事实来源引用
- `runtime.run()` 返回结构扩展(向后兼容现有字段,增加 `citations` 与 `tool_trace`):

```json
{
  "answer": "...",
  "source": "llm" | "rule",
  "llm_error": null,
  "plan": [{"tool": "snapshot", "purpose": "..."}],
  "tool_trace": [{"tool": "task_detail", "args": {"task_id": 3}, "ok": true}],
  "citations": [{"type": "task", "task_id": 3, "title": "开发登录页", "status": "in_progress"}],
  "memory": [...]
}
```

- `citations` 从已执行的工具结果中提取(任务/风险/成员),供前端"来源可追溯"展示。

### 3.6 失败兜底
- 保持现有 `_fallback()`(风险/周报/推荐/负载四类规则回答),并补充 task_detail 的单任务规则回答。
- LLM 未配置或任一环节异常 → `source="rule"`,`llm_error` 记录原因,`answer` 用规则结果。

## 4. 涉及文件(实施顺序)
1. `backend/agent/llm.py`:新增 `llm_json_decision()`(或复用 recommend 的 `llm_json`)。
2. `backend/agent/memory.py`:摘要压缩、recent 语义调整。
3. `backend/agent/tools.py`:新增 3 个只读工具。
4. `backend/agent/runtime.py`:多步循环 + citations 提取 + 兜底。
5. `backend/agent/plan.py`:保留规则分支,新增 LLM 规划入口(可选,与 runtime 合并)。
6. `backend/routers/agent.py`:响应结构透传(无需大改,字段已兼容)。
7. `backend/services/analytics.py`:`internal_task_detail`(如无)供工具复用。
8. `README.md`、`docs/API接口契约.md`:补充 D4 新结构。
9. `backend/test/test_stage2.py`(或新增聚焦测试):循环/工具/兜底最少覆盖。

## 5. 验收标准(映射 D4 文档验收 + C 级)
- `POST /api/projects/{id}/agent/chat` 对"谁认领、某任务状态、最近风险、上周周报"等多类问题能给出答案。
- LLM 决策中只会调用白名单工具,不会出现未知工具调用。
- 不配置 `.env` 时全链路规则兜底,接口 `source="rule"` 且不报错。
- 回答包含 `citations`,来源可追溯到具体任务/风险/成员。
- 消息超过阈值后触发摘要;摘要失败不丢消息。
- API Key 不出现在任何返回字段;`GET /api/agent/config` 仍返回脱敏信息。
- 现有核心测试(如 `test_agent` ...)保持通过;新增最少 2 个聚焦测试(推理/兜底)。

## 6. 本地验证命令
```powershell
python scripts/seed_stage2_demo.py
$env:COLLAB_DB = "$PWD\collab.db"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 对话(梁LLM配置时无 .env 走规则;配置后 LLM 生效)
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/projects/1/agent/chat -ContentType 'application/json' -Body '{"message":"登录页任务现在是什么状态?","session_id":"demo"}'
# 建议观察返回中 tool_trace / citations / source
```

## 7. 完成标志
- [ ] 多步循环在 max_steps 内收敛或兜底。
- [ ] 3 个新工具可被调用并返回真实项目事实。
- [ ] citations 出现在响应中且可追溯到事实。
- [ ] 会话摘要触发与失败回退验证通过。
- [ ] README/API 文档同步。
- [ ] 提交:中文 commit 前缀 `feat:`,例如 `feat:D4 Agent多步推理与来源引用`。