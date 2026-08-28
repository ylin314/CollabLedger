# D5 外部平台接入（GitHub） - 深化方案

> 面向开发会话（session / goal）的可执行方案文档。目标：让成员把真实 GitHub 提交/PR 一键导入「贡献账本」，由手动记账升级为「外部事实 + 人工确认」。只实现 GitHub 一项；飞书 / 腾讯文档接入仅记录 TODO，不实现。
> 状态：已与负责人确认决策（见「决策记录」）。执行顺序：D5 → D6 → D7，本文件第一个执行。

## 1. 现状对照

### 1.1 文档要求（团队分工 3.8 贡献系统、产品路线图阶段三）
- 手动贡献 + 外部平台接入；手动贡献 / 确认 / 争议已有，GitHub 等接入未做（README 阶段三 TODO）。

### 1.2 当前实现（backend/routers/contributions.py + backend/models.py）
| 能力 | 现状 |
| --- | --- |
| 贡献记录 | `contributions` 表：kind / title / description / quantity / metadata / evidence_url / status(pending/confirmed/disputed) / source(manual) / occurred_at |
| 平台连接 | `platform_connections`（user_id/platform/external_account_id/credentials_ref/status）、`project_integrations`（project/connection/config/enabled）已存在 |
| 事件去重 | `external_events`（integration_id + external_id 唯一约束）已存在 |
| 同步任务 | `sync_jobs`（integration、status、cursor、error）已存在 |

### 1.3 差距结论
- 无任何外部平台接入：贡献全部来自手动填写。
- 已建好的平台连接 / 事件去重 / 同步任务三张表尚未被使用（P0 基建空置）。

## 2. 决策记录（已确认）
| 项 | 决策 |
| --- | --- |
| 技术方案 | GitHub OAuth App（非 PAT 极简版）✅ |
| 接入范围 | 只做 GitHub 一项；飞书 / 腾讯文档在 README + 本文档中记 TODO，不实现 ✅ |
| 同步方向 | 单向 GitHub → 账本（导入为贡献）；不反向写 GitHub（建 issue/PR 不做）✅ |
| 同步触发 | 手动触发（连接后点「同步」）；Webhook 实时同步列为 backlog，不实现 ✅ |
| 数据落点 | contribution status=pending，source=github，owner 确认后 confirmed；复用现有 4 张平台表，不建新表 ✅ |
| Token 存储 | OAuth token 加密后仅存后端 `credentials_ref`，任何接口不返回明文 token ✅ |
| 反向写入 | 本轮不做（TODO 记录） |
| OAuth scope | 只读最小 scope：`repo`（含 public+private 读提交）或 `public_repo`；实施时按需求取最小集 |

## 3. 深化设计

### 3.1 总体流程
```text
前端「连接 GitHub」→ GET /api/integrations/github/auth-url（生成 state 存会话）
→ 跳转 GitHub 授权页（回调 redirect_uri=http://127.0.0.1:8000/api/integrations/github/callback）
→ 回调换 code → POST token 获取 access_token → 存入 platform_connections.credentials_ref（加密）
→ 项目设置页「同步提交/PR」→ POST /api/projects/{id}/integrations/github/sync
→ 拉取 commits/PRs → 逐条写 external_events（去重）→ 生成 contribution(pending, source=github, evidence_url=commit/PR 链接)
→ 组长在贡献账本确认 → 状态 confirmed
```

### 3.2 后端接口（backend/routers/integrations.py，新建）
| 方法/路径 | 说明 |
| --- | --- |
| GET `/api/integrations/github/auth-url` | 返回 GitHub 授权跳转 URL（含 state），state 存会话/临时表防 CSRF |
| GET `/api/integrations/github/callback` | OAuth 回调：换 token、存连接、跳回前端 |
| POST `/api/integrations/github/disconnect` | 断开连接（删 token） |
| GET `/api/projects/{id}/integrations/github/status` | 返回连接状态、最近同步时间、同步计数 |
| POST `/api/projects/{id}/integrations/github/sync` | 同步贡献：拉 commits + PRs → 去重写 external_events → 生成 contributions |
| GET `/api/projects/{id}/contributions?source=github` | 复用现有接口按来源过滤 |

- 配置统一从 `.env` 读取：`GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` / `GITHUB_REDIRECT_URI`（本地 `http://127.0.0.1:8000/api/integrations/github/callback`，生产走 `COLLAB_DOMAIN`）。
- 未配置 `GITHUB_CLIENT_ID` 时，接口返回明确提示「未配置 GitHub 接入」，前端隐藏/禁用连接按钮。

### 3.3 同步与去重规则
- 拉取范围：该用户可访问的仓库（`GET /user/repos`），或项目配置的仓库列表（`project_integrations.config` 存仓库名）。
- 事件粒度：commit（author=该成员）与 PR（author=该成员或 merged 由该成员合并）。
- 去重：`external_events.integration_id + external_id` 唯一约束，insert ignore；重复同步不产生重复贡献。
- 生成贡献：
  - kind=code，title=`提交：{repo}/#{sha[:7]} {msg 前 60 字}` 或 `PR：{repo}#{number} {title}`；
  - description=`由 GitHub 自动同步 · {repo} · {时间}`；
  - evidence_url=对应 commit/PR 的 URL；
  - quantity=1；status=pending；source=github；occurred_at=commit/PR 时间。
- 失败处理：GitHub API 调用失败写 `sync_jobs.error`，接口返回可读错误；不中断已成功的部分导入。

### 3.4 前端（frontend/src/main.jsx）
- 贡献账本页或成员管理页增加「GitHub 接入」区域：
  - 未连接：显示「连接 GitHub」按钮（点击跳 OAuth）。
  - 已连接：显示已连接账号 + 「同步」「断开」按钮 + 最近同步时间。
  - 同步结果 toasts：`已导入 N 条新提交/PR`。
- 贡献列表增加 `source` 徽标（手动 / GitHub）；GitHub 导入项右上角来源角标 + evidence 链接可点开。

### 3.5 安全
- `client_secret` 只在后端使用，绝不出现在前端或接口返回。
- `state` 参数防 CSRF：生成随机 state，回调时校验，失败拒绝。
- token 加密（或至少 base64 + 服务端隔离）存 `credentials_ref`，断开连接即删除。
- 所有第三方接口调用限流 + 超时，避免阻塞主线程。
- 接口鉴权沿用现有 `ensure_project_access`。

## 4. 测试
- `backend/test/test_integrations_github.py`（mock GitHub API，不依赖真实网络）：
  - auth-url 生成含 state、callback state 校验失败返回 400
  - 同步：mock commits/PRs → 生成 contributions（source=github、evidence_url 正确）
  - 去重：同一 SHA/PR 同步两次不重复
  - 权限：非成员/非 owner 访问同步接口返回 403
  - 失败：GitHub API 报错 → sync_jobs.error、接口可读错误
- 不配置 `GITHUB_CLIENT_ID` 时：连接按钮/接口提示未配置，不抛 500。

## 5. 验收标准
- [ ] `platform_connections` 新增一条 GitHub OAuth 连接，token 保存在后端
- [ ] 手动同步后贡献账本出现 source=github 的 pending 贡献，evidence_url 指向真实 commit/PR
- [ ] 同一事件重复同步不重复记录
- [ ] affiliate 前端连接 / 同步 / 断开全流程可用
- [ ] `pytest backend/` 全绿（含新增 test_integrations_github.py）；Don't break 既有 36 用例
- [ ] `cd frontend && npm run build` 通过
- [ ] README 阶段三 TODO 更新：GitHub 已接入，飞书 / 腾讯文档仍为未实现
