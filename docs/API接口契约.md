# 《协作账本》API 接口契约

> 状态：接口草案，用于前后端并行开发。  
> 说明：本文档定义目标接口契约，不代表所有接口都已在当前代码中实现。

## 1. 全局约定

### 1.1 基础信息

- Base URL：`/api`
- 数据格式：`application/json; charset=utf-8`
- 时间格式：ISO 8601 UTC，例如 `2026-08-25T10:00:00Z`
- 日期格式：`YYYY-MM-DD`
- 主键：整数自增 ID
- 错误信息：中文；错误码：英文大写

### 1.2 认证方式

第一版使用 HTTP-only Cookie Session：

```http
Cookie: collab_session=<session_id>
```

要求：

- Cookie 设置 `HttpOnly`、`Secure`、`SameSite=Lax`
- 登录成功后由服务端设置 Cookie
- 前端请求统一携带 `credentials: "include"`
- 业务接口默认要求登录

### 1.3 角色模型

| 角色 | 权限 |
| --- | --- |
| `owner` | 项目全部管理权限 |
| `member` | 可处理自己负责的任务、打卡、记录自己的贡献 |
| `viewer` | 只读，可查看项目数据和报告 |

> 任务评审人与导师观察者的规则如下（字段细节以 `B_业务接口文档.md` 第 10 章为准）：
>
> - **任务级评审人（reviewer）**：挂在单个任务上（`tasks.reviewer_id`，可空），不是项目角色。任务无评审人时仅 owner 可评价，有评审人时 owner 或该评审人可评价（当前实现仍在逐步落地）。
> - **导师观察者（mentor）**：复用 `viewer` 角色，创建项目或单独邀请时均通过邀请链接加入；导师本人接受后才建立 membership，可被指定为任务评审人。不新增顶层角色。

权限规则：

- 未登录：`401`
- 已登录但不是项目成员：`403`
- 角色权限不足：`403`
- 资源不存在：`404`

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

| HTTP 状态 | 错误码 | 说明 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | 请求格式或参数错误 |
| 401 | `UNAUTHORIZED` | 未登录或登录态失效 |
| 403 | `FORBIDDEN` | 无项目权限或角色权限不足 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` | 状态冲突 |
| 422 | `VALIDATION_ERROR` | 字段校验失败 |
| 429 | `RATE_LIMITED` | 请求过于频繁 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |
| 502 | `LLM_PROVIDER_ERROR` | LLM 服务调用失败 |

### 1.5 分页约定

Query 参数：

- `page`：页码，从 1 开始，默认 `1`
- `page_size`：每页数量，默认 `20`，最大 `100`

响应格式：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 100
}
```

---

## 2. 认证与用户接口

### 2.1 注册用户

```http
POST /api/auth/register
```

权限：公开。

请求：

```json
{
  "name": "张三",
  "email": "zhangsan@example.com",
  "password": "your-password"
}
```

字段要求：

- `name`：必填，1-50 字符
- `email`：必填，合法且唯一
- `password`：必填，至少 8 位

成功响应：`201 Created`

```json
{
  "id": 1,
  "name": "张三",
  "email": "zhangsan@example.com",
  "skills": [],
  "max_concurrent_tasks": 3,
  "status": "offline",
  "created_at": "2026-08-25T10:00:00Z"
}
```

错误：

- `422 VALIDATION_ERROR`
- `409 CONFLICT`：邮箱已被注册

### 2.2 登录

```http
POST /api/auth/login
```

权限：公开。

请求：

```json
{
  "email": "zhangsan@example.com",
  "password": "your-password"
}
```

成功响应：`200 OK`

```json
{
  "user": {
    "id": 1,
    "name": "张三",
    "email": "zhangsan@example.com",
    "skills": ["Python", "后端"],
    "max_concurrent_tasks": 3,
    "status": "online"
  }
}
```

响应头：

```http
Set-Cookie: collab_session=<session_id>; HttpOnly; Secure; SameSite=Lax; Path=/
```

错误：

- `401 UNAUTHORIZED`：邮箱或密码错误

### 2.3 退出登录

```http
POST /api/auth/logout
```

权限：需要登录。

请求体：空。

成功响应：`204 No Content`

行为：

- 删除服务端 Session
- 清除客户端 Cookie

### 2.4 获取当前用户

```http
GET /api/auth/me
```

权限：需要登录。

成功响应：`200 OK`

返回当前用户对象。

### 2.5 更新个人资料

```http
PATCH /api/users/me
```

权限：需要登录。

请求：

```json
{
  "name": "张三",
  "skills": ["Python", "后端"],
  "max_concurrent_tasks": 4,
  "status": "online"
}
```

所有字段可选。成功后返回更新后的用户对象。

---

## 3. 项目接口

### 3.1 获取项目列表

```http
GET /api/projects
```

权限：需要登录。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `archived` | boolean | 否 | 是否只看归档项目，默认 `false` |
| `keyword` | string | 否 | 按项目名称搜索 |
| `page` | integer | 否 | 页码 |
| `page_size` | integer | 否 | 每页数量 |

成功响应：`200 OK`

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

说明：只返回当前用户参与的项目。

### 3.2 创建项目

```http
POST /api/projects
```

权限：需要登录。

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

字段要求：

- `name`：必填，1-100 字符
- `end_date` 不能早于 `start_date`
- `mentors`：可选的导师邀请目标列表；每项必须提供格式合法的导师邮箱。创建项目时仅生成并发送 `role="viewer"` 邀请链接，不直接加入导师；导师接受链接后才以 `viewer` 身份加入。

成功响应：`201 Created`

返回项目详情。创建人自动成为 `owner`。若传入 `mentors`，响应增加 `mentor_invitations` 列表，返回对应的导师邀请对象及 `invite_url`；这些导师在接受邀请前不是项目成员。

### 3.3 获取项目详情

```http
GET /api/projects/{project_id}
```

权限：项目成员。

成功响应：`200 OK`

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

### 3.4 更新项目

```http
PATCH /api/projects/{project_id}
```

权限：`owner`。

