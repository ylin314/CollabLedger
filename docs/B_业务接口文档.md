# 《协作账本》B 模块业务接口文档

> 负责人：B（核心业务后端）
> 覆盖范围：B1 项目管理、B2 成员与邀请、B3 任务系统、B4 打卡、B5 质量评价、B6 贡献账本、B7 历史项目
> 状态：**接口约定已确定并已实现**；与当前代码对齐（`backend/routers/projects.py`、`tasks.py`、`contributions.py`）。任务级评审人（`reviewer_id`）与导师观察者邀请链路已按本文（含第 10 章）实现。
> 用途：前端（C）、AI/分析（D）对接 B 模块时以本文档为准。与 `docs/API接口契约.md` 冲突时，本文档描述“已实现真实行为”，契约文档描述“目标态”。

---

## 0. 阅读指引

- 本文档只覆盖 B 负责的接口。认证（A）、AI/报告/Agent（D）、外部平台接入（D）请分别查 `API接口契约.md` 第 2、9、10 章。
- 每个接口标注：路径、方法、权限、请求、响应、错误、**实现说明/与契约差异**。
- 标记 `⚠️ 差异` 的地方表示当前代码与 `API接口契约.md` 草案不一致，协同时请以本文档为准，后续再决定是否对齐。

---

## 1. 全局约定

### 1.1 基础信息

- Base URL：`/api`
- 数据格式：`application/json; charset=utf-8`
- 时间戳：ISO 8601 UTC，格式 `2026-08-25T10:00:00Z`
- 日期：`YYYY-MM-DD`
- 主键：整数自增 ID

### 1.2 认证

- 使用 HTTP-only Cookie Session（`collab_session`）。
- 前端请求统一带 `credentials: "include"`。
- B 的所有业务接口默认要求登录。

### 1.3 角色与权限

| 角色 | 权限级别 | 能力 |
| --- | --- | --- |
| `owner` | 3 | 项目全部管理权限 |
| `member` | 2 | 处理自己负责的任务、打卡、记录自己的贡献 |
| `viewer` | 1 | 只读 |


> - **导师观察者（mentor）**：通过邀请链接以 `viewer` 身份加入（只读、不参与任务执行与管理），但可被指定为任务评审人。它不是独立顶层角色，而是「viewer + 可被授予任务评审权」的用法。见 2.2、3.4、4.2、6.2。
> - **任务级评审人（reviewer）**：挂在**单个任务**上，不是项目角色。任务创建时可指定或留空，owner 或任务创建者可修改；任务未指定评审人时只能由 owner 评价。见 4.2、6.2。

权限判定顺序（`ensure_project_access`）：

1. 项目不存在或已软删除 → `404 NOT_FOUND`
2. 未登录 → `401 UNAUTHORIZED`
3. 已登录但非项目成员 → `403 FORBIDDEN`（“没有该项目的访问权限”）
4. 角色级别低于接口要求 → `403 FORBIDDEN`（“角色权限不足”）

> 归档项目为只读：任何写操作命中归档项目返回 `409 CONFLICT`（“归档项目为只读状态”）。这是通过 `ensure_writable` 统一拦截的。

### 1.4 统一错误响应

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不正确",
    "details": [
      { "field": "name", "message": "项目名称不能为空" }
    ]
  }
}
```

`details` 仅在字段级校验错误时出现。

| HTTP | code | 说明 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | 参数错误（如指派非项目成员） |
| 401 | `UNAUTHORIZED` | 未登录 |
| 403 | `FORBIDDEN` | 无项目权限或角色不足 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` | 状态冲突（归档只读、状态流转非法、唯一 owner 等） |
| 422 | `VALIDATION_ERROR` | 字段校验失败 |

### 1.5 分页

Query：`page`（默认 1，≥1）、`page_size`（默认 20，1–100）。

响应：

```json
{ "items": [], "page": 1, "page_size": 20, "total": 100 }
```

> ⚠️ 差异：成员列表、任务日志、打卡列表（任务维度）、评价历史等接口**只返回 `{ "items": [...] }`，不带分页字段**。只有“项目维度的列表”（项目列表、任务列表、项目打卡列表、贡献列表）才是完整分页结构。详见各接口。

---

## 2. B1 项目管理

### 2.1 获取项目列表

```http
GET /api/projects
```

权限：登录用户。只返回当前用户参与（有 membership）且未软删除的项目。

Query：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `archived` | boolean | `true` 只看归档项目，`false`（默认）只看 active。**二选一，不会混合返回** |
| `keyword` | string | 按项目名模糊匹配 |
| `page` / `page_size` | int | 分页 |

