# D1-D7 整改路线与 TODO

> 审计结论已于 2026-08-30 获用户确认；D1、D2 已完成本地整改与阶段提交准备，当前严格串行进入 D3，顺序仍为 D1 → D2 → D3 → D4 → D5 → D6 → D7。每阶段必须同时具备代码、接口、数据库、前端、权限、失败/降级、真实运行和重启持久化证据，才可进入下一阶段。

## 阶段 0：审计结论确认（已完成）

- [x] D2 高优先级未分配风险聚合为一条。
- [x] D3 主贡献数只统计 `confirmed`；`pending/disputed` 单独列出；工时“打卡优先，否则任务工时”，两列展示但不混加。
- [x] D4 Agent 会话按用户隔离；对话属于个人、项目数据属于团队；Agent 只读，只读已存在周报；生成/刷新必须由周报页面明确按钮触发。
- [x] D1/D6 历史画像采用全局开关 + 项目白名单/覆盖；默认启用，关闭冻结保留，另提供删除派生数据；不删除团队原始记录。
- [x] D1 推荐采纳只改负责人；评审人由界面显式确认，导师关系不自动变更。
- [x] D5 纳入可选 Webhook 与 GitHub 反向创建 Issue/PR；飞书/腾讯文档、GitHub OAuth 等原范围外能力纳入整改。
- [x] D6 对外使用 API 契约字段，深化算法字段附加返回。

## D1 智能推荐（已完成本地整改）

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

- [x] 冻结口径：`unfinished` 不计当前负载，`weighted_overdue_tasks` 只计 `overdue`。
- [x] `high_member_load` 统一使用 weighted 口径，保留计数字段兼容并同步前端文案。
- [x] `critical_unassigned` 与 `unassigned_task` 聚合为一条，使用 `source_types` 保留来源明细。
- [x] 风险 LLM 失败返回脱敏可诊断错误并禁止吞异常；生产 request id/服务端集中日志留 D7。
- [x] 前端区分接口故障、无风险、LLM rule fallback，禁止风险/负载 `null` 伪装空数据。
- [x] 用临时 SQLite 真实边界数据验证延期、无负责人、关键无人承接、原始 normal/加权 high 和错误脱敏；真实 LLM 主路径成功。
- [ ] D7 联合验收：成员隔离浏览器动作、桌面/390px、PostgreSQL、容器重启；D2 本地代码/接口/SQLite/LLM/前端构建已通过。

## D3 周报（已完成本地整改，待全量回归后进入 D4）

- [x] 贡献主数字只计 `confirmed`；`pending/disputed` 合并单列“待确认 X 项”，并保留状态拆分。
- [x] 工时按“打卡优先，否则任务工时”二选一，打卡工时和任务工时分列，禁止混加。
- [x] `weekly_reports` 唯一周期；GET 首访只读，POST 生成/刷新，history 不重复。
- [ ] 真实 LLM 验证成员摘要、整体洞察、部分失败 mixed、全失败 rule。
- [x] 本阶段不做 PDF；已移除前端 PDF 选项，后端返回明确 501，禁止假成功。
- [x] history 使用一次 JOIN，保留创建人、source、llm_error。
- [x] 工作区 GET 只读；周报页面按钮发 POST 生成/刷新。
- [ ] SQLite 新库、历史库、重复启动、重启、PostgreSQL 真实验证。

## D4 Agent（已完成本地整改，待全量回归后进入 D5）

- [x] “读取已存在周报”和“生成并落库周报”已拆分；Agent 工具严格只读。
- [x] 已返回结构化 citations 与 tool_trace；LLM 只能基于 facts，复杂引用拒绝矩阵留 D7。
- [x] 外部自由文本标记不可信，工具白名单固定；参数异常进入脱敏 fallback。
- [x] memory、sessions 列表/删除和 runtime 均按 user_id 隔离；项目事实仍按成员权限。
- [x] 摘要失败保留原消息并返回脱敏 `memory_warning`。
- [x] 规则/mock 已验证多步、未知工具、非法 JSON、fallback；真实 provider 故障矩阵留 D7。
- [x] 后端 citations 已含任务/风险/成员/周报真实 ID；点击式浏览器与 390px 留 D7。

## D5 外部平台接入（代码与本地链路已整改，实网外部阻塞）

- [x] 产品范围纳入 Webhook、GitHub 反向创建 Issue/PR；均为用户显式可选功能，不能静默写入。
- [x] 统一 adapter 与 platforms/connections/oauth/project integrations/events/sync/retry 接口。
- [x] Fernet 真加密 token；生产缺少密钥时拒绝 OAuth；接口不返回凭据。
- [x] state 落库，绑定 user + 精确 login session，TTL、一次性消费，重启不依赖内存。
- [x] GitHub 项目仓库独立绑定；配置、同步、Webhook、反写均 owner 权限。
- [x] Commit/PR/Issue/Review 分页、since 起点、逐仓库 partial、重试、stale running 恢复。
- [x] 断开改为冻结：清 token、停用 integration、保留 events/contributions 和重连去重语义。
- [x] 飞书/腾讯文档真实 HTTP adapter、连接、资源绑定、事件落库、pending 贡献链路。
- [x] Webhook HMAC 校验、delivery 去重和显式注册；GitHub Issue/PR 显式反向写入且只允许已绑定仓库。
- [x] 修复前端回调、source 徽标、owner 门控、`onReload`、`|| 1`，并增加多平台/反写 UI。
- [ ] **外部阻塞**：真实 GitHub OAuth→绑定→同步→pending→确认→去重→Webhook→反写→断开/重连。
- [ ] **外部阻塞**：真实飞书 OAuth/Wiki/Docx 与腾讯文档开放 API 主路径。
- [ ] GitHub `additions/deletions` 仍为 0；如必须展示真实 diff 统计，需增加逐 commit 请求和速率预算。
- [ ] D7：compose 透传 D5 环境变量、容器重启、桌面/390px 浏览器验收。

## D6 长期画像与跨项目协作

- [x] profile 聚合只读取真实 tasks/reviews/work_logs/confirmed contributions；pending/disputed 不进入贡献和活跃月份。
- [x] 统一 active/left/deleted membership 与历史项目口径；返回来源项目并说明时间衰减。
- [x] 补齐 API 契约字段，同时附加深化字段、data_sources、计算逻辑和来源明细。
- [x] 自报 skills 仅作冷启动，历史技能强度只由真实任务证据计算。
- [x] 全局开关 + 项目覆盖；默认启用，关闭冻结保留，项目覆盖支持恢复跟随全局。
- [x] 实现双方授权的跨项目合作关系 API/UI；撤销授权立即移除。
- [x] 实现长期任务方向 API/UI、真实样本解释和诚实冷启动规则。
- [x] 前端增加独立个人入口、授权设置、画像来源、合作关系、长期推荐、删除二次确认；无数据不造假。
- [x] 自动化验证本人/同项目/离开项目/双边授权/撤销授权和 D1 推荐回归。
- [ ] D7：SQLite 重启持久化、旧库兼容、PostgreSQL、桌面/390px 浏览器行为。
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