请求字段均可选：`name`、`project_type`、`description`、`start_date`、`end_date`。

成功响应：`200 OK`

返回更新后的项目详情。

### 3.5 归档项目

```http
POST /api/projects/{project_id}/archive
```

权限：`owner`。

请求体：空。

成功响应：`200 OK`

```json
{
  "id": 1,
  "status": "archived",
  "archived_at": "2026-12-30T10:00:00Z"
}
```

归档后项目只读，历史数据保留。

### 3.6 恢复项目

```http
POST /api/projects/{project_id}/restore
```

权限：`owner`。

成功响应：`200 OK`

返回更新后的项目详情。

### 3.7 删除项目

```http
DELETE /api/projects/{project_id}
```

权限：`owner`。

成功响应：`204 No Content`

建议软删除，删除后普通接口返回 `404`。

---

## 4. 成员与邀请接口

### 4.1 获取成员列表

```http
GET /api/projects/{project_id}/members
```

权限：项目成员。

成功响应：`200 OK`

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

### 4.2 修改成员角色

```http
PATCH /api/projects/{project_id}/members/{user_id}
```

权限：`owner`。

请求：

```json
{ "role": "member" }
```

角色可选：`owner`、`member`、`viewer`。

成功响应：`200 OK`

```json
{
  "user_id": 2,
  "role": "member",
  "updated_at": "2026-08-25T10:00:00Z"
}
```

约束：项目必须至少保留一个 `owner`。

### 4.3 移除成员

```http
DELETE /api/projects/{project_id}/members/{user_id}
```

权限：`owner`。

成功响应：`204 No Content`

被移除成员不能再访问项目，但其历史任务和贡献记录保留。

### 4.4 创建项目邀请

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
  "email": "mentor@example.com"
}
```

`role` 可取 `member` 或 `viewer`。给导师发送邀请时使用 `role="viewer"`，通常指定 `email` 且设置 `max_uses=1`；导师本人通过 `invite_url` 接受后才加入项目，不得由 owner 直接代为加入。

成功响应：`201 Created`

```json
{
  "id": 1,
  "code": "ABC123XYZ",
  "role": "member",
  "expires_at": "2026-09-01T10:00:00Z",
  "max_uses": 10,
  "used_count": 0,
  "revoked": false,
  "invite_url": "/invite/ABC123XYZ"
}
```

### 4.5 获取邀请列表

```http
GET /api/projects/{project_id}/invitations
```

权限：`owner`。

成功响应：`200 OK`

返回邀请列表。

### 4.6 撤销邀请

```http
POST /api/invitations/{invitation_id}/revoke
```

权限：`owner`。

成功响应：`200 OK`

```json
{ "id": 1, "revoked": true }
```

### 4.7 查看邀请信息

```http
GET /api/invitations/{code}
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "project_name": "软件工程课程大作业",
  "role": "member",
  "expires_at": "2026-09-01T10:00:00Z",
  "valid": true
}
```

### 4.8 接受邀请

```http
POST /api/invitations/{code}/accept
```

权限：需要登录。

请求体：空。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "user_id": 2,
  "role": "member",
  "joined_at": "2026-08-25T10:00:00Z"
}
```

错误：

- `404 NOT_FOUND`
- `409 CONFLICT`：用户已是项目成员，或邀请已过期、撤销

---

## 5. 任务接口

### 动态班级与多人参与补充

班级成员池与项目队伍分离：`GET/POST /api/classrooms` 管理长期班级空间，`GET/POST/PATCH/DELETE /api/classrooms/{id}/members` 管理成员加入、退出和角色。创建项目时可传 `classroom_id` 与 `member_ids`，项目只保留本次临时队伍；项目成员移除采用状态退出，历史任务和贡献仍可追溯。

任务创建和更新支持 `participant_ids` 数组。任务仍可设置一个 `assignee_id` 作为负责人，但所有 active 参与者都可以推进任务状态和提交任务打卡；任务响应新增 `participant_ids` 与 `participants`。

### 5.1 任务状态模型

```text
unassigned → assigned → in_progress → completed
                  ↓
                paused → in_progress
                  ↓
                 overdue
                  ↓
               unfinished
```

| 状态 | 说明 |
| --- | --- |
| `unassigned` | 未分配 |
| `assigned` | 已分配，尚未开始 |
| `in_progress` | 进行中 |
| `paused` | 暂停 |
| `completed` | 已完成 |
| `overdue` | 已延期 |
| `unfinished` | 项目结束后仍未完成 |

### 5.2 任务对象

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

任务对象已返回 `reviewer_id` / `reviewer_name`（可空），表示该任务的授权评审人。创建时可指定，owner 或任务创建者可改。详见 `B_业务接口文档.md` 4.2 / 4.4 / 4.6。

### 5.3 获取任务列表

```http
GET /api/projects/{project_id}/tasks
```

权限：项目成员。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `status` | string | 否 | 按状态筛选 |
| `assignee_id` | integer | 否 | 按负责人筛选 |
| `task_type` | string | 否 | 按任务类型筛选 |
| `keyword` | string | 否 | 按标题和描述搜索 |
| `due_before` | date | 否 | 截止日期早于该日期 |
| `overdue_only` | boolean | 否 | 只看延期任务 |
| `sort` | string | 否 | `due_date`、`created_at`、`priority` |
| `order` | string | 否 | `asc`、`desc` |
| `page` | integer | 否 | 页码 |
| `page_size` | integer | 否 | 每页数量 |

成功响应：`200 OK`

返回分页任务列表。

### 5.4 创建任务

```http
POST /api/projects/{project_id}/tasks
```

权限：`owner` 或 `member`。

请求：

```json
{
  "title": "完成后端鉴权模块",
  "description": "实现注册、登录、退出和项目权限校验",
  "assignee_id": 2,
  "task_type": "后端",
  "priority": "high",
  "due_date": "2026-09-10",
  "estimated_hours": 8
}
```

字段要求：

- `title`：必填，1-200 字符
- `assignee_id`：可选；不填则状态为 `unassigned`
- `priority`：可选，`low`、`medium`、`high`，默认 `medium`
- `estimated_hours`：可选，大于等于 0

