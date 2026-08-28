# 数据库结构与迁移

## 数据库入口

- 默认数据库：项目根目录 `collab.db`（SQLite）。
- 可通过环境变量 `COLLAB_DB` 指定 SQLite 文件路径。
- 设置 `DATABASE_URL` 或 `COLLAB_DATABASE_URL` 后切换到 PostgreSQL，例如 `postgresql+psycopg://user:password@host:5432/collab_ledger`。
- 应用启动时由 `backend.db.initialize()` 执行幂等初始化：SQLite 兼容旧 MVP 数据，PostgreSQL 使用完整 SQLAlchemy metadata。
- 新代码可使用 `backend.db.session_scope()` 获取 SQLAlchemy Session；兼容适配器保证旧 API 查询可在迁移期间运行。
- 正式版本迁移由 Alembic 管理，配置文件为 `alembic.ini`，迁移脚本位于 `alembic/versions/`。

## 迁移命令

```powershell
# 升级到最新版本
python -m alembic upgrade head

# 查看当前版本
python -m alembic current

# 回退一个版本（执行前必须备份）
python -m alembic downgrade -1
```

生产环境执行迁移前必须先按 `docs/BACKUP_RESTORE.md` 创建并校验备份。应用启动时的兼容初始化用于承接旧 MVP 数据库，不替代版本化 Alembic 变更。

## 核心业务表

| 表 | 用途 |
| --- | --- |
| `users` | 用户账号、技能、工作状态和密码散列 |
| `auth_sessions` | 登录会话摘要、过期与撤销状态 |
| `classrooms` | 长期班级成员池 |
| `classroom_memberships` | 班级成员加入/退出历史与角色 |
| `projects` | 项目信息、所属班级、归档和软删除状态 |
| `memberships` | 用户在项目中的角色、加入/退出状态 |
| `task_participants` | 任务多人参与关系及历史 |
| `project_invitations` | 邀请码、角色、有效期和使用次数 |
| `tasks` | 任务、负责人、状态、工时和质量 |
| `task_logs` | 任务状态与字段变化记录 |
| `task_checkins` | 成员主动打卡、工时和阻塞点 |
| `task_reviews` | 当前任务质量评价 |
| `task_review_history` | 任务质量评价历史 |
| `work_logs` | 项目成员按日期主动填写的工作日志 |
| `quality_reviews` | 项目范围的成员质量评价 |
| `contributions` | 手动或外部平台产生的贡献记录 |

## 平台、Agent 与分析表

| 表 | 用途 |
| --- | --- |
| `platform_connections` | 用户授权的外部平台连接 |
| `project_integrations` | 项目绑定的平台集成及配置 |
| `external_events` | 外部平台同步的原始事件 |
| `sync_jobs` | 同步任务状态、游标和错误 |
| `agent_sessions` | 项目 Agent 会话元数据 |
| `agent_messages` | Agent 会话消息 |
| `agent_memory` | 旧版 Agent 兼容记忆表 |
| `recommendations` | 任务推荐结果快照、采纳状态与来源 |
| `recommendation_events` | 推荐生成/采纳/手选事件 |
| `weekly_reports` | 周报快照持久化（周期、source、LLM 错误、完整 payload） |
| `audit_logs` | 所有 HTTP 写操作的安全审计事件 |

## 约束约定

1. 所有项目数据通过 `project_id` 隔离。
2. 业务外键启用 SQLite `PRAGMA foreign_keys=ON`。
3. 时间戳统一为 UTC ISO-8601，后缀为 `Z`。
4. 项目、任务和贡献采用软删除字段，避免误删后不可恢复。
5. 密码、原始会话令牌和外部平台密钥不得写入审计日志。
6. 表结构变更必须新增 Alembic revision，不允许手工修改生产库。
