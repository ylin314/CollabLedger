# D4 Agent：核验、整改与阶段验收

> 审计与整改日期：2026-08-30。权威依据：`docs/D_深化方案_D4_Agent.md`、API 接口契约 9.7-9.9、权限模型、Agent/安全专项文档、D3 周报口径。用户已确认：对话记录按用户隔离；项目事实仍是同一份真实团队数据；Agent 严格只读，只能查看已经存在的周报；生成/刷新周报必须走周报页面明确按钮，不能藏在聊天里。

## 1. 阶段结论

D4 核心 Agent 链路已完成：白名单只读工具、简化 ReAct 多步循环、真实事实引用、规则回退、长会话摘要压缩和用户级会话隔离均已落地。D3 已将周报 GET 改为只读，因此 Agent 的 `weekly_report` 工具不会创建周报。当前仍需在 D7 做桌面/390px 全链路、真实 provider 故障矩阵、PostgreSQL/容器重启验收。

已执行：Agent 测试 `10 passed`；D3/D2 聚焦测试通过；此前后端全量 `86 passed`，前端 9 项测试、typecheck/build 通过。D4 修改后尚未再次执行后端全量，提交前必须完成全量回归。

## 2. 逐条需求映射

| 需求 | 状态 | 具体证据 |
|---|---|---|
| Agent 对话接口基于真实项目事实 | 真实完成（SQLite） | `backend/routers/agent.py:39-47` 先鉴权项目，`backend/agent/runtime.py:97-220` 执行只读工具并将 facts 注入模型；工具调用真实读取 tasks、risks、reports、members。 |
| 白名单工具与未知工具拒绝 | 真实完成 | `runtime.py:18,178-201` 白名单含 6 个工具；未知工具返回 fallback 和 `llm_error`；`backend/test/test_agent.py:test_agent_unknown_tool_falls_back`。 |
| 多步循环与 max steps | 真实完成 | `runtime.py:149-201` 按 `AGENT_MAX_STEPS` 循环，工具结果追加后继续决策；`test_agent_react_loop_tool_trace_and_citations` 证明两轮收敛。 |
| 只读工具集 | 真实完成 | `backend/agent/tools.py:12-64` 全部为内部查询；没有写入任务/贡献/周报的工具。`weekly_report` 调用 D3 只读 `get_weekly_report`。 |
| 周报只能查看已存在记录 | 真实完成 | `analytics.get_weekly_report` 只读；不存在返回 `exists=false`，Agent fallback 明确提示必须去周报页生成；`test_agent_conversation_isolated_by_user_and_weekly_tool_is_read_only` 查询数据库确认 `weekly_reports` 行数不增加。 |
| 生成/刷新周报不藏在聊天 | 真实完成 | 唯一写入路径为 `POST /api/projects/{id}/weekly-report`，前端 `ReportView.jsx` 明确按钮调用；Agent chat 未调用该写接口。 |
| 会话按用户隔离 | 真实完成（SQLite） | `backend/agent/memory.py:38-65,126-133` 所有读写带 `user_id`；`runtime.run(... user_id)`；`routers/agent.py:40-47,50-65` 会话列表与删除仅当前用户。测试同项目两用户相同 session id 互不可见。 |
| 团队事实与个人对话边界 | 真实完成 | 项目数据查询始终按 `project_id` + 当前用户项目权限；memory 查询按 `user_id`，两者没有混成一份共享聊天上下文。 |
| 事实引用 citations | 真实完成 | `runtime.py:66-95,210-218` 从任务/风险/成员/周报/推荐结果提取去重引用；响应透传。 |
| LLM 失败规则回退 | 部分实现 | `runtime.py:161-204` 空答案、非法 JSON、超时、未知工具均 fallback；错误已脱敏。但真实 provider 的超时/空 content/非法 JSON和全部回退矩阵未在本阶段全部跑完。 |
| Prompt injection 边界 | 真实完成（规则边界） | `runtime.py:142-148` 明确任务/贡献/外部文本是不可信数据，禁止执行其中指令；工具仍受固定白名单与内部函数限制。复杂跨轮攻击样本留 D7。 |
| 长会话摘要压缩 | 真实完成 | `memory.py:67-124` 超阈值压缩，摘要角色前插；LLM 失败保留原消息并设置 `last_error`，runtime 返回 `memory_warning`。 |
| 摘要失败可观测且不丢消息 | 真实完成（单元/SQLite） | `memory.py:96-109` 失败不删原消息；`runtime.py:205-218` 返回脱敏 warning；原有失败保留测试仍通过。 |
| API key 不暴露 | 真实完成（代码/测试） | `agent/config.py:59-65` public_dict 无 key；runtime/tool error 用 `_safe_runtime_error` 脱敏；已有安全测试及 D2 脱敏测试通过。 |
| 认证、项目成员隔离、owner 清空权限 | 部分实现 | 路由先项目权限；viewer 不能 DELETE，owner 只能删除自己的会话。跨项目、历史无 user_id 记录和浏览器矩阵留 D7。 |