响应 `200`：

```json
{
  "items": [
    {
      "id": 1,
      "name": "软件工程课程大作业",
      "project_type": "课程项目",
      "description": "面向小组作业的协作管理系统",
      "start_date": "2026-09-01",
      "end_date": "2026-12-20",
      "status": "active",
      "role": "owner",
      "member_count": 4,
      "task_count": 18,
      "completed_task_count": 6,
      "created_at": "2026-08-25T10:00:00Z",
      "updated_at": "2026-08-25T10:00:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

说明：`role` 是当前用户在该项目里的角色。列表项统计只含 `member_count / task_count / completed_task_count`，更细的统计在项目详情里。

### 2.2 创建项目

```http
POST /api/projects
```

权限：登录用户。创建人自动成为 `owner`。

请求：

```json
{
  "name": "软件工程课程大作业",
  "project_type": "课程项目",
  "description": "面向小组作业的协作管理系统",
  "start_date": "2026-09-01",
  "end_date": "2026-12-20",
  "mentors": [
    { "email": "mentor@example.com" }
  ]
}
```

字段：

- `name`：必填，1–100 字符，去空格后不能为空
- `project_type`：可选，≤100 字符
- `description`：可选，≤5000 字符
- `start_date` / `end_date`：可选；若都填，`end_date` 不能早于 `start_date`
- `mentors`：可选，导师邀请目标列表；每项必须提供格式合法的 `email`，用于定向发送邀请。

响应 `201`：返回**项目详情对象**（结构同 2.3）；传入 `mentors` 时，响应增加 `mentor_invitations` 列表，返回为每位导师生成的邀请对象及 `invite_url`。

导师处理规则：**创建项目时不直接把导师加入成员表**。系统为 `mentors` 中的每位导师生成一个 `role="viewer"`、默认单次使用且可按 `email` 定向的邀请链接；链接发送给导师后，由导师本人确认接受，接受成功后才以 `viewer` 身份加入项目。导师加入后可被指定为任务评审人（见 4.2）。

错误：`422 VALIDATION_ERROR`（名称为空 / 日期区间非法 / 导师邮箱缺失或格式不正确）。

### 2.3 获取项目详情

```http
GET /api/projects/{project_id}
```

权限：项目成员（viewer 及以上）。

响应 `200`：

```json
{
  "id": 1,
  "name": "软件工程课程大作业",
  "project_type": "课程项目",
  "description": "面向小组作业的协作管理系统",
  "start_date": "2026-09-01",
  "end_date": "2026-12-20",
  "status": "active",
  "owner_id": 1,
  "current_user_role": "owner",
  "statistics": {
    "member_count": 4,
    "task_count": 18,
    "completed_task_count": 6,
    "in_progress_task_count": 5,
    "overdue_task_count": 2,
    "progress": 33
  },
  "created_at": "2026-08-25T10:00:00Z",
  "updated_at": "2026-08-25T10:00:00Z"
}
```

说明：

- `overdue_task_count` 统计 `overdue` + `unfinished` 两种状态。
- `progress` = round(completed / task_count * 100)，无任务时为 0。

### 2.4 更新项目

```http
PATCH /api/projects/{project_id}
```

权限：`owner`。归档项目不可改（`409`）。

请求（全部可选）：`name`、`project_type`、`description`、`start_date`、`end_date`。

响应 `200`：返回更新后的项目详情。

校验：`name` 不能为空串；`end_date` 不能早于 `start_date`（会结合已存值判断）。

### 2.5 归档 / 恢复项目

```http
POST /api/projects/{project_id}/archive
POST /api/projects/{project_id}/restore
```

权限：`owner`。

- archive：仅当项目为 active，否则 `409`。响应 `200`：

```json
{ "id": 1, "status": "archived", "archived_at": "2026-12-30T10:00:00Z" }
```

- restore：仅当项目为 archived，否则 `409`。响应 `200`：返回项目详情。

归档后项目只读，历史数据保留。

### 2.6 删除项目

```http
DELETE /api/projects/{project_id}
```

权限：`owner`。软删除（写 `deleted_at`）。响应 `204`。删除后普通接口返回 `404`。

---

## 3. B2 成员与邀请

### 3.1 获取成员列表

```http
GET /api/projects/{project_id}/members
```

权限：项目成员。

响应 `200`（**无分页**）：

```json
{
  "items": [
    {
      "user_id": 1,
      "name": "张三",
      "email": "zhangsan@example.com",
      "role": "owner",
      "skills": ["Python", "后端"],
      "max_concurrent_tasks": 3,
      "status": "online",
      "current_task_count": 2,
      "joined_at": "2026-08-25T10:00:00Z"
    }
  ]
}
```

说明：`current_task_count` = 该成员在本项目内状态为 `assigned/in_progress/paused/overdue` 的任务数（D 做负载分析可直接用）。排序：owner → member → viewer，同角色按加入时间。


### 3.2 修改成员角色

```http
PATCH /api/projects/{project_id}/members/{user_id}
```

权限：`owner`。

请求：`{ "role": "member" }`（可选 `owner` / `member` / `viewer`）。

响应 `200`：

```json
{ "user_id": 2, "role": "member", "updated_at": "2026-08-25T10:00:00Z" }
```

约束：

- 成员不存在 → `404`。
- **主 owner 特权**：目标本身是 owner 且要降级时，只有**主 owner**（`projects.owner_id`）或**本人**（主动退位）可操作；普通 owner 之间互不可管 → 否则 `403`（“只有主 owner 可以调整其他 owner 的角色”）。
- 把最后一个 owner 降级 → `409 CONFLICT`（“项目必须至少保留一个 owner”）。
- 若被降级的人恰好是 `projects.owner_id`（主 owner 自我退位），会自动把 `owner_id` 迁移到最早加入的另一个 owner。

### 3.3 移除成员

```http
DELETE /api/projects/{project_id}/members/{user_id}
```

权限：`owner`。响应 `204`。

约束：

- **主 owner 特权**：目标本身是 owner 时，只有**主 owner**（`projects.owner_id`）或**本人**（主动退出）可移除；普通 owner 不能移除其他 owner → 否则 `403`（“只有主 owner 可以移除其他 owner”）。
- 移除最后一个 owner → `409`。
- 被移除成员不能再访问项目，但其历史任务/贡献保留。若被移除的是 `owner_id`，会自动迁移给最早加入的另一个 owner。

### 3.4 创建项目邀请

```http
POST /api/projects/{project_id}/invitations
```

权限：`owner`。

请求：

```json
{
  "role": "member",
  "expires_in_hours": 168,
  "max_uses": 10,
  "email": "lisi@example.com"
}
```

字段：

- `role`：`member` / `viewer`，默认 `member`
- `expires_in_hours`：默认 168（7 天），1 ~ 8760
- `max_uses`：默认 10，1 ~ 10000
- `email`：可选，绑定后只有该邮箱用户能接受该邀请
- `expires_days`：可选，若填则覆盖 `expires_in_hours`（= days × 24）

邀请导师观察者使用本接口发送 `role="viewer"` 的邀请时，必须传入格式合法的 `email`；该邮箱会绑定收件人，账号邮箱为空或不匹配时均不能接受邀请。建议同时设定 `max_uses=1`。导师接受邀请后以 `viewer` 身份加入，可被指定为任务评审人（见 4.2）。可选的 `is_mentor=true` 标记仅用于前端区分导师与普通 viewer，不改变权限。

响应 `201`：

```json
{
  "id": 1,
  "code": "ABC123XYZ",
  "role": "member",
  "expires_at": "2026-09-01T10:00:00Z",
  "max_uses": 10,
  "used_count": 0,
  "revoked": false,
  "is_mentor": false,
  "invite_url": "/invite/ABC123XYZ"
}
```

说明：邀请码为 12 位大写字符串。前端拼接邀请链接可用 `invite_url`。`is_mentor` 表示该邀请是否为导师观察者邀请（默认 `false`，仅前端标记用，不改变权限，见 10.2）。

### 3.5 获取邀请列表

```http
GET /api/projects/{project_id}/invitations
```

权限：`owner`。响应 `200`：`{ "items": [ 邀请对象 ] }`（结构同 3.4 响应）。

### 3.6 撤销邀请

```http
POST /api/invitations/{invitation_id}/revoke
```

权限：邀请所属项目的 `owner`。

响应 `200`：`{ "id": 1, "revoked": true }`。

### 3.7 查看邀请信息

```http
GET /api/invitations/{code}
```

权限：登录用户。用邀请码查询（大小写不敏感）。

响应 `200`：

```json
{
  "project_id": 1,
  "project_name": "软件工程课程大作业",
  "role": "member",
  "expires_at": "2026-09-01T10:00:00Z",
  "valid": true
}
```

`valid` 综合判断：项目 active + 未撤销 + 未达使用上限 + 未过期。邀请不存在或项目已删 → `404`。

### 3.8 接受邀请

```http
POST /api/invitations/{code}/accept
```

权限：登录用户。请求体：空。

响应 `200`：

```json
{ "project_id": 1, "user_id": 2, "role": "member", "joined_at": "2026-08-25T10:00:00Z" }
```

错误：

- `404`：邀请不存在
- `409 CONFLICT`：已是项目成员，或邀请已过期/撤销/达上限
- `403 FORBIDDEN`：邀请绑定了 email 且与当前用户不符

> 兼容接口：`POST /api/auth/accept-invitation`，body `{ "invite_code": "..." }` 或 `{ "token": "..." }`，行为等价。

---

## 4. B3 任务系统

### 4.1 任务状态模型

```
unassigned → assigned → in_progress → completed
                 ↘ (指派/取消)   ↕ pause/resume
             assigned ⇄ paused
   assigned/in_progress/paused → overdue → completed
   任一活动状态 → unfinished（项目结束仍未完成）
