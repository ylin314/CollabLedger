# D1-D7 整改路线与 TODO

> 仅记录审计后的可执行路线，不表示已开始整改。严格顺序：D1 → D2 → D3 → D4 → D5 → D6 → D7。每阶段完成“代码、接口、数据库、前端、权限、失败/降级、真实运行、重启持久化”验收后，才进入下一阶段。

## 阶段 0：审计结论确认（用户确认后开始）

- [ ] 确认 D2 critical/unassigned 重复风险口径。
- [ ] 确认 D3 confirmed 贡献与工时双计口径。
- [ ] 确认 D4 Agent 会话归属、首次周报生成和严格只读边界。
- [ ] 确认 D1/D6 历史画像授权粒度与撤销策略。
- [ ] 确认 D1 reviewer/mentor 是否联动。
- [ ] 确认 D5 Webhook、反向写入、平台优先级和外部凭据责任。
- [ ] 确认 D6 profile API 兼容字段方案。

## D1 智能推荐

- [ ] 修复推荐采纳/手选的同一事务：tasks、recommendations、recommendation_events 一致提交或一致回滚。
- [ ] 增加 rec_id 条件更新/幂等键，阻止并发双击产生重复事件。
- [ ] 明确并实现 reviewer/mentor 联动；不得隐式修改 reviewer。
- [ ] 接入 D6 授权后的历史画像，返回 profile_source 和来源明细。
- [ ] 用脱敏真实 provider 验证 skill/reason 成功、超时、空 content、非法 JSON、回退。
- [ ] Playwright 验收推荐页面：桌面、390px、owner/member/viewer、超载排除、采纳、手选、历史。
- [ ] 阶段验收：代码/接口/DB/UI/权限/失败/重启证据齐全。

## D2 负载与风险

- [ ] 冻结 unfinished 是否计入当前负载和 weighted_overdue_tasks。
- [ ] 统一 high_member_load 使用计数或 weighted 口径，并同步 API/前端文案。
- [ ] 确认并实现 critical_unassigned 与 unassigned_task 聚合/并存规则。
- [ ] 风险 LLM 失败返回脱敏可诊断错误，服务端日志记录 request id，禁止吞异常。
- [ ] 前端区分接口故障、无风险、LLM rule fallback，禁止 `null` 伪装空数据。
- [ ] 用真实边界数据验证延期、临近截止、无负责人、关键无人承接、加权环境变量。
- [ ] 阶段验收：成员隔离、风险字段、排序、fallback、桌面/390px、重启。

## D3 周报

- [ ] 冻结 contribution_count 是否只计 confirmed；补 pending/disputed 复现验收。
- [ ] 冻结 actual_hours 与 checkin hours 计量方式，消除双计。
- [ ] 保证 weekly_reports 唯一周期、首访/二次/refresh/history 幂等。
- [ ] 真实 LLM 验证成员摘要、整体洞察、部分失败 mixed、全失败 rule。
- [ ] PDF：若本阶段不做，移除 pdf 假成功；若要做，接入中文字体并输出完整报告。
- [ ] 修复 history N+1，保留创建人和错误可观测字段。
- [ ] 禁止工作区无提示自动生成高成本周报，或明确缓存/触发策略。
- [ ] SQLite 新库、历史库、重复启动、重启、PostgreSQL 真实验证。

## D4 Agent

- [ ] 将“读取已存在周报”和“生成并落库周报”拆分，Agent 工具严格只读。
- [ ] 增加结构化事实 ID/citation 校验，拒绝无事实引用的敏感数字/成员结论。
- [ ] 强化 prompt injection 边界：外部描述/贡献内容作为不可信数据，工具参数白名单和类型校验。
- [ ] 确认会话共享/按用户隔离，补 user_id、跨用户读取和删除权限。
- [ ] 摘要失败保留原消息且提供可观测 warning，不输出 provider secret。
- [ ] 真实 LLM 验证多步收敛、未知工具、超时、空 content、非法 JSON、fallback。
- [ ] 前端引用可定位到任务/风险/成员真实详情，桌面/390px 验收。

## D5 外部平台接入

- [ ] 先确认 Webhook 和反向写入是否纳入最终 PRD；确认平台优先级和外部凭据。
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

- [ ] 设计并落地按用户/项目/用途的授权模型、默认值、撤销和删除语义。
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
- [ ] B：若 D1/D6 需要，确认 reviewer/mentor、贡献统计、历史项目和授权接口契约。
- [ ] C：修复/补齐 D5 回调、source 徽标、画像授权 UI、Playwright；非 D 代码问题建立 GitHub Issue 后再处理。

## 完成定义

- [ ] 每阶段文档逐条需求均有代码、接口输出、数据库结果、浏览器行为或明确外部阻塞证据。
- [ ] 总表不把“代码存在”“测试通过”写成“产品完整”。
- [ ] 所有敏感配置、token、数据库、node_modules、dist、截图、`.gitnexus`、本地 CLAUDE.md 均不提交。
- [ ] 每阶段中文原子 commit；远程 push/PR/merge 需单独授权。