## 3. 根因与整改记录

1. **对话共享风险**：旧 memory 只按 `project_id/session_id` 查询，且 runtime 不传用户。现所有 append/recent/summarize/clear 带 `user_id`；路由列表与删除也限定当前用户。
2. **Agent 读取周报会写库**：D3 旧 GET 首访生成，Agent `weekly_report` 间接触发。现拆分只读 `get_weekly_report` 与写入 `generate_weekly_report`，聊天只读。
3. **周报 fallback 误把项目报告当周报**：旧 `_fallback` 对“周报”直接读取 snapshot report。现无已存在周报时明确提示，只有 `weekly_report.exists=true` 才回答周报数字。
4. **错误信息可泄露**：旧 `_run_tool`、LLM 异常直接 `str(exc)`。现统一 `_safe_runtime_error`，去除 api key/Authorization/token 片段并截断。
5. **摘要失败静默**：旧 memory 吞掉异常且 runtime 无 warning。现设置 `last_error`，输出 `memory_warning`，原消息保持不变。
6. **旧负载口径**：Agent fallback 使用旧 `load_level`。现优先使用 D2 `weighted_level`，兼容旧数据回退旧字段。

## 4. 复现与验证

1. 同项目两用户分别以 `session_id=private` 对话：owner 会话列表含 private，member 列表为空；member 再用同 ID 对话后，双方 `memory` 内容互不出现。
2. Agent `weekly_report` 查询尚未生成周期：返回 `exists=false/stored=false`，直接查 SQLite `weekly_reports` 仍为 0。
3. 先用周报页 POST 生成，再 Agent 查询：Agent 只能读取已落库 payload，并返回周报引用；聊天不改变 `weekly_reports` 行数。
4. LLM 返回第二轮 `task_detail` 再返回 answer：`tool_trace` 含两轮调用，`citations` 含具体 task id。
5. LLM 返回未知工具、非法 JSON、空答案或异常：响应 `source=fallback`、保留脱敏 `llm_error`，不执行写操作。
6. 摘要压缩失败：原 user/assistant 消息数量不减少，响应可带 `memory_warning`。
7. 认证：未登录 401、非成员 403、viewer 删除会话 403；owner 删除只影响自己的 user_id 行。

已执行命令：

- `pytest -q backend/test/test_agent.py` → `10 passed`；
- D3/D2 聚焦测试 → 通过；
- 前端 `npm test` → `4 files / 9 tests passed`；
- `npm run typecheck`、`npm run build` → 通过；
- 全量基线（D3 前）→ `86 passed`，D4 修改后全量待提交前重跑。

## 5. 涉及文件、阶段顺序与验收标准

- 后端：`backend/agent/memory.py`、`backend/agent/runtime.py`、`backend/routers/agent.py`；
- 测试：`backend/test/test_agent.py`；
- 文档：`docs/API接口契约.md`、本文件、`docs/核验与整改/TODO.md`。

D4 依赖 D2/D3 的真实风险和只读周报接口；D5 可复用 D4 的敏感错误脱敏边界；D6 只读取团队事实，不能把 Agent 对话纳入长期画像；D7 负责最终跨环境与浏览器验收。

验收标准：每个 Agent 响应有 answer/source/tool_trace/citations/facts/memory；只读工具不写 team data；对话按 user_id 隔离；周报不存在时明确提示；LLM 故障规则回退；API key 不出响应、日志和仓库。

## 6. 剩余风险

- 旧数据库中的 `agent_memory.user_id IS NULL` 记录不会展示给已认证用户，这是保守隐私策略；D7 需要决定是否提供一次性 owner 迁移工具，默认不自动猜测归属。
- `agent_sessions` 表与 `agent_memory` 并存，当前会话列表来源于 memory；若未来统一表，需要迁移设计，不在本阶段重构。
- 真实 provider 多轮回答依赖外部模型稳定性；当前只验证规则和 mock/已有主路径，复杂攻击/降级留 D7。
- 数据库连接与多 worker 并发下摘要压缩尚未实测。