成功响应：`201 Created`

返回任务对象。

规则：

- 指定负责人时，负责人必须是项目成员
- 归档项目不允许创建任务

可传 `reviewer_id`（可选）指定任务评审人，须为项目成员；留空则默认仅 owner 评审。owner 或任务创建者可修改。详见 `B_业务接口文档.md` 10.1。

### 5.5 获取任务详情

```http
GET /api/tasks/{task_id}
```

权限：项目成员。

成功响应：`200 OK`

返回任务对象。

### 5.6 更新任务

```http
PATCH /api/tasks/{task_id}
```

权限：

- `owner` 可更新所有字段
- `member` 只能更新自己负责任务的执行字段
- `viewer` 无权限

请求：

```json
{
  "title": "完成后端鉴权和权限测试",
  "description": "补充权限测试",
  "assignee_id": 2,
  "task_type": "后端",
  "priority": "high",
  "due_date": "2026-09-12",
  "estimated_hours": 10,
  "actual_hours": 5
}
```

所有字段可选。成功后返回更新后的任务对象。字段变化必须写入任务日志。

### 5.7 指派任务

```http
POST /api/tasks/{task_id}/assign
```

权限：`owner` 或 `member`。

请求：

```json
{
  "assignee_id": 2,
  "note": "你更熟悉后端鉴权"
}
```

成功响应：`200 OK`

返回更新后的任务对象。

规则：

- 被指派人必须是项目成员
- 指派后状态从 `unassigned` 变为 `assigned`
- 写入任务日志

### 5.8 任务状态操作

以下接口均为 `POST`，权限为 `owner` 或任务负责人：

```http
POST /api/tasks/{task_id}/start
POST /api/tasks/{task_id}/pause
POST /api/tasks/{task_id}/resume
POST /api/tasks/{task_id}/complete
POST /api/tasks/{task_id}/overdue
POST /api/tasks/{task_id}/unfinished
```

请求：

```json
{
  "note": "开始开发登录接口",
  "actual_hours": 7.5
}
```

`actual_hours` 仅 `complete` 推荐使用，其他操作可为空。

成功响应：`200 OK`

```json
{
  "id": 1,
  "status": "in_progress",
  "updated_at": "2026-08-25T12:00:00Z",
  "log": {
    "id": 10,
    "action": "start",
    "note": "开始开发登录接口",
    "user_id": 2,
    "at": "2026-08-25T12:00:00Z"
  }
}
```

### 5.9 获取任务日志

```http
GET /api/tasks/{task_id}/logs
```

权限：项目成员。

成功响应：`200 OK`

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

### 5.10 删除任务

```http
DELETE /api/tasks/{task_id}
```

权限：`owner`。

成功响应：`204 No Content`

建议软删除，并保留相关追溯记录。

---

## 6. 成员主动打卡接口

### 6.1 打卡对象

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

### 6.2 创建任务打卡

```http
POST /api/tasks/{task_id}/checkins
```

权限：任务负责人或 `owner`。

请求：

```json
{
  "content": "完成登录接口和权限依赖",
  "hours": 2.5,
  "blockers": "数据库迁移脚本待确认"
}
```

字段要求：

- `content`：必填，1-2000 字符
- `hours`：必填，0-24
- `blockers`：可选，0-1000 字符

成功响应：`201 Created`

返回打卡对象。

隐私边界：

- 只记录成员主动填写的工作内容
- 不记录屏幕、键盘、鼠标、位置、在线时长等行为数据

### 6.3 获取任务打卡列表

```http
GET /api/tasks/{task_id}/checkins
```

权限：项目成员。

成功响应：`200 OK`

返回打卡列表。

### 6.4 获取项目打卡列表

```http
GET /api/projects/{project_id}/checkins
```

权限：项目成员。

Query 参数：`user_id`、`task_id`、`start_date`、`end_date`、`page`、`page_size`。

成功响应：`200 OK`

返回分页打卡列表。

---

## 7. 任务质量评价接口

### 7.1 评价对象

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

### 7.2 创建或更新评价

```http
POST /api/tasks/{task_id}/review
```

权限：`owner` 或被授权评审人。

任务无评审人时仅 owner 可评价；任务有 `reviewer_id` 时 owner 或该评审人可评价。详见 `B_业务接口文档.md` 6.2 / 10.1。

请求：

```json
{
  "quality": 4.5,
  "comment": "接口完成度高，测试覆盖完整"
}
```

字段要求：

- `quality`：必填，0-5 分，最多一位小数
- `comment`：可选，0-1000 字符

成功响应：`201 Created` 或 `200 OK`

返回评价对象。

规则：

- 任务必须处于 `completed` 状态
- 已存在评价可覆盖更新，但保留历史
- 普通成员不能评价自己的任务（评审人 ≠ 任务负责人）

### 7.3 获取任务评价

```http
GET /api/tasks/{task_id}/review
```

权限：项目成员。

成功响应：`200 OK`

返回评价对象；未评价时返回 `404`。

### 7.4 获取评价历史

```http
GET /api/tasks/{task_id}/review/history
```

权限：项目成员。

成功响应：`200 OK`

返回评价历史列表，按时间倒序。

---

## 8. 贡献接口

### 8.1 贡献对象

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

`kind` 可选值：

- `code`
- `document`
- `meeting`
- `research`
- `test`
- `design`
- `other`

`status` 可选值：

- `pending`
- `confirmed`
- `disputed`

`source` 可选值：

- `manual`
- `github`

### 8.2 获取贡献列表

```http
GET /api/projects/{project_id}/contributions
```

权限：项目成员。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | integer | 否 | 按成员筛选 |
| `kind` | string | 否 | 按类型筛选 |
| `status` | string | 否 | 按状态筛选 |
| `source` | string | 否 | 按来源筛选 |
| `start_date` | date | 否 | 开始日期 |
| `end_date` | date | 否 | 结束日期 |
| `page` | integer | 否 | 页码 |
| `page_size` | integer | 否 | 每页数量 |

