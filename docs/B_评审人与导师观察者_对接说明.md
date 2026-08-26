# 评审人与导师观察者 — 对接说明

> 负责人：B（核心业务后端）
> 覆盖范围：任务级评审人（`reviewer_id`）、导师观察者邀请（`is_mentor`）
> 面向对象：C（前端）、D（AI/分析）、A（权限/迁移）
> 状态：**已实现并对齐代码**（`backend/routers/tasks.py`、`backend/routers/projects.py`、`backend/schemas.py`、`backend/db.py`、`backend/models.py`）
> 关联文档：详见 `docs/B_业务接口文档.md` 第 4/6/10 章。本文档聚焦本次改动的功能、逻辑与对接要点。

---

## 1. 这次实现了什么

本次改动落地了两个此前只停留在「目标设计」的能力，并把数据库 schema 版本升到 `4`（`SCHEMA_VERSION`，见 `backend/db.py`）：

1. **任务级评审人（reviewer）**
   - 任务新增可空外键 `reviewer_id`（指向 `users`，`ON DELETE SET NULL`）。
   - 谁能评价一个任务，不再只看项目角色，而是「owner **或**该任务被指定的评审人」。
   - 评审人可以是以 `viewer` 身份加入的外部导师，从而实现「外部人不参与开发、只负责打分」。

2. **导师观察者（mentor）**
   - 邀请记录 `project_invitations` 新增标记字段 `is_mentor`（默认 `0`）。
   - 创建项目时可直接传 `mentors` 列表，后端自动生成一次性 viewer 邀请链接。
   - 导师复用现有 `viewer` 角色，**不新增顶层角色**，`is_mentor` 仅供前端区分展示，不改变任何权限。

设计动机：小组作业里「打分的人」常常不是组员（例如助教、导师），既不该占用开发负载，也不该拥有 owner 的管理权。评审授权因此与角色解耦——授权 = 「被设为某任务的 reviewer」，天然记录在任务上、可追溯。

---

## 2. 数据模型变更

### 2.1 `tasks` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `reviewer_id` | `INTEGER` 可空，外键 → `users.id`，`ON DELETE SET NULL` | 该任务指定的评审人；为空表示只有 owner 能评价 |

任务对象在读取时通过 `LEFT JOIN users` 附带返回 `reviewer_name`（见 `backend/repositories/entities.py:task_row` 与 `backend/routers/tasks.py:list_tasks`）。

### 2.2 `project_invitations` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_mentor` | `INTEGER NOT NULL DEFAULT 0` | 该邀请是否为导师观察者邀请，仅作前端标记，不影响权限 |

### 2.3 迁移

- `SCHEMA_VERSION` 升至 `4`（`backend/db.py`）。
- 新建库：`SCHEMA_SQL` 已包含上述字段。
- 存量库：`initialize()` 中的前向迁移 `_add_columns` 幂等补齐 `tasks.reviewer_id`、`project_invitations.is_mentor`，无需手动 DDL。
- 存量库还会幂等补齐 `task_review_history.updated_at`，并对存量数据回填（`UPDATE task_review_history SET updated_at=COALESCE(updated_at,created_at)`），保证历史记录有 `updated_at`。
- 评审授权本身不落独立授权表——它就是任务上的一个字段，变更进任务日志（`task_logs`）。

---

## 3. 接口对接

### 3.1 创建任务 `POST /api/projects/{project_id}/tasks`

请求体新增可选字段 `reviewer_id`：

```json
{
  "title": "后端任务",
  "assignee_id": 12,
  "reviewer_id": 34
}
```

- `reviewer_id` 若非空，**必须是本项目成员**（含以 viewer 加入的导师），否则 `400`。
- 返回的任务对象包含 `reviewer_id` 与 `reviewer_name`。

### 3.2 更新任务 `PATCH /api/tasks/{task_id}`

请求体新增可选字段 `reviewer_id`（传 `null` 表示清空评审人）：

```json
{ "reviewer_id": 34 }
```

修改评审人的权限规则：

- **owner**：可任意设置/清空 `reviewer_id`。
- **任务创建者（`created_by`）**：即使是普通成员，也可以改本任务的 `reviewer_id`。这是普通成员唯一能碰的「非执行字段」。
- 其他普通成员：改 `reviewer_id` 返回 `403`（“只有 owner 或任务创建者可以修改评审人”）。
- 设置的 `reviewer_id` 非空时同样校验其为项目成员，否则 `400`。

> 逻辑细节：`reviewer_id` 被从「普通成员只能改执行字段」的限制中单独放行——如果本次 PATCH 只动 `reviewer_id` 且发起人是创建者，则不再要求发起人是负责人。若同时动了其它字段，其它字段仍走原有「负责人 + 仅 `actual_hours`」的约束。

### 3.3 任务对象

`list_tasks` / `get_task` / `create_task` / `update_task` 返回的任务对象统一新增两个字段：