```

状态集合：`unassigned` `assigned` `in_progress` `paused` `completed` `overdue` `unfinished`。

**状态操作允许的前置状态**（代码 `_task_action` 强约束，违反返回 `409`）：

| 操作 | 允许的当前状态 | 目标状态 |
| --- | --- | --- |
| `start` | assigned | in_progress |
| `pause` | in_progress | paused |
| `resume` | paused | in_progress |
| `complete` | assigned / in_progress / paused / overdue | completed |
| `overdue` | assigned / in_progress / paused | overdue |
| `unfinished` | unassigned / assigned / in_progress / paused / overdue | unfinished |

补充：`start/pause/resume/complete` 若任务无负责人 → `409`（“任务尚未指派负责人”）。

### 4.2 任务对象

```json
{
  "id": 1,
  "project_id": 1,
  "title": "完成后端鉴权模块",
  "description": "实现注册、登录、退出和项目权限校验",
  "assignee_id": 2,
  "assignee_name": "张三",
  "status": "in_progress",
  "task_type": "后端",
  "priority": "high",
  "due_date": "2026-09-10",
  "estimated_hours": 8,
  "actual_hours": 4.5,
  "quality": null,
  "created_by": 1,
  "created_at": "2026-08-25T10:00:00Z",
  "updated_at": "2026-08-25T12:00:00Z"
}
```

`quality` 由评价接口写入（见 B5），任务本身冗余存一份最新分。

> 任务对象将新增 `reviewer_id` / `reviewer_name` 字段，表示该任务的**授权评审人**。
>
> ```json
> { "reviewer_id": 9, "reviewer_name": "王导师" }
> ```
>
> 规则：
> - 在创建任务时指定（见 4.4）或留空（`null`）。
> - 评审人必须是项目成员（含以 viewer 身份加入的导师观察者）。
> - `reviewer_id` 为空时，只有 owner 能评价该任务；非空时，owner 与该评审人都能评价（见 6.2）。

### 4.3 获取任务列表

```http
GET /api/projects/{project_id}/tasks
```

权限：项目成员。

Query：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 按状态，非法值 `422` |
| `assignee_id` | int | 按负责人 |
| `task_type` | string | 按类型 |
| `keyword` | string | 匹配标题或描述 |
| `due_before` | date | 截止早于该日 |
| `overdue_only` | boolean | 只看延期（overdue/unfinished，或已过 due 且未完成） |
| `sort` | enum | `due_date` / `created_at`（默认） / `priority` |
| `order` | enum | `asc` / `desc`（默认） |
| `page` / `page_size` | int | 分页 |

响应 `200`：分页任务列表（`items` + `page/page_size/total`）。

### 4.4 创建任务

```http
POST /api/projects/{project_id}/tasks
```

权限：`owner` 或 `member`。归档项目 `409`。

请求：

```json
{
  "title": "完成后端鉴权模块",
  "description": "…",
  "assignee_id": 2,
  "task_type": "后端",
  "priority": "high",
  "due_date": "2026-09-10",
  "estimated_hours": 8
}
```

字段：

- `title`：必填，1–200
- `assignee_id`：可选。填了则须为项目成员（否则 `400 BAD_REQUEST`），状态置 `assigned`；不填则 `unassigned`
- `priority`：`low/medium/high`，默认 `medium`
- `estimated_hours`：可选，≥0

响应 `201`：任务对象。会写一条 `created` 任务日志。

创建任务时可指定评审人 `reviewer_id`（可选，留空则默认 owner 评审）。
>
> ```json
> { "title": "…", "assignee_id": 2, "reviewer_id": 9 }
> ```
>
> 规则：`reviewer_id` 必须是项目成员（含导师观察者）；建议评审人与负责人不同，避免自评。后续可由 owner 或任务创建者通过 4.6 修改。

### 4.5 获取任务详情

```http
GET /api/tasks/{task_id}
```

权限：项目成员。响应 `200`：任务对象。任务不存在或已删 → `404`。

### 4.6 更新任务

```http
PATCH /api/tasks/{task_id}
```

权限：

- `owner`：可改除 `status`/`quality` 外所有字段
- `member`：**仅当是本任务负责人**，且只能改 `actual_hours`
- `viewer`：无权限

> ⚠️ 重要约束：**本接口禁止改 `status` 和 `quality`**。带上这两个字段会返回 `422`（“请使用专用状态或评价接口”）。状态流转走 4.8，评分走 B5。

请求（全部可选）：`title`、`description`、`assignee_id`、`task_type`、`priority`、`due_date`、`estimated_hours`、`actual_hours`、`note`。

- 改 `assignee_id` 且未显式带 status 时，会自动把 status 设为 `assigned`（有负责人）或 `unassigned`（置空）。
- 指派对象须为项目成员。
- `note` 会写入任务日志。字段变化会记为 `updated` 或 `assigned` 日志。

`reviewer_id` 可通过本接口修改，但**仅 owner 或任务创建者**（`created_by`）可改；普通成员即使是负责人也不能改评审人。传 `reviewer_id: null` 表示取消评审人（回退为仅 owner 评审）。新评审人须为项目成员。

响应 `200`：更新后的任务对象。

### 4.7 指派任务

```http
POST /api/tasks/{task_id}/assign
```

权限：`owner` 或 `member`。

请求：

```json
{ "assignee_id": 2, "note": "你更熟悉后端鉴权" }
```

- `assignee_id`：必填，须为项目成员
- 指派后状态置 `assigned`，写 `assigned` 日志

响应 `200`：更新后的任务对象。

> 兼容：也接受 query 参数 `?user_id=2&note=...`（旧客户端），契约客户端请用 JSON body。

### 4.8 任务状态操作

```http
POST /api/tasks/{task_id}/start
POST /api/tasks/{task_id}/pause
POST /api/tasks/{task_id}/resume
POST /api/tasks/{task_id}/complete
POST /api/tasks/{task_id}/overdue
POST /api/tasks/{task_id}/unfinished
```

权限：`owner` 或**任务负责人**。

请求（可选）：

```json
{ "note": "开始开发登录接口", "actual_hours": 7.5 }
```

`actual_hours` 仅 `complete` 生效并写入任务；其余操作忽略。

响应 `200`：

```json
{
  "id": 1,
  "status": "in_progress",
  "updated_at": "2026-08-25T12:00:00Z",
  "log": { "id": 10, "action": "start", "note": "开始开发登录接口", "user_id": 2, "at": "2026-08-25T12:00:00Z" }
}
```

非法状态流转 → `409`（见 4.1 表）。

### 4.9 获取任务日志

```http
GET /api/tasks/{task_id}/logs
```

权限：项目成员。响应 `200`（**无分页**，按 id 升序）：

```json
{
  "items": [
    {
      "id": 1,
      "task_id": 1,
      "user_id": 1,
      "user_name": "张三",
      "action": "assigned",
      "from_status": "unassigned",
      "to_status": "assigned",
      "note": "你更熟悉后端鉴权",
      "at": "2026-08-25T10:05:00Z"
    }
  ]
}
```

`action` 取值：`created` / `assigned` / `updated` / `start` / `pause` / `resume` / `complete` / `overdue` / `unfinished`。

### 4.10 删除任务

```http
DELETE /api/tasks/{task_id}
```

权限：“owner 或创建人”，响应 `204`。

---

## 5. B4 成员主动打卡

### 5.1 打卡对象

```json
{
  "id": 1,
  "task_id": 1,
  "project_id": 1,
  "user_id": 2,
  "user_name": "张三",
  "content": "完成登录接口和权限依赖",
  "hours": 2.5,
  "blockers": "数据库迁移脚本待确认",
  "created_at": "2026-08-25T12:00:00Z"
}
```

### 5.2 创建打卡

```http
POST /api/tasks/{task_id}/checkins
```

权限：**任务负责人或 `owner`**（非负责人的普通成员 `403`）。归档项目 `409`。

请求：

```json
{ "content": "完成登录接口和权限依赖", "hours": 2.5, "blockers": "数据库迁移脚本待确认" }
```

字段：`content` 必填 1–2000；`hours` 必填 0–24；`blockers` 可选 ≤1000。

响应 `201`：打卡对象。

隐私边界：只记录成员主动填写内容，不采集屏幕/键鼠/位置/在线时长。

### 5.3 任务打卡列表

```http
GET /api/tasks/{task_id}/checkins
```

权限：项目成员。响应 `200`（**无分页**，按 id 倒序）：`{ "items": [ 打卡对象 ] }`。

### 5.4 项目打卡列表

```http
GET /api/projects/{project_id}/checkins
```

权限：项目成员。

Query：`user_id`、`task_id`、`start_date`、`end_date`、`page`、`page_size`。

响应 `200`：分页打卡列表。（D 做周报/负载可用）

---

## 6. B5 任务质量评价

### 6.1 评价对象

```json
{
  "id": 1,
  "task_id": 1,
  "reviewer_id": 1,
  "reviewer_name": "张三",
  "quality": 4.5,
  "comment": "接口完成度高，测试覆盖完整",
  "created_at": "2026-08-25T18:00:00Z",
  "updated_at": "2026-08-25T18:00:00Z"
}
```

### 6.2 创建 / 更新评价

```http
POST /api/tasks/{task_id}/review
```

权限：`owner` 或该任务的评审人（`reviewer_id`）。归档项目 `409`。

> 权限（已实现）：任务级评审人已落地，本接口权限为：
>
> - 任务 `reviewer_id` 为空 → 只有 owner 能评价。
> - 任务 `reviewer_id` 非空 → owner **或**该评审人能评价。评审人可以是以 viewer 身份加入的导师观察者（评价接口最低角色已放宽到 viewer，再按 owner/评审人做判定）。
> - 仍保留「非 owner 不能评价自己负责的任务」的约束（即评审人不应同时是该任务负责人）。
> - 非 owner 且非该任务评审人 → `403`（“只有 owner 或该任务的评审人可以评价”）。

请求：

```json
{ "quality": 4.5, "comment": "接口完成度高，测试覆盖完整" }
```

字段：`quality` 必填，0–5，**最多一位小数**（否则 `422`）；`comment` 可选 ≤1000。

规则：

- 任务必须是 `completed`，否则 `409`。
- 已有评价则覆盖更新，同时**追加一条历史**（`task_review_history`）。
- 会把分数同步写回任务的 `quality` 字段。

响应：首次 `201`，更新 `200`，返回评价对象。

### 6.3 获取当前评价

```http
GET /api/tasks/{task_id}/review
```

权限：项目成员。响应 `200`：评价对象；未评价 → `404`（“任务尚未评价”）。

### 6.4 评价历史

```http
GET /api/tasks/{task_id}/review/history
```

权限：项目成员。响应 `200`（无分页，按 id 倒序）：`{ "items": [ 评价对象 ] }`。

---

## 7. B6 贡献账本

### 7.1 贡献对象

```json
{
  "id": 1,
  "project_id": 1,
  "user_id": 2,
  "user_name": "张三",
  "kind": "code",
  "title": "完成后端鉴权模块",
  "description": "实现注册、登录、退出和权限校验",
  "quantity": 1,
  "evidence_url": "https://github.com/example/repo/pull/1",
  "status": "confirmed",
  "source": "manual",
  "occurred_at": "2026-08-25T18:00:00Z",
  "created_at": "2026-08-25T18:00:00Z",
  "updated_at": "2026-08-25T18:00:00Z"
}
```

- `kind`：`code` / `document` / `meeting` / `research` / `test` / `design` / `other`
- `status`：`pending` / `confirmed` / `disputed`
- `source`：`manual`（B 手动录入）/ `github`（D 平台同步预生成）

> 注：`metadata`（自由字段）会入库，但**列表/详情响应默认不返回**（`as_contribution` 未包含）。D 需要时可另行约定。

### 7.2 贡献列表

```http
GET /api/projects/{project_id}/contributions
```

权限：项目成员。

Query：`user_id`、`kind`、`status`、`source`、`start_date`、`end_date`、`page`、`page_size`。

响应 `200`：分页贡献列表，按 `occurred_at` 倒序。

### 7.3 创建贡献

```http
POST /api/projects/{project_id}/contributions
```

权限：`owner` 或 `member`。归档项目 `409`。

请求：

```json
{
  "user_id": 2,
  "kind": "code",
  "title": "完成后端鉴权模块",
  "description": "…",
  "quantity": 1,
  "evidence_url": "https://github.com/example/repo/pull/1",
  "occurred_at": "2026-08-25T18:00:00Z"
}
```

规则：

- `title` 必填 1–300；`kind` 默认 `other`；`quantity` 默认 1，≥0
- 普通成员只能给自己（`user_id` 省略或等于自己）创建，否则 `403`
- owner 可代录他人贡献；系统记录实际操作者（`created_by`）
- 目标用户须为项目成员（否则 `400`）
- `occurred_at` 省略时取当前时间
- **默认状态 `pending`，来源 `manual`**

响应 `201`：贡献对象。

### 7.4 贡献详情

```http
GET /api/contributions/{contribution_id}
```

权限：项目成员。响应 `200`：贡献对象。不存在或已删 → `404`。

### 7.5 更新贡献

```http
PATCH /api/contributions/{contribution_id}
```

权限：

- 创建者：只能改自己创建的 `pending` 贡献
- `owner`：可改任意未确认（非 confirmed）贡献
- 已确认（`confirmed`）贡献不可改 → `409`

请求（可选）：`kind`、`title`、`description`、`quantity`、`evidence_url`、`occurred_at`。

响应 `200`：更新后的贡献对象。

### 7.6 确认贡献

```http
POST /api/contributions/{contribution_id}/confirm
```

权限：`owner`。请求：`{ "note": "已核对 PR 和测试记录" }`（可选）。

响应 `200`：

```json
{ "id": 1, "status": "confirmed", "confirmed_by": 1, "confirmed_at": "2026-08-25T19:00:00Z" }
```

确认会清空之前的争议标记。

### 7.7 标记争议

```http
POST /api/contributions/{contribution_id}/dispute
```

权限：`owner`。请求：`{ "note": "缺少证明材料" }`（可选）。

响应 `200`：

```json
{ "id": 1, "status": "disputed", "dispute_note": "缺少证明材料" }
```

有争议贡献不计入最终贡献报告（D 的报告逻辑默认只统计 `confirmed`）。

### 7.8 删除贡献

```http
DELETE /api/contributions/{contribution_id}
```

权限：创建者（仅自己创建的 `pending`）或 `owner`（任意）。软删除，响应 `204`。

---

## 8. B7 历史项目

当前 B 模块**未单独实现历史项目专用接口**。历史项目通过以下已实现能力组合支持：

- 归档：`POST /api/projects/{id}/archive`（见 2.5），归档后只读、数据保留。
- 历史项目列表：`GET /api/projects?archived=true`（见 2.1）复用。
- 归档项目的任务/贡献/评价仍可通过对应只读接口访问。

> 契约中的 `GET /api/users/me/history`（跨项目历史聚合）归属 D6 长期画像，尚未实现，见 `API接口契约.md` 第 11 章。B 侧的职责边界到“归档 + 只读保留”为止。

---

## 9. 给协同方的要点清单

### 面向前端 C

1. 401 统一跳登录；403/404/409 展示 `error.message`。
2. `viewer` 隐藏所有写按钮；`member` 隐藏项目设置/成员管理/贡献确认；`owner` 全开。
3. 任务状态**不要用 PATCH 改**，用 4.8 的专用动作接口；评分用 B5 接口。
4. 任务状态/评分/打卡变更后，建议刷新：任务详情、任务日志、项目详情统计。
5. 注意分页与非分页列表的响应结构差异（见 1.5）。

### 面向 AI / 分析 D

1. 负载分析可直接用成员列表的 `current_task_count`（口径：assigned/in_progress/paused/overdue）。
2. 报告默认只统计 `confirmed` 贡献；`disputed` 不计入。
3. 平台同步预生成贡献时，写 `source` 非 `manual`、`status=pending`，走 B6 的确认流程，不要绕过 owner 确认。
4. 项目统计口径：`overdue_task_count` 含 `unfinished`；`progress` = completed/total 取整。

### 冻结与变更规则

1. 本文档中标 `⚠️ 差异` 的点，是代码与契约草案不一致处，改动前请在群里同步。
2. 修改字段类型、必填性、权限、状态码属破坏性变更，需通知 A/C/D。
3. 新增字段尽量可选，避免破坏旧客户端。
4. 每个接口改动同步更新本文档 + `API接口契约.md`。

---

## 10. 附录：评审人与导师观察者（已实现）

> 状态：**已实现并对齐代码**。规则已落地，对具体字段/接口的影响见前面各章（角色表 1.3、创建项目 2.2、任务对象 4.2、创建任务 4.4、更新任务 4.6、评价 6.2）。本附录做集中说明，供 A（权限/迁移）、C（前端）、D（统计口径）对齐。数据库 schema 版本升至 `4`（`tasks.reviewer_id`、`project_invitations.is_mentor`、`task_review_history.updated_at`）。

### 10.0 为什么需要这两个身份

现有角色只有 `owner / member / viewer` 三种，两个真实场景无法表达：

1. **评审人缺位**：任务质量评价（6.2）目前只有 owner 能打分，但 owner 未必是最懂该任务的人。需要能按任务指定评审人。
2. **导师/助教缺位**：课程或竞赛里，导师、助教、企业导师、评委需要观察项目全貌并可能参与评审，但**不参与日常任务执行，也不拥有 owner 管理权**。viewer 太弱（不能评审），owner 太强（能改一切）。

### 10.1 任务级评审人（reviewer）

定位：挂在**单个任务**上的授权评审人，不是项目角色。

已决策规则：

- 在**创建任务时指定**（`reviewer_id`）或**留空**。
- **owner 或任务创建者**（`created_by`）可修改评审人；普通成员（含负责人）不能改。
- 任务**未指定评审人** → 只有 owner 能评价该任务。
- 任务**已指定评审人** → owner 或该评审人可评价。
- 评审人须为项目成员（含以 viewer 身份加入的导师观察者）；建议与负责人不同，避免自评。
- 仍保留「非 owner 不能评价自己负责的任务」的约束。

数据模型（已实现）：`tasks` 增加可空外键字段 `reviewer_id`（→ users，`ON DELETE SET NULL`）。无需独立授权表——评审授权就等于「被设为某任务的 reviewer」，天然可追溯（记录在任务上，变更进任务日志）。

涉及接口（已实现）：4.4 创建任务加 `reviewer_id`；4.6 更新任务允许 owner/创建者改 `reviewer_id`（传 `null` 取消）；4.2 任务对象返回 `reviewer_id/reviewer_name`；6.2 评价权限按上表放宽。

### 10.2 导师观察者（mentor）

定位：项目的「只读 + 可评审」外部观察者，典型是课程导师、助教、企业导师、评委。

已决策规则：**通过邀请链接加入**（owner 发 `role="viewer"` 的邀请，见 2.2、3.4），由导师本人确认后以 `viewer` 身份加入（只读），同时**可被指定为任务的评审人**（见 10.1）。它不是新的顶层角色，而是「viewer + 可被任务指定为 reviewer」的用法。

能力边界：

| 能力 | mentor（viewer 身份） |
| --- | --- |
| 查看项目详情、任务、打卡、贡献、报告、风险、周报 | ✅ 允许（viewer 的读能力） |
| 对被指定为评审人的任务打质量分 | ✅ 允许（作为该任务 reviewer，见 10.1） |
| 创建/编辑/指派/流转任务 | ❌ 禁止 |
| 打卡、记录贡献 | ❌ 禁止（导师不是执行者） |
| 确认/争议贡献 | ❌ 禁止（仍归 owner） |
| 改成员角色、移除成员、编辑/归档/删除项目、建邀请 | ❌ 禁止 |

实现要点：

- mentor 复用现有 `viewer` 角色，**不新增顶层角色**，`ROLE_LEVEL` 不变；评审能力来自「被某任务指定为 reviewer」，与角色解耦。
- 加入方式：owner 发 `role="viewer"` 的邀请链接（见 3.4），导师本人确认接受后加入；不走「直接添加成员」路径，保证外部人自愿加入。
- 与 D 对齐的统计口径：导师是 viewer，本就不进负载分析和推荐候选（推荐只看可被指派的成员）。
- 导师标记：`project_invitations` 已加 `is_mentor` 字段（默认 `0`）。创建项目时 `mentors` 列表会生成 `role="viewer"`、`max_uses=1`、`is_mentor=1` 的邀请；`POST /api/projects/{id}/invitations` 也接受 `is_mentor=true`。该标记仅用于前端区分「导师」与普通 viewer，不改变权限；接受邀请后成员仍以 `viewer` 身份加入（`memberships` 未新增标记字段）。

### 10.3 落地记录与仍需与 A 对齐的点

已实现（B 侧）：

1. `tasks.reviewer_id` 可空外键（`db.py` SCHEMA_SQL + 前向迁移 `_add_columns`，schema 版本升至 `4`）。
2. 评价接口（6.2）权限改为「owner 或该任务 reviewer」，最低角色放宽到 viewer 后再判定。
3. `project_invitations.is_mentor` 标记（默认 `0`），创建项目 `mentors` 与邀请接口 `is_mentor` 均已支持。评审授权记录在任务上（变更进任务日志），未新增授权表。

仍需与 A 对齐：

1. 在 `PERMISSION_MODEL.md` 补充「任务级评审权」规则（owner 或该任务 reviewer 可评价）。
2. `memberships` 未加 `is_mentor` 标记：导师与普通 viewer 在成员表层面无差别，仅邀请记录带标记。若前端需在成员列表直接区分导师，再评估是否加成员级标记。
3. 已允许同一用户既是 member 又被设为其他任务 reviewer，但仍禁止评审自己负责的任务。