成功响应：`200 OK`

返回分页贡献列表。

### 8.3 创建贡献

```http
POST /api/projects/{project_id}/contributions
```

权限：`owner` 或 `member`。

请求：

```json
{
  "user_id": 2,
  "kind": "code",
  "title": "完成后端鉴权模块",
  "description": "实现注册、登录、退出和权限校验",
  "quantity": 1,
  "evidence_url": "https://github.com/example/repo/pull/1",
  "occurred_at": "2026-08-25T18:00:00Z"
}
```

规则：

- 普通成员只能为自己的 `user_id` 创建贡献
- `owner` 可代录贡献，但必须记录实际操作者
- 默认状态为 `pending`

成功响应：`201 Created`

返回贡献对象。

### 8.4 获取贡献详情

```http
GET /api/contributions/{contribution_id}
```

权限：项目成员。

成功响应：`200 OK`

返回贡献对象。

### 8.5 更新贡献

```http
PATCH /api/contributions/{contribution_id}
```

权限：

- 贡献创建者可更新 `pending` 贡献
- `owner` 可更新任意未确认贡献

请求：

```json
{
  "title": "完成后端鉴权和权限测试",
  "description": "补充权限测试",
  "quantity": 2,
  "evidence_url": "https://github.com/example/repo/pull/2"
}
```

成功响应：`200 OK`

返回更新后的贡献对象。

### 8.6 确认贡献

```http
POST /api/contributions/{contribution_id}/confirm
```

权限：`owner`。

请求：

```json
{ "note": "已核对 PR 和测试记录" }
```

成功响应：`200 OK`

```json
{
  "id": 1,
  "status": "confirmed",
  "confirmed_by": 1,
  "confirmed_at": "2026-08-25T19:00:00Z"
}
```

### 8.7 标记贡献争议

```http
POST /api/contributions/{contribution_id}/dispute
```

权限：`owner`。

请求：

```json
{ "note": "缺少证明材料，需要补充说明" }
```

成功响应：`200 OK`

```json
{
  "id": 1,
  "status": "disputed",
  "dispute_note": "缺少证明材料，需要补充说明"
}
```

有争议贡献不直接计入最终贡献报告。

### 8.8 删除贡献

```http
DELETE /api/contributions/{contribution_id}
```

权限：

- 贡献创建者可删除 `pending` 贡献
- `owner` 可删除任意贡献

成功响应：`204 No Content`

建议软删除，并保留操作记录。

---

## 9. AI 与报告接口

### 9.1 获取任务推荐

```http
GET /api/projects/{project_id}/recommendations
```

权限：项目成员。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `task_id` | integer | 与 `task_name` 二选一 | 指定已有任务 |
| `task_name` | string | 与 `task_id` 二选一 | 新任务标题 |
| `task_type` | string | 否 | 任务类型 |
| `estimated_hours` | number | 否 | 预计工时，默认 1 |
| `limit` | integer | 否 | 候选人数，默认 3 |
| `include_owner` | boolean | 否 | 默认 false，组长不进入候选 |

成功响应重点字段：

- `recommendation_id`：本次生成记录 ID，采纳时使用
- `comparison`：第一候选与第二候选的分差和关键维度对比，便于组长理解“为什么是他”
- `recommendations[].dimensions`：技能/质量/效率/负载四维分数、证据、是否样本不足
- `recommendations[].dimensions.skill.skill_families`：命中的技能族，如后端开发、前端开发、文档与答辩
- `recommendations[].reasons.contrast`：第一候选的对比解释
- `excluded`：未进入候选的成员及原因（`overloaded` / `owner_excluded` / `viewer`）
- `excluded_overloaded`：兼容旧前端的超负载列表
- `disclaimer`：固定为“推荐仅供参考，最终由组长决定。”
- `source`：`rule` 或 `hybrid`；无 LLM Key 时走规则路径

规则：

- 默认只推荐 `member`；`viewer` 永不推荐；组长默认排除
- 技能匹配使用同义词技能族 + 字面技能 + 历史任务类型；未配置 LLM Key 时仍可给出可解释匹配
- 达到最大并发任务数的成员进入 `excluded`，不进候选
- 高负载但未超上限仍可推荐，负载分降低并写明“负载偏高”
- 无评价/无工时按中性分 0.5，理由写“按中性分”
- 推荐只基于项目内事实，不公开排名，不自动指派

### 9.1.1 批量生成未分配任务建议

```http
POST /api/projects/{project_id}/recommendations/batch
```

权限：`owner` 或 `member`。请求 `{"limit":3,"include_owner":false}`。成功返回每个未分配任务的 9.1 结果，不自动指派。

### 9.1.2 推荐历史

```http
GET /api/projects/{project_id}/recommendations/history
```

权限：项目成员。可带 `task_id`。返回生成记录、状态、是否采纳、采纳了谁；`status_label` 提供中文状态展示文案。

### 9.1.3 采纳或手选负责人

```http
POST /api/projects/{project_id}/recommendations/{rec_id}/decide
```

权限：`owner` 或 `member`。请求 `{"user_id":2,"note":"采纳推荐"}`。成功后调用现有任务指派，并写入 `recommendation_events`。若人选不在推荐列表中，`action` 为 `manual`。

### 9.2 获取成员负载分析

```http
GET /api/projects/{project_id}/members/load
```

