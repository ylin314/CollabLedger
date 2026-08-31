# D5 外部平台接入深化方案

> 更新日期：2026-08-30。用户已撤销旧版“只做 GitHub、Webhook/反向写入/飞书/腾讯文档不做”的范围限制。本版以 API 契约为对外标准，完整纳入 GitHub、Webhook、显式反向写入、飞书与腾讯文档；外部凭据不足只影响实网验收，不允许以静态 JSON 或假成功代替。

## 1. 产品决策

| 决策项 | 最终口径 |
|---|---|
| 平台范围 | GitHub、飞书、腾讯文档均进入 D5；后续平台通过同一适配器扩展 |
| 数据方向 | 外部平台 → CollabLedger 为主；GitHub Issue/PR 允许用户显式确认后反向写入 |
| 实时性 | 手动同步为可控主路径；GitHub Webhook 为用户可选实时增强 |
| 贡献状态 | 所有外部事件先写 `external_events`，再生成 `pending` 贡献，必须由 owner 确认 |
| 权限 | 个人平台连接归用户；项目绑定、同步、Webhook 注册、反向写入仅 owner |
| 断开 | 默认冻结：清空 token、停用项目集成、保留事件与贡献；不因重连破坏去重 |
| 凭据 | Fernet 加密，生产无 `GITHUB_TOKEN_SECRET` 时拒绝授权；接口永不返回 token |
| OAuth state | 数据库持久化，绑定用户和具体登录 session，10 分钟 TTL，一次消费 |

## 2. 架构

```text
平台目录 / 用户连接 / 项目绑定
        ↓
backend/services/platform_adapters.py
  ├─ FeishuAdapter：真实 OAuth + wiki/docx HTTP API
  └─ TencentDocAdapter：真实开放 API 基址 + 资源路径
        ↓
backend/routers/integration_platforms.py
  ├─ 统一 OAuth / connection / integration / sync / events
  ├─ GitHub 契约兼容别名、统计、Webhook
  └─ GitHub Issue/PR 显式反向写入
        ↓
external_events（唯一键去重） → contributions(source=平台,status=pending)
```

GitHub 既有同步主链保留在 `backend/routers/integrations.py`，通用平台和扩展能力拆到独立路由，避免一个路由文件继续膨胀。

## 3. 数据模型

### 3.1 `oauth_states`

- `state`：随机一次性标识；
- `user_id`：绑定用户；
- `session_hash`：绑定发起 OAuth 的具体登录会话；
- `platform`、`redirect_uri`、`expires_at`、`consumed_at`；
- 服务重启后仍可校验，跨用户/跨 session/重放均拒绝。

### 3.2 `platform_connections`

在兼容旧库的前提下补充：

- `external_username`；
- `scopes`；
- `connected_at`；
- `last_synced_at`。

`credentials_ref` 只保存 Fernet 密文。断开时状态改为 `revoked`、清空密文，不删除连接记录。

### 3.3 既有事件与任务表

- `project_integrations.config` 保存资源类型、资源 ID、资源 URL、同步起点和最后同步时间；
- `external_events(integration_id, external_id)` 唯一约束承担跨重启去重；
- `sync_jobs` 记录 `running/success/partial/failed`；30 分钟以上遗留 `running` 自动标记失败并允许重试。

## 4. 对外接口

### 4.1 通用接口

- `GET /api/integrations/platforms`
- `GET /api/integrations/connections`
- `POST /api/integrations/{platform}/oauth/start`
- `POST /api/integrations/{platform}/connections`
- `DELETE /api/integrations/connections/{connection_id}`
- `GET /api/projects/{project_id}/integrations`
- `POST /api/projects/{project_id}/integrations`
- `POST /api/projects/{project_id}/integrations/{integration_id}/sync`
- `POST /api/projects/{project_id}/integrations/{integration_id}/retry`
- `GET /api/projects/{project_id}/integrations/{integration_id}/events`

### 4.2 GitHub 兼容接口

保留既有 `/api/integrations/github/*`，并实现契约别名：

- `/api/github/status`
- `/api/github/oauth/start`
- `/api/github/oauth/callback`
- `/api/github/connections`
- `/api/github/connections/current`
- `/api/projects/{project_id}/github/repositories`
- `/api/projects/{project_id}/github/sync`
- `/api/projects/{project_id}/github/statistics`

