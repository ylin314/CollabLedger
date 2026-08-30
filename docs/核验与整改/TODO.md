# D1-D7 整改路线与 TODO

> 审计结论已于 2026-08-30 获用户确认；当前只推进 D1，严格顺序仍为 D1 → D2 → D3 → D4 → D5 → D6 → D7。每阶段必须同时具备代码、接口、数据库、前端、权限、失败/降级、真实运行和重启持久化证据，才可进入下一阶段。

## 阶段 0：审计结论确认（已完成）

- [x] D2 高优先级未分配风险聚合为一条。
- [x] D3 主贡献数只统计 `confirmed`；`pending/disputed` 单独列出；工时“打卡优先，否则任务工时”，两列展示但不混加。
- [x] D4 Agent 会话按用户隔离；对话属于个人、项目数据属于团队；Agent 只读，只读已存在周报；生成/刷新必须由周报页面明确按钮触发。
- [x] D1/D6 历史画像采用全局开关 + 项目白名单/覆盖；默认启用，关闭冻结保留，另提供删除派生数据；不删除团队原始记录。
- [x] D1 推荐采纳只改负责人；评审人由界面显式确认，导师关系不自动变更。
- [x] D5 纳入可选 Webhook 与 GitHub 反向创建 Issue/PR；飞书/腾讯文档、GitHub OAuth 等原范围外能力纳入整改。
- [x] D6 对外使用 API 契约字段，深化算法字段附加返回。

## D1 智能推荐（当前阶段）

### 已完成或已落地

- [x] 推荐采纳/手选的任务、推荐状态和 recommendation_events 使用同一事务；失败整体回滚。
- [x] recommendation_id 条件更新阻止重复点击，重复决策返回 409。
- [x] 推荐采纳不隐式写 reviewer/mentor；单任务和批量页均提供显式评审人确认提示，并有单任务组件测试证明“先改负责人、确认后再写评审人”。
- [x] 历史画像聚合按用户授权来源项目过滤，新增全局/项目授权 API、冻结和删除派生数据语义。
- [x] API 契约字段保留，算法计算字段作为附加返回。

### 待完成验收

- [ ] 用脱敏真实 provider 验证 skill/reason 成功、超时、空 content、非法 JSON、回退和供应商字段兼容。
- [ ] Playwright 验收推荐页面：桌面、390px、owner/member/viewer、超载排除、无候选、采纳、手选、显式评审人、历史和错误提示。
- [ ] 多进程/多 worker 以及 PostgreSQL 隔离级别下验证重复决策和回滚。
- [ ] 阶段验收：代码/接口/DB/UI/权限/失败/重启证据齐全后，才进入 D2。

## D2 负载与风险

- [ ] 冻结 unfinished 是否计入当前负载和 weighted_overdue_tasks。
- [ ] 统一 high_member_load 使用计数或 weighted 口径，并同步 API/前端文案。
- [ ] 落实 critical_unassigned 与 unassigned_task 聚合为一条的返回和展示规则，保留来源明细。
- [ ] 风险 LLM 失败返回脱敏可诊断错误，服务端日志记录 request id，禁止吞异常。
- [ ] 前端区分接口故障、无风险、LLM rule fallback，禁止 `null` 伪装空数据。
- [ ] 用真实边界数据验证延期、临近截止、无负责人、关键无人承接、加权环境变量。
- [ ] 阶段验收：成员隔离、风险字段、排序、fallback、桌面/390px、重启。

## D3 周报

- [ ] 贡献主数字只计 `confirmed`；`pending/disputed` 单独列“待确认 X 项”。
- [ ] 工时按“打卡优先，否则任务工时”二选一，打卡工时和任务工时分列，禁止混加。
- [ ] 保证 weekly_reports 唯一周期、首访/二次/refresh/history 幂等。
- [ ] 真实 LLM 验证成员摘要、整体洞察、部分失败 mixed、全失败 rule。
- [ ] PDF：若本阶段不做，移除 pdf 假成功；若要做，接入中文字体并输出完整报告。
- [ ] 修复 history N+1，保留创建人和错误可观测字段。
- [ ] 禁止工作区无提示自动生成高成本周报；生成/刷新必须由周报页明确按钮触发。
- [ ] SQLite 新库、历史库、重复启动、重启、PostgreSQL 真实验证。

## D4 Agent