权限：项目成员。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "generated_at": "2026-08-25T10:00:00Z",
  "members": [
    {
      "user_id": 2,
      "name": "张三",
      "current_task_count": 3,
      "max_concurrent_tasks": 3,
      "remaining_capacity": 0,
      "load_ratio": 1.0,
      "load_level": "high",
      "estimated_hours": 18,
      "active_task_ids": [1, 2, 3],
      "weighted_load": 1.3,
      "weighted_level": "high",
      "weighted_label": "高负载",
      "weighted_overdue_tasks": 1
    }
  ]
}
```

字段说明（D2 深化，向后兼容）：

- `load_ratio` / `load_level`：按任务数计数（保留原语义，前端不受影响）。
- `weighted_load`：加权负载 = Σ状态权重 ÷ 最大并发任务数；状态权重默认进行中 1.0、已分配 0.6、暂停 0.5、延期 1.3，可用 `LOAD_WEIGHT_IN_PROGRESS` / `LOAD_WEIGHT_ASSIGNED` / `LOAD_WEIGHT_PAUSED` / `LOAD_WEIGHT_OVERDUE` 环境变量覆盖。
- `weighted_level` / `weighted_label`：按加权比计算，`<0.5` 低负载、`0.5-0.8` 正常、`>0.8` 高负载。
- `weighted_overdue_tasks`：该成员当前延期任务数。

### 9.3 获取项目风险

```http
GET /api/projects/{project_id}/risks
```

权限：项目成员。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "generated_at": "2026-08-25T10:00:00Z",
  "count": 2,
  "summary": "当前最需要关注：任务「完成后端鉴权模块」已延期。建议优先处理延期调度与指派。",
  "summary_source": "llm",
  "risks": [
    {
      "type": "overdue_task",
      "level": "high",
      "severity": 90,
      "message": "任务「完成后端鉴权模块」已延期",
      "task_id": 1,
      "due_date": "2026-09-10"
    },
    {
      "type": "high_member_load",
      "level": "medium",
      "severity": 65,
      "message": "张三当前负载为 3/3",
      "user_id": 2,
      "current_task_count": 3,
      "max_concurrent_tasks": 3
    }
  ]
}
```

Query 参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `summarize` | bool | 否 | `true` | 是否生成 LLM 风险总结；传 `0`/`false` 跳过（批量/性能场景） |

`type` 可选值：`overdue_task`、`upcoming_deadline`、`unassigned_task`、`high_member_load`、`no_recent_activity`、`critical_unassigned`。  
`level` 可选值：`low`、`medium`、`high`。

`severity`（0-100，输出按降序排列）：`critical_unassigned=95`、`overdue_task=90`、`upcoming_deadline=70`、`high_member_load=65`、`unassigned_task=60`、`no_recent_activity=30`。

`summary` / `summary_source`（D2 深化）：

- 默认 `summarize=true` 且存在风险时才生成 `summary`；LLM 未配置或任一段失败时 `summary_source="rule"` 回退规则拼接，接口不报错。
- `critical_unassigned`：高优先级（`priority='high'`）且未分配的任务，代表“关键任务无人承接”。

### 9.4 生成项目周报

```http
GET /api/projects/{project_id}/weekly-report
```

权限：项目成员。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `week_start` | date | 否 | 默认本周周一；格式非法返回 422；传入后按周一归一化 |
| `start_date` | date | 否 | 兼容旧参数；与 `end_date` 同传时按周一归一化后视作 `week_start` |
| `end_date` | date | 否 | 兼容旧参数 |
| `refresh` | bool | 否 | `true` 时强制重新生成并覆盖该周期（不产生重复行） |
| `format` | string | 否 | `json` 或 `markdown`，默认 `json` |

行为：

- 首次访问某周：基于真实数据实时生成（LLM 逐成员摘要 + 整体洞察）并落库 `weekly_reports`，返回 `stored=true`。
- 再次访问同周：直接读库返回（除非 `refresh=true`）。
- LLM 任一段失败或未配置：该段回退规则文本，整体 `source` 取 `llm | mixed | rule`，`llm_error` 记录失败原因但不阻塞接口。
- 回看上周：传 `week_start=<上周周一>` 即可，未生成过则实时生成并落库。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "project_name": "软件工程课程大作业",
  "period": {
    "start_date": "2026-08-24",
    "end_date": "2026-08-30",
    "week_start": "2026-08-24"
  },
  "summary": {
    "tasks_total": 18,
    "tasks_completed": 4,
    "tasks_in_progress": 5,
    "tasks_overdue": 1,
    "checkin_count": 12,
    "contribution_count": 8,
    "actual_hours": 26.5
  },
  "highlights": ["完成登录鉴权模块", "完成数据库设计"],
  "risks": ["任务「接口联调」临近截止且尚未分配"],
  "next_actions": ["优先分配未完成任务", "为延期任务调整排期"],
  "members": [
    {
      "user_id": 2,
      "name": "张三",
      "completed_tasks": 2,
      "active_tasks": 2,
      "checkin_count": 4,
      "actual_hours": 12.5,
      "summary": "本周完成 2 项任务，累计工时 12.5 小时",
      "summary_source": "llm"
    }
  ],
  "insight": "本周整体进度正常，主要风险为……建议……",
  "insight_source": "llm",
  "source": "llm",
  "llm_error": null,
  "stored": true,
  "generated_at": "2026-08-25T10:00:00Z"
}
```

规则：

- 周报只能基于真实项目数据生成
- 不做成员排名
- 不输出人格评价或“摸鱼”判断

### 9.4.1 周报历史

```http
GET /api/projects/{project_id}/weekly-report/history
```

权限：项目成员。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `limit` | int | 否 | 默认 20，最大 100 |
| `before` | date | 否 | 只返回周期开始早于该日期的记录 |

成功响应：`200 OK`，`items` 按 `period_start` 倒序，每项含 `id / period_start / period_end / source / llm_error / created_by / created_at / updated_at / tasks_completed / checkin_count / risks_count`（不含大 payload）。


### 9.5 获取项目报告

```http
GET /api/projects/{project_id}/report
```

权限：项目成员。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "project_name": "软件工程课程大作业",
  "generated_at": "2026-08-25T10:00:00Z",
  "overall": {
    "tasks_total": 18,
    "tasks_completed": 6,
    "tasks_in_progress": 5,
    "tasks_overdue": 2,
    "progress": 33
  },
  "members": [
    {
      "user_id": 2,
      "name": "张三",
      "tasks_total": 6,
      "tasks_completed": 3,
      "tasks_overdue": 1,
      "average_quality": 4.5,
      "actual_hours": 18.5,
      "contributions": [
        { "kind": "code", "quantity": 5 },
        { "kind": "document", "quantity": 2 }
      ]
    }
  ]
}
```