GitHub 同步覆盖 Commit、PR、Issue、Review；列表接口按页读取，使用 `sync_from/last_synced_at/since` 做增量起点；每个仓库独立提交，单仓库失败不回滚其他仓库。

### 4.3 Webhook 与反向写入

- `POST /api/projects/{project_id}/integrations/{integration_id}/github/webhook`：用户显式注册；
- `POST /api/integrations/github/webhook/{integration_id}`：校验 `X-Hub-Signature-256`，按 delivery 去重；
- `POST /api/projects/{project_id}/github/issues`；
- `POST /api/projects/{project_id}/github/pulls`。

反向写入只能针对当前项目已绑定仓库，且必须由 owner 点击确认，不允许由 Agent 或后台任务静默触发。

## 5. 平台适配

### 5.1 GitHub

OAuth 取得用户 token；同步 Commit/PR/Issue/Review；Webhook 实时事件；显式创建 Issue/PR。所有自动贡献均为 `source=github,status=pending`。

### 5.2 飞书

- 使用 `FEISHU_APP_ID/FEISHU_APP_SECRET` 发起用户 OAuth；
- 通过飞书开放平台换取 user access token；
- `wiki_space` 调用 Wiki 节点接口，`document` 调用 Docx 文档接口；
- 标准化为 `document_updated` 事件与 `source=feishu` 的 pending 文档贡献；
- 无凭据时 UI 明确显示“需要外部配置”，不返回假数据。

### 5.3 腾讯文档

- 使用已开通开放平台提供的 access token；
- `TENCENT_DOC_API_BASE` 和项目 `api_path/resource_id` 决定真实请求；
- 标准化为 `source=tencent_doc` 的 pending 文档贡献；
- 因当前 `.env` 未提供腾讯文档开放平台凭据，本地只能验证完整持久化链路，实网主路径标记为外部阻塞。

## 6. 前端交互

贡献账本页提供：

1. 平台目录、连接状态和“需要外部配置”状态；
2. GitHub OAuth、仓库同步、Webhook 注册；
3. 飞书 OAuth；
4. 腾讯文档令牌连接（密码框，提交后清空，不回显）；
5. 文档资源绑定与显式同步；
6. GitHub Issue/PR 显式创建表单；
7. 贡献来源徽标；
8. owner 门控，移除 `owner_id || 1` 等硬编码。

桌面和 390px 采用响应式单列布局，最终浏览器验收归 D7 统一执行。

## 7. 安全与失败路径

- OAuth state：用户 + session + TTL + 一次消费；
- token：Fernet 密文，生产密钥缺失即拒绝；
- Webhook：HMAC-SHA256 常量时间比较；
- 外部错误：只返回平台和 HTTP 状态，不泄露 token、响应体或底层异常；
- 项目权限：绑定/同步/Webhook/反写均 owner；
- 仓库隔离：反写只允许已绑定仓库；
- 部分成功：返回 `partial` 和逐仓库脱敏错误；
- 断开：冻结保留历史；
- 无配置：显式 `NOT_CONFIGURED`，前端不静默隐藏。

## 8. 验收与当前状态

### 8.1 已通过的本地真实链路

- OAuth state 落库、跨内存重置、跨用户/跨 session 拒绝、一次消费；
- Fernet 密文、错误密钥无法解密、生产缺密钥拒绝；
- owner 权限、分页参数、去重、partial、失败任务、软断开；
- 通用平台连接→绑定→adapter→事件落库→pending 贡献→重复同步去重；
- Webhook 正确签名、错误签名、delivery 去重；
- GitHub Issue/PR 显式反向写入调用；
- 前端 typecheck、测试和生产 build。

### 8.2 外部阻塞

2026-08-30 对本地 `.env` 只做“是否配置”检查（未读取/输出值）：GitHub OAuth、Webhook、飞书、腾讯文档所需变量均为空。因此不能宣称真实外网 OAuth/同步/Webhook 已跑通。待凭据可用时按以下顺序验收：

```text
连接 → 回调 → 项目绑定 → 同步 → external_events → pending contribution
→ owner confirm → 重复同步去重 → Webhook → 显式反向写入 → 断开 → 重连
```

外部资源未提供前，代码保持明确阻塞，不启用 mock/fallback 冒充主路径。