```json
{
  "reviewer_id": 34,
  "reviewer_name": "张导师"
}
```

（字段来自 `as_task` 白名单，见 `backend/core/context.py`。）

### 3.4 评价任务 `POST /api/tasks/{task_id}/review`

权限从「仅 owner」放宽为：

- 接口最低角色要求从 `owner` 降到 `viewer`，进入后再做判定。
- 任务 `reviewer_id` 为空 → 只有 owner 能评价。
- 任务 `reviewer_id` 非空 → owner **或**该 `reviewer_id` 对应用户能评价。
- 非 owner 且非该任务评审人 → `403`（“只有 owner 或该任务的评审人可以评价”）。
- 保留原约束：非 owner 不能评价自己负责的任务（即评审人不应同时是负责人），且只有 `completed` 状态的任务可评价（否则 `409`）。

### 3.5 创建项目 `POST /api/projects` — 附带导师

请求体新增可选字段 `mentors`（数组；每项的 `email` 必填且须为合法邮箱）：

```json
{
  "name": "带导师的项目",
  "mentors": [{ "email": "mentor@example.com" }]
}
```

行为：

- 每个 mentor 生成一条 `role="viewer"`、`max_uses=1`、有效期 168 小时、`is_mentor=1` 的邀请。
- 创建成功的响应体在有导师时附带 `mentor_invitations` 数组，每项即标准邀请对象（含 `code`、`invite_url`、`is_mentor: true`）。前端拿到后可直接把链接发给导师。
- 未传 `mentors` 时响应体**不包含** `mentor_invitations` 字段。

### 3.6 创建邀请 `POST /api/projects/{project_id}/invitations`

请求体新增可选字段 `is_mentor`（默认 `false`）；当其为 `true` 时，`email` 必填且须为合法邮箱：

```json
{ "role": "viewer", "is_mentor": true, "email": "mentor@example.com" }
```

- 所有邀请对象（创建、列表、按邀请码获取）的返回都新增 `is_mentor` 布尔字段。
- `is_mentor` 仅是标记，不改变邀请角色或权限；导师接受后仍以 `viewer` 身份加入。

---

## 4. 各方对接要点

### 前端（C）
- 创建/编辑任务表单可加「评审人」选择器，候选取项目成员（含 viewer 导师）。
- 任务详情用 `reviewer_name` 展示评审人；`reviewer_id` 为空时评价按钮只对 owner 显示，非空时对 owner 和该评审人显示。
- 成员/邀请列表可用 `is_mentor` 给导师打标签。
- 创建项目页可提供「邀请导师」入口，提交后从 `mentor_invitations` 取链接展示。

### AI / 分析（D）
- 导师是 viewer，不进负载分析与推荐候选（推荐只看可被指派的成员），口径不变。
- `reviewer_id` 可作为「谁评的分」维度纳入统计，评审动作仍记录在 `task_logs` 与 `task_reviews`。

### 权限 / 迁移（A）
- schema 已由 B 侧前向迁移处理，无需额外脚本。
- 待补：在 `PERMISSION_MODEL.md` 增加「任务级评审权（owner 或该任务 reviewer）」规则。
- `memberships` 未加 `is_mentor`：成员表层面导师与普通 viewer 无差别，标记只在邀请记录上。若前端需在成员列表直接区分导师，再评估是否加成员级标记。

---

## 5. 涉及文件

| 文件 | 改动 |
|------|------|
| `backend/db.py` | schema 版本升 4；`tasks.reviewer_id`、`project_invitations.is_mentor` 建表与迁移；`task_review_history.updated_at` 补列与回填 |
| `backend/models.py` | `Task.reviewer_id`、`project_invitations.is_mentor` 映射 |
| `backend/schemas.py` | 新增 `MentorIn`；`ProjectIn.mentors`、`InvitationIn.is_mentor`、`TaskIn.reviewer_id`、`TaskUpdate.reviewer_id` |
| `backend/repositories/entities.py` | `task_row` 关联返回 `reviewer_name` |
| `backend/core/context.py` | `as_task` 白名单加 `reviewer_id`、`reviewer_name` |
| `backend/routers/projects.py` | 抽出 `_insert_invitation`；创建项目支持 `mentors`；邀请支持 `is_mentor` |
| `backend/routers/tasks.py` | 创建/更新任务支持 `reviewer_id` 及校验；评价接口权限放宽 |
| `backend/test/test_reviewer_mentor.py` | 覆盖评审人指派、评价权限、成员校验、导师项目创建 |

---

## 6. 验证

```bash
uv run pytest backend/test/test_reviewer_mentor.py -q
```

当前 3 个用例全部通过，覆盖：
- 评审人指派 + 导师（viewer）评价 + 负责人不能自评；
- 评审人必须是成员 + 创建者/owner 改评审人的权限边界；
- 创建项目带导师生成一次性 viewer 邀请、导师接受后以 viewer 加入。