默认只统计 `confirmed` 贡献，不生成成员排名。

### 9.6 导出项目报告

```http
GET /api/projects/{project_id}/report/export
```

权限：项目成员。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `format` | string | 否 | `markdown` 或 `pdf`，默认 `markdown` |

成功响应：

- Markdown：`Content-Type: text/markdown; charset=utf-8`
- PDF：`Content-Type: application/pdf`

### 9.7 Agent 对话

```http
POST /api/projects/{project_id}/agent/chat
```

权限：项目成员。

请求：

```json
{
  "message": "目前项目最大的风险是什么？",
  "session_id": "default"
}
```

字段要求：

- `message`：必填，1-2000 字符
- `session_id`：可选，默认 `default`，1-100 字符

成功响应：`200 OK`

```json
{
  "answer": "当前项目最大的风险是任务「完成后端鉴权模块」已经延期，建议优先处理。",
  "source": "llm",
  "llm_error": null,
  "plan": [
    { "tool": "snapshot", "purpose": "读取任务、成员、风险和贡献事实" }
  ],
  "tool_trace": [
    { "tool": "risk_detail", "args": {}, "ok": true, "error": null }
  ],
  "citations": [
    {
      "type": "task",
      "task_id": 3,
      "title": "完成后端鉴权模块",
      "status": "in_progress"
    }
  ],
  "facts": {
    "project_id": 1,
    "risk_count": 1
  },
  "memory": [
    {
      "role": "user",
      "content": "目前项目最大的风险是什么？",
      "created_at": "2026-08-25T10:00:00Z"
    }
  ],
  "generated_at": "2026-08-25T10:00:00Z"
}
```

`source` 可选值：`llm`、`fallback`（LLM 任一环节失败时回退规则回答，`llm_error` 记录原因，不阻塞接口）。

响应字段说明：

- `plan`：本次问题触发的初始工具规划（规则分支，不依赖 LLM）。
- `tool_trace`：实际执行过的工具轨迹，含工具名、参数与成功/失败状态，供前端展示推理过程。
- `citations`：从工具结果中提取的来源引用（任务 / 风险 / 成员 / 周报），可追溯到具体事实。
- `facts`：本轮收集到的项目事实快照（脱敏，不含 API Key）。
- `memory`：当前会话记忆（超过阈值后自动压缩为 `role=summary` 摘要，前插返回）。

Agent 行为规则：

- Agent 只能读取当前用户有权访问的项目，且只能调用白名单只读工具（`snapshot` / `recommend` / `task_detail` / `risk_detail` / `weekly_report` / `member_load`），不会出现未知工具调用。
- 回答必须基于项目事实（LLM 只能依据注入的 `facts` / `tool_trace`），不输出成员排名、人格评价或负面标签。
- LLM 采用多步推理循环（ReAct 简化版），`AGENT_MAX_STEPS`（默认 4）控制最大轮数，超限或失败时自动规则兜底。
- 长对话自动摘要压缩：`AGENT_SUMMARY_THRESHOLD`（默认 8）与 `AGENT_SUMMARY_LIMIT`（默认 8）控制，摘要失败时保留原消息不丢上下文。

### 9.8 获取 Agent 会话列表

```http
GET /api/projects/{project_id}/agent/sessions
```

权限：项目成员。

成功响应：`200 OK`

返回会话列表，包含 `session_id`、`last_message`、`message_count`、`updated_at`。

### 9.9 清空 Agent 会话

```http
DELETE /api/projects/{project_id}/agent/sessions/{session_id}
```

权限：项目成员。

成功响应：`204 No Content`

---

## 10. 外部平台接入接口

平台接入采用统一适配层。GitHub、飞书、腾讯文档、会议系统等具体平台都通过相同的连接、授权、项目绑定、同步和事件模型接入。

> 实现状态（2026-08-28）：GitHub 已实现（OAuth 授权/回调/断开、项目同步/去重、token 后端混淆存储），对应 `backend/routers/integrations.py` 与前端接入组件；飞书 / 腾讯文档 / 会议系统等其余平台仍为 TODO，前端仅预留平台列表。

支持的 `platform` 值：

- `github`
- `gitlab`
- `gitee`
- `feishu`
- `tencent_doc`
- `tencent_meeting`
- `dingtalk`
- `zoom`
- `teams`
- `notion`
- `yuque`

### 10.1 通用平台接口

#### 获取可用平台列表

```http
GET /api/integrations/platforms
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "items": [
    {
      "platform": "github",
      "name": "GitHub",
      "category": "code",
      "oauth_supported": true,
      "scopes": ["repo", "read:org"],
      "enabled": true
    }
  ]
}
```

#### 获取当前用户平台连接

```http
GET /api/integrations/connections
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "items": [
    {
      "id": 1,
      "platform": "github",
      "external_username": "example",
      "connected_at": "2026-08-25T10:00:00Z",
      "last_synced_at": "2026-08-25T11:00:00Z",
      "scopes": ["repo", "read:org"]
    }
  ]
}
```

#### 发起平台 OAuth

```http
POST /api/integrations/{platform}/oauth/start
```

权限：需要登录。

请求：

```json
{
  "redirect_uri": "https://example.com/settings/integrations/callback"
}
```

成功响应：`200 OK`

```json
{
  "authorize_url": "https://github.com/login/oauth/authorize?client_id=xxx&state=xxx",
  "state": "random-state"
}
```

#### 建立平台连接

```http
POST /api/integrations/{platform}/connections
```

权限：需要登录。

请求：

```json
{
  "code": "oauth-code",
  "state": "random-state"
}
```

成功响应：`201 Created`

返回平台连接对象。

#### 删除平台连接

```http
DELETE /api/integrations/connections/{connection_id}
```

权限：需要登录。

成功响应：`204 No Content`

#### 获取项目平台集成

```http
GET /api/projects/{project_id}/integrations
```

权限：项目成员。

成功响应：`200 OK`

