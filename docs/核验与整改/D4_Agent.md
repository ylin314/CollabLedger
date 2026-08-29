# D4 Agent：全量真实性核验与整改

> 审计日期：2026-08-29。依据 D4 深化方案、团队分工 D4、API 契约 `:1546-1643`、项目书第十一章和当前 Agent 调用链。

## 1. 逐条需求映射

| 需求 | 状态 | 证据 |
|---|---|---|
| Agent HTTP 对话、项目权限 | 真实完成（规则/接口） | `backend/routers/agent.py:39-47` 调用 `ensure_project_access(...,"member")`；viewer 被拒绝。 |
| 六个只读白名单工具 | 部分实现 | 白名单 `backend/agent/runtime.py:18`，工具实现 `tools.py:12-64`。但 `weekly_report` 调用 `get_weekly_report`，在未落库时会生成并写入 weekly_reports（`analytics.py:266-291`），因此并非严格只读。 |
| ReAct 多步循环/max_steps | 真实完成（代码/模拟测试） | `runtime.py:149-201`；工具决定、白名单校验、轨迹记录存在；`test_agent.py:64-88` 用 fake LLM 验证两轮。真实 provider 未验收。 |
| 只基于项目事实、不编造 | 部分实现 | system prompt `runtime.py:142-147` 有约束，工具返回真实数据；但对 LLM 最终 `answer` 没有事实校验/引用一致性验证（`:170-175`），提示词不是强制安全边界。可被 prompt injection 或模型幻觉绕过。 |
| citations/tool_trace | 真实完成（结构/前端展示） | `runtime.py:65-95,210-218`；AgentView `:113-137` 展示。当前 citations 是文字标签，不是可点击证据链接，和契约“可追溯到具体事实”的强要求仍有差距。 |
| 会话记忆持久化 | 真实完成（SQLite） | `memory.py:37-62` 写入/读取 agent_memory；本轮真实服务停止重启后 sessions 仍保留。 |
| 摘要压缩与失败不丢 | 真实完成（代码/测试） | `memory.py:64-117`；失败关闭连接并保留原消息；`test_agent.py:101-142` 覆盖。触发阈值按消息数，未做多用户会话隔离。 |
| LLM Chat Completions 主路径、空 content retry、API key 脱敏 | 部分实现 | `llm.py:36-77` 有真实 HTTP、Bearer、空 content+reasoning retry；`config.py:59-65` public_dict 脱敏；但真实 provider 未运行，且测试 `test_agent.py:41-61` 只证明 fake HTTP。 |
| 失败规则兜底 | 真实完成（规则路径） | `runtime.py:39-63,161-169,202-205`；本轮 smoke 返回 `source=fallback`。 |
| 会话删除仅 owner | 真实完成（接口/安全测试） | `routers/agent.py:61-65`；`test_security.py:52-64` viewer 403。 |
| 前端真实交互 | 部分实现 | AgentView `:44-85` 请求真实 API、展示 warning/trace/citations；本轮未做 Playwright 和窄屏验收。 |

## 2. 关键安全发现

1. **最终答案未做事实约束（P1）**：任何通过 JSON 解析的 `answer` 都直接返回；没有对回答中的数字、任务名、成员名与 facts 做校验。需增加“事实引用对象+渲染模板”或最少做引用 id 校验。
2. **prompt injection 边界不足（P1）**：用户消息进入 `user_payload`，工具结果和用户内容混在同一 JSON；system 有文字约束但没有不可覆盖的结构化 policy 检查。需对外部内容、任务描述、贡献描述做不可信数据标记，禁止模型把其中指令当系统指令。
3. **weekly_report 工具写库（P1）**：违反 D4 “所有工具只读”定义。拆为只读已存在周报和显式生成动作，或在 Agent 内禁止首次生成。
4. **会话没有 user_id 归属（P2）**：`memory.py:37-45` 只按 project_id/session_id 保存；同一项目成员可以看到默认会话消息，是否符合隐私需产品确认。至少应记录 actor/user_id 并在 sessions/history 做归属或明确项目共享会话。
5. **摘要错误被 runtime 再次静默吞掉（P2）**：`runtime.py:205-209` 捕获摘要异常后 `pass`，虽然原消息不丢，但没有可观测 warning。

## 3. 已执行证据

后端 81 passed；Agent 测试覆盖 fallback、fake Chat Completions、ReAct、未知工具、摘要成功/失败；真实 smoke 返回 `source=fallback`、2 条工具轨迹、13 条 citations。没有真实 LLM 成功证据，不能写“LLM 主路径已验证”。

## 4. 整改顺序与验收

1. 先冻结会话隐私范围和引用 UI；
2. 拆分严格只读工具与生成周报动作；
3. 增加结构化 fact id/citation 校验，补 prompt injection 防护和长度/超时边界；
4. 使用外部凭据做一次脱敏 live run，保存只含响应摘要的证据；
5. D7 做桌面/390px 浏览器链路。

验收必须覆盖：风险、周报、任务状态、推荐四类问题；白名单外工具拒绝；模型空内容/非法 JSON/超时回退；摘要失败原消息可读；API key 不在响应/日志；引用可定位到任务/风险/成员，而不是只显示一段文字。