- [ ] 将“读取已存在周报”和“生成并落库周报”拆分，Agent 工具严格只读。
- [ ] 增加结构化事实 ID/citation 校验，拒绝无事实引用的敏感数字/成员结论。
- [ ] 强化 prompt injection 边界：外部描述/贡献内容作为不可信数据，工具参数白名单和类型校验。
- [ ] 会话按用户隔离，补 user_id、跨用户读取和删除权限；项目事实仍按团队项目权限读取。
- [ ] 摘要失败保留原消息且提供可观测 warning，不输出 provider secret。
- [ ] 真实 LLM 验证多步收敛、未知工具、超时、空 content、非法 JSON、fallback。
- [ ] 前端引用可定位到任务/风险/成员真实详情，桌面/390px 验收。

## D5 外部平台接入

- [x] 产品范围纳入 Webhook、GitHub 反向创建 Issue/PR；均为用户可选开关，不能静默写入。
- [ ] 抽象统一 adapter：platforms/connections/oauth/project integrations/events/sync。
- [ ] 使用 Fernet/AES-GCM 等真正加密 token；生产缺少密钥时拒绝启动；支持失效/轮换。
- [ ] state 落库、绑定 user/session、TTL、一次性消费，重启不丢且防 CSRF。
- [ ] GitHub 项目仓库绑定独立接口；同步权限 owner；配置不能由 member 越权修改。
- [ ] Commit/PR 分页、since/cursor、部分成功、重试、stale running 恢复。
- [ ] 断开保留历史 events/integration 去重语义，不因重连重复导入。
- [ ] 补 Issue/Review/增删行统计；所有自动贡献 pending→owner confirm。
- [ ] 按 adapter 接入飞书文档/日历/会议、腾讯文档/会议；至少完成一个文档/会议平台真实主路径。
- [ ] 修复 GitHub 前端回调落地、source 徽标、owner 门控、`onReload`、`|| 1`。
- [ ] 真实 OAuth→绑定→同步→pending→确认→去重→断开验收；不以 mock 代替。

## D6 长期画像与跨项目协作

- [x] D1 已落地最小授权表和后端 API；D6 仍需完成完整产品化 UI、用途说明和审计证据。
- [ ] profile 聚合只读取授权范围的真实 tasks/reviews/work_logs/confirmed contributions。
- [ ] 统一 active/left/deleted membership 与历史项目口径；说明时间衰减计算。
- [ ] 补 profile API 兼容字段、data_sources、计算逻辑和来源明细。
- [ ] 实现跨项目合作关系 API/UI，过滤未授权项目。
- [ ] 实现长期任务推荐 API/UI和冷启动规则。
- [ ] 前端增加授权设置、画像来源、合作关系、长期推荐；无数据不造假。
- [ ] 验证本人/同项目/离开项目/跨项目未授权/撤销授权/重启持久化/390px。

## D7 质量部署与演示

- [ ] 补 Playwright E2E：注册/登录/邀请/任务/打卡/评价/贡献/推荐/周报/Agent/画像。
- [ ] 每条 E2E 在桌面和 390px 执行；截图仅放本地产物，不入 Git。
- [ ] 真实 SQLite：新库、历史库、重复启动幂等、重启不丢任务/贡献/周报/OAuth/画像。
- [ ] 真实 PostgreSQL：Alembic upgrade/current、建表、写读、重启、备份恢复。
- [ ] Docker：修复 GITHUB_* env 透传，验证 healthcheck、命名卷、容器重启、静态托管。
- [ ] HTTPS/CORS/Secure Cookie/Trust Proxy/LLM env 在真实反代环境验证；无域名证书时记录外部阻塞。
- [ ] 备份恢复演练后核对登录、项目、任务、贡献、审计日志。
- [ ] CI 增加必要的 compile/migration/contract 门禁，但不把外部密钥放进 CI。
- [ ] 修正演示手册与实现状态（disputed、GitHub 回调、source 徽标、画像授权）。
- [ ] 15 分钟演示从 seed 到收尾真实走查，未实现能力明确标红，不用假数据。

## 非 D 依赖：只记录 Issue，不直接改队友代码

- [ ] A：生产 `.env` 对齐、Docker/PG/HTTPS/监控告警和正式备份恢复验收。
- [ ] B：若 D1/D6 需要，确认 reviewer/mentor、贡献统计、历史项目和授权接口契约；本轮以 API 契约为准。
- [ ] C：补齐 D5 回调、source 徽标、画像授权 UI、Playwright；非 D 代码问题建立 GitHub Issue 后再处理。

## 完成定义

- [ ] 每阶段逐条需求均有代码、接口输出、数据库结果、浏览器行为或明确外部阻塞证据。
- [ ] 总表不把“代码存在”“测试通过”写成“产品完整”。
- [ ] 所有敏感配置、token、数据库、node_modules、dist、截图、`.gitnexus`、本地 CLAUDE.md 均不提交。
- [ ] 每阶段中文原子 commit；远程 push/PR/merge 需单独授权。