```json
{
  "items": [
    {
      "id": 1,
      "project_id": 1,
      "platform": "github",
      "resource_type": "repository",
      "resource_id": "example/repo",
      "resource_url": "https://github.com/example/repo",
      "enabled": true,
      "last_synced_at": "2026-08-25T11:00:00Z"
    }
  ]
}
```

#### 创建项目平台集成

```http
POST /api/projects/{project_id}/integrations
```

权限：`owner`。

请求：

```json
{
  "platform": "feishu",
  "resource_type": "wiki_space",
  "resource_id": "wiki_123",
  "resource_url": "https://example.feishu.cn/wiki/wsp_123",
  "sync_from": "2026-09-01T00:00:00Z"
}
```

成功响应：`201 Created`

返回项目平台集成对象。

#### 手动同步平台数据

```http
POST /api/projects/{project_id}/integrations/{integration_id}/sync
```

权限：`owner`。

请求：

```json
{
  "since": "2026-09-01T00:00:00Z"
}
```

成功响应：`202 Accepted`

```json
{
  "job_id": "sync_20260825_0001",
  "status": "running"
}
```

#### 获取平台外部事件

```http
GET /api/projects/{project_id}/integrations/{integration_id}/events
```

权限：项目成员。

成功响应：`200 OK`

```json
{
  "items": [
    {
      "id": 1,
      "platform": "github",
      "event_type": "pull_request",
      "external_id": "PR-1",
      "actor_user_id": 2,
      "occurred_at": "2026-08-25T10:00:00Z",
      "metadata": {
        "action": "opened",
        "url": "https://github.com/example/repo/pull/1"
      }
    }
  ]
}
```

#### 通用约束

- 平台凭证必须加密存储，接口永远不返回 access token。
- 外部事件先落库，再预生成 `pending` 状态的贡献。
- 同步失败必须记录错误并支持重试。
- 平台不可用时不能影响手动贡献记录。
- 每条自动生成的贡献都必须能追溯到原始平台事件。

### 10.2 获取 GitHub 绑定状态

```http
GET /api/github/status
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "connected": true,
  "github_username": "example",
  "connected_at": "2026-08-25T10:00:00Z"
}
```

### 10.3 发起 GitHub OAuth

```http
POST /api/github/oauth/start
```

权限：需要登录。

请求：

```json
{
  "redirect_uri": "https://example.com/settings/github/callback"
}
```

成功响应：`200 OK`

```json
{
  "authorize_url": "https://github.com/login/oauth/authorize?client_id=xxx&state=xxx",
  "state": "random-state"
}
```

说明：`state` 由服务端生成并校验，防止 CSRF。

### 10.4 GitHub OAuth 回调

推荐由 GitHub 直接回调到后端：

```http
GET /api/github/oauth/callback?code=xxx&state=xxx
```

成功行为：

- 校验 `state`
- 使用 `code` 换取 access token
- 加密保存 token
- 重定向到前端绑定成功页面

如果采用前端处理回调，可使用：

```http
POST /api/github/connections
```

请求：

```json
{
  "code": "xxx",
  "state": "xxx"
}
```

成功响应：`200 OK`

返回当前用户 GitHub 绑定状态。

### 10.5 绑定项目仓库

```http
POST /api/projects/{project_id}/github/repositories
```

权限：`owner`。

请求：

```json
{
  "repository_url": "https://github.com/example/repo",
  "default_branch": "main",
  "sync_from": "2026-09-01"
}
```

成功响应：`201 Created`

```json
{
  "id": 1,
  "project_id": 1,
  "repository_url": "https://github.com/example/repo",
  "default_branch": "main",
  "sync_from": "2026-09-01",
  "last_synced_at": null,
  "created_at": "2026-08-25T10:00:00Z"
}
```

### 10.6 同步 GitHub 数据

```http
POST /api/projects/{project_id}/github/sync
```

权限：`owner`。

请求：

```json
{
  "repository_id": 1,
  "since": "2026-09-01T00:00:00Z"
}
```

成功响应：`200 OK`

```json
{
  "repository_id": 1,
  "synced_at": "2026-08-25T10:00:00Z",
  "statistics": {
    "new_commits": 12,
    "new_pull_requests": 3,
    "new_issues": 2,
    "new_reviews": 5
  }
}
```

### 10.7 获取 GitHub 统计

```http
GET /api/projects/{project_id}/github/statistics
```

权限：项目成员。

Query 参数：`repository_id`、`user_id`、`start_date`、`end_date`。

成功响应：`200 OK`

```json
{
  "project_id": 1,
  "members": [
    {
      "user_id": 2,
      "github_username": "example",
      "commits": 18,
      "additions": 1200,
      "deletions": 300,
      "pull_requests": 4,
      "reviews": 6,
      "issues": 3
    }
  ]
}
```

规则：

- Commit 数量不能作为唯一贡献标准
- GitHub 数据进入贡献账本时，默认状态为 `pending`
- 成员可补充说明贡献背景

### 10.8 解绑 GitHub

```http
DELETE /api/github/connections/current
```

权限：需要登录。

成功响应：`204 No Content`

解绑后删除或失效化 access token；历史统计可以保留，但不能再继续同步。

---

## 11. 长期协作接口

### 11.1 获取历史项目

```http
GET /api/users/me/history
```

权限：需要登录。

Query 参数：`project_type`、`year`、`page`、`page_size`。

成功响应：`200 OK`

```json
{
  "items": [
    {
      "project_id": 1,
      "name": "软件工程课程大作业",
      "project_type": "课程项目",
      "status": "archived",
      "role": "member",
      "start_date": "2026-09-01",
      "end_date": "2026-12-20",
      "tasks_completed": 8,
      "average_quality": 4.4
    }
  ]
}
```

### 11.2 获取跨项目协作履历

```http
GET /api/users/me/history
GET /api/users/profile/{user_id}/history
```

需要登录。第一种读取当前用户；第二种仅允许读取与当前用户存在 active 共同班级关系的成员。返回真实项目、任务参与和贡献记录，不生成推测性标签。

### 11.3 获取个人协作画像

