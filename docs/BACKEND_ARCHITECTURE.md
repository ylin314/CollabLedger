# 后端架构

## 分层

```text
backend/
  main.py                  FastAPI 应用组装、中间件、异常处理和兼容导出
  core/
    context.py             配置、数据库依赖、权限和序列化公共能力
    errors.py              统一业务异常与错误响应
  routers/
    auth_users.py          注册、登录和用户资料
    projects.py            项目、成员和邀请
    tasks.py               任务、状态流转、打卡和评价
    contributions.py       贡献账本
    analytics.py           负载、推荐、风险和报告 HTTP 接口
    agent.py               Agent HTTP 接口
    system.py              健康检查
  services/
    analytics.py           推荐、负载、风险、周报和报告业务计算
    agent_runtime.py        Agent runtime 工厂
  repositories/
    entities.py            任务与贡献实体查询
  schemas.py               API 输入模型
  models.py                完整 SQLAlchemy metadata
  db.py                    SQLite/PostgreSQL、Session 和兼容数据访问层
  auth.py                  密码散列与会话管理
  audit.py                 审计日志写入
  rate_limit.py            写接口限流中间件
  agent/                   Agent 配置、工具、计划、记忆与 LLM 运行时
  test_*.py                自动化回归测试
alembic/
  versions/                版本化数据库迁移
```

`main.py` 只负责应用装配，现有 65 个 API 路由由领域 Router 注册。为了兼容旧测试和 Agent 工具，`main.py` 继续 re-export 少量公开函数，但实现不再位于入口文件。

## 数据库策略

- 默认 SQLite，便于本地零配置运行。
- 设置 `DATABASE_URL` 后切换到 PostgreSQL。
- 新代码使用 `session_scope()` 和 SQLAlchemy Model。
- 旧业务 SQL 通过 SQLAlchemy 兼容连接运行，可在不改变外部 API 的前提下渐进迁移。
- 表结构由 Alembic 和 `Base.metadata` 管理；旧 SQLite 库使用幂等升级器保留数据。

## 依赖方向

- `main` 组装 routers，不包含业务路由实现。
- routers 依赖 core、services、repositories 和 schemas。
- services 不依赖 FastAPI App。
- repositories 只负责数据读取或持久化，不依赖 routers。
- 数据库模块不得导入路由或 Agent runtime。
- Agent tools 只能读取已通过项目权限验证的项目事实。
- 安全审计失败不得改变原业务响应，但必须写服务端异常日志。

## 质量门禁

```powershell
python -m compileall -q backend alembic
python -m pytest -q backend
cd frontend
npm run build
```

数据库变更还必须验证：

```powershell
python -m alembic upgrade head
python -m alembic current
```