```http
GET /api/users/me/profile
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "user_id": 1,
  "name": "张三",
  "project_count": 3,
  "completed_task_count": 42,
  "average_quality": 4.3,
  "efficiency": 1.1,
  "on_time_rate": 0.88,
  "top_skills": [
    { "skill": "后端", "score": 88 },
    { "skill": "Python", "score": 84 }
  ],
  "collaboration_types": [
    { "type": "code", "ratio": 0.55 },
    { "type": "document", "ratio": 0.25 }
  ],
  "data_sources": [
    { "source": "completed_tasks", "count": 42 },
    { "source": "confirmed_contributions", "count": 65 }
  ],
  "generated_at": "2026-08-25T10:00:00Z"
}
```

规则：

- 画像只基于项目成果和已确认贡献
- 不输出人格评价、道德评价或公开排名
- 用户可查看数据来源和计算口径

### 11.4 获取跨项目合作关系

```http
GET /api/users/me/collaborations
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "items": [
    {
      "user_id": 2,
      "name": "李四",
      "shared_project_count": 3,
      "shared_task_count": 12,
      "last_collaborated_at": "2026-12-20",
      "cooperation_score": 82
    }
  ]
}
```

只统计双方共同参与且已授权用于协作分析的项目。

### 11.4 获取长期任务推荐

```http
GET /api/users/me/recommendations
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "recommendations": [
    {
      "skill": "后端",
      "score": 88,
      "reason": "历史完成后端任务质量较高，效率稳定。"
    }
  ]
}
```

新用户缺少历史数据时，使用技能和负载规则推荐。

### 11.5 获取数据授权设置

```http
GET /api/users/me/authorizations
```

权限：需要登录。

成功响应：`200 OK`

```json
{
  "cross_project_profile": true,
  "collaboration_analysis": true,
  "history_visible": true
}
```

### 11.6 更新数据授权

```http
PATCH /api/users/me/authorizations
```

权限：需要登录。

请求：

```json
{
  "cross_project_profile": false,
  "collaboration_analysis": true,
  "history_visible": true
}
```

成功响应：`200 OK`

返回更新后的授权对象。

用户关闭授权后，相关跨项目分析必须停止使用其数据；个人画像必须停止更新或删除。

---

## 12. 接口实现优先级

### P0：第一阶段必须实现

1. `POST /api/auth/register`
2. `POST /api/auth/login`
3. `POST /api/auth/logout`
4. `GET /api/auth/me`
5. `PATCH /api/users/me`
6. `GET /api/projects`
7. `POST /api/projects`
8. `GET /api/projects/{project_id}`
9. `PATCH /api/projects/{project_id}`
10. `POST /api/projects/{project_id}/archive`
11. `GET /api/projects/{project_id}/members`
12. `PATCH /api/projects/{project_id}/members/{user_id}`
13. `POST /api/projects/{project_id}/invitations`
14. `GET /api/invitations/{code}`
15. `POST /api/invitations/{code}/accept`
16. `GET /api/projects/{project_id}/tasks`
17. `POST /api/projects/{project_id}/tasks`
18. `PATCH /api/tasks/{task_id}`
19. `POST /api/tasks/{task_id}/assign`
20. `POST /api/tasks/{task_id}/start|pause|resume|complete|overdue|unfinished`
21. `GET /api/tasks/{task_id}/logs`
22. `GET /api/tasks/{task_id}/checkins`
23. `POST /api/tasks/{task_id}/checkins`
24. `GET /api/tasks/{task_id}/review`
25. `POST /api/tasks/{task_id}/review`

### P1：第二阶段优先实现

1. `GET /api/projects/{project_id}/recommendations`
2. `GET /api/projects/{project_id}/members/load`
3. `GET /api/projects/{project_id}/risks`
4. `GET /api/projects/{project_id}/weekly-report`
5. `GET /api/projects/{project_id}/report`
6. `POST /api/projects/{project_id}/agent/chat`
7. 贡献相关接口
8. 报告导出接口

### P2：第三阶段以后实现

1. ~~GitHub 接入接口~~（D5 已实现，见第 10 章）
2. 长期协作接口
3. 个人画像接口
4. 跨项目合作关系接口
5. 数据授权接口

---

## 13. 前端对接注意事项

### 13.1 请求封装

```javascript
async function request(url, options = {}) {
  const response = await fetch(url, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  })

  if (response.status === 401) {
    // 跳转登录页
  }

  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const message = payload?.error?.message || "请求失败，请稍后重试"
    throw new Error(message)
  }

  if (response.status === 204) return null
  return response.json()
}
```

### 13.2 权限 UI

- 未登录：跳转登录页
- 非项目成员：显示无权限页面
- `viewer`：隐藏所有写操作按钮
- `member`：隐藏项目设置、成员管理、贡献确认按钮
- `owner`：显示完整管理入口

### 13.3 状态刷新

任务状态变化后，应同时刷新：

- 任务详情
- 任务日志
- 项目统计
- 成员负载
- 风险列表

### 13.4 AI 展示

- 推荐必须展示理由
- 周报和 Agent 回答需标注生成时间
- Agent 失败时展示兜底结果和错误提示
- 不展示成员排名

---

## 14. 接口冻结规则

1. P0 接口应先冻结，再进入并行开发。
2. 修改接口必须同步修改本文档。
3. 修改字段类型、必填性、权限或状态码属于破坏性变更。
4. 破坏性变更需通知所有前后端开发成员。
5. 新增字段尽量设计为可选，避免破坏旧客户端。
6. 每个接口实现时必须补充：
   - Pydantic schema
   - 权限校验
   - 错误处理
   - 单元测试

---

## 15. 与当前代码的对齐建议

当前代码已有部分基础接口，但与本文档目标契约存在差异。建议按以下顺序对齐：

1. 实现统一错误响应。
2. 实现认证、退出和当前用户。
3. 给现有项目、任务、贡献接口补权限。
4. 新增邀请、打卡、质量评价接口。
5. 对齐 AI、报告和 Agent 响应结构。
6. 最后实现 GitHub 和长期协作接口。

开发过程中不要一次性重写全部接口，应保持现有可用功能，逐步迁移到本文档契约。
