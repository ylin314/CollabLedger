# 协作账本

面向小组作业的智能协作与贡献管理系统 MVP。

## Docker 一键部署（推荐）

确保已安装 Docker Desktop，然后在项目根目录执行：

```powershell
docker compose up -d --build
```

部署完成后访问：

- 系统首页：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

查看状态和日志：

```powershell
docker compose ps
docker compose logs -f
```

停止服务：

```powershell
docker compose down
```

SQLite 数据存放在 Docker 命名卷 `collab-ledger-data` 中，普通重建或 `docker compose down` 不会丢失数据。只有明确执行 `docker compose down -v` 才会一并删除数据卷。

如需修改宿主机端口：

```powershell
$env:PORT=8080
docker compose up -d --build
```

如果拉取基础镜像时提示连接 `127.0.0.1:7897` 失败，说明 Docker Desktop 配置了本地代理但代理程序未运行。请在 Docker Desktop 的 Settings → Resources → Proxies 中关闭该代理，或先启动对应代理程序，再重新执行一键部署命令。



### PostgreSQL 生产数据库

默认仍可零配置使用 SQLite。生产环境可启用 PostgreSQL：

```powershell
$env:POSTGRES_PASSWORD='请设置高强度密码'
docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres up -d --build
```

应用通过 SQLAlchemy/Alembic 管理完整数据库结构。PostgreSQL 备份和恢复分别使用 `scripts/backup_postgres.ps1` 与 `scripts/restore_postgres.ps1`。

### 生产 HTTPS、备份与迁移

生产部署使用 Caddy HTTPS overlay：

```powershell
docker compose -f compose.yaml -f compose.https.yaml --profile https up -d --build
```

上线前需要在 `.env` 配置正式域名、严格 CORS、Secure Cookie 和可信代理。完整说明见 `docs/DEPLOYMENT.md`。

数据库备份和恢复：

```powershell
.\scripts\backup.ps1
.\scripts\restore.ps1 -BackupFile .\backups\collab-时间戳.db -Yes
```

备份使用 SQLite Online Backup API 并自动执行完整性检查；详细流程见 `docs/BACKUP_RESTORE.md`。正式迁移由 Alembic 管理：

```powershell
python -m alembic upgrade head
python -m alembic current
```

## 后端启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

项目根目录的 `.env` 是 Agent 的唯一配置入口。后端会自动读取它；Docker Compose 也会把同一组变量注入容器。`.env` 已加入 `.gitignore`，不会提交到 Git。

当前 `.env` 使用 OpenAI Chat Completions 兼容协议（不使用 Responses API）。完整模板见 `.env.example`：

```dotenv
LLM_BASE_URL=https://aigw.saurlax.com/
LLM_API_KEY=你的APIKey
LLM_MODEL=deepseek-v4-flash
LLM_CHAT_COMPLETIONS_URL=
RECOMMEND_SKILL_MODE=llm
RECOMMEND_USE_LLM_SKILL=true
RECOMMEND_USE_LLM_REASON=true
```

`LLM_CHAT_COMPLETIONS_URL` 留空时自动请求 `${LLM_BASE_URL}/v1/chat/completions`。未配置 Key 时，D1 推荐自动走规则路径，不阻断演示。

## 前端开发与生产预览

```powershell
cd frontend
npm install
npm run dev
```

开发服务器默认运行在 <http://127.0.0.1:5173>，并将 `/api` 请求代理到后端 8000 端口。
生产构建使用 `npm run build`；构建后重新启动后端，FastAPI 会自动托管 `frontend/dist`，可直接访问 <http://127.0.0.1:8000>。

启动后访问 <http://127.0.0.1:8000/docs> 查看交互式 API 文档，健康检查地址为 `/api/health`。默认 SQLite 数据库为项目根目录 `collab.db`；可通过 `COLLAB_DB` 环境变量指定路径。前端开发服务器可跨域访问 API（已启用 CORS）。若存在 `frontend/dist`，后端会在根路径自动托管构建产物。

核心接口包括：

- `/api/projects`、`/api/projects/{id}/members`：项目和成员管理
- `/api/projects/{id}/tasks`、`/api/tasks/{id}/start|pause|resume|complete`：任务与生命周期日志
- `/api/projects/{id}/contributions`：手动或平台同步的贡献记录
- `/api/projects/{id}/recommendations`：基于技能、质量、效率和负载的任务推荐
- `/api/projects/{id}/report`、`/api/projects/{id}/agent`：贡献报告与轻量协作 Agent

当前前端不再使用内置演示数据伪装成功；API 失败会直接提示错误。任务、贡献、推荐、负载、风险和周报均写入 SQLite。

首次使用时，如果数据库中没有项目，打开系统会直接显示“创建你的第一个项目”页面。填写项目名称、类型、周期和组长信息后，系统会先创建用户，再创建真实项目，之后所有任务和 Agent 对话都会使用该项目，不再使用演示项目。

也可以通过 API 创建：

```powershell
$owner = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/users -ContentType 'application/json' -Body '{"name":"张三","skills":["Python","后端"],"status":"online"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/projects -ContentType 'application/json' -Body (@{name='软件工程课程大作业';project_type='课程项目';owner_id=$owner.id} | ConvertTo-Json)
```

隐私边界：本 MVP 只记录项目任务、主动填写的贡献和协作分析，不采集私人聊天、桌面、摄像头、键盘、鼠标或屏幕数据；推荐和 Agent 只提供可解释建议，不生成成员排名或“摸鱼”判断。

### Agent 四层结构

- `tool`：读取项目任务、成员负载、风险、报告，并调用规则推荐器。
- `memory`：按项目和会话保存 Agent 对话记忆，使用当前配置的 SQLite 或 PostgreSQL 数据库持久化。
- `plan`：先根据问题规划需要读取的工具，再执行工具，避免模型凭空猜测。
- `llm`：调用 OpenAI Chat Completions 兼容协议 `POST /v1/chat/completions`，不是 Responses API。

可通过 `GET /api/agent/config` 查看脱敏后的 URL、模型和配置状态；完整 API Key 永远不会由接口返回。

## 当前阶段与角色 TODO

更新时间：2026-08-25。分支约定：`main` 只合可运行代码；rxc 当前开发分支为 `dev_D`。角色对应：A=ly，B=dkd，C=czc，D=rxc。任务编号（A1/B1/C1/D1）保持不变。

| 阶段 | 目标 | 状态 | 说明 |
| --- | --- | --- | --- |
| 阶段一 基础功能 P0 | 注册登录、项目、邀请、任务、打卡、评价、看板 | 已完成 | ly/dkd/czc 已形成真实协作闭环 |
| 阶段二 AI 功能 P1 | 推荐、负载、匹配度、风险、周报 | rxc 推进中 | D1 已加深：语义匹配、四维拆开、排除原因、采纳留痕、批量建议；D2-D4 本轮冻结 |
| 阶段三 贡献系统 P1 | 手动贡献 + 外部平台接入 | 部分完成 | 手动贡献/确认/争议已有；GitHub 等接入未做 |
| 阶段四 长期协作 P2 | 历史项目、画像、跨项目授权 | 未开始 | 归档接口有雏形，画像页不要用假数据 |

### 角色 TODO

**ly（A）后端底座 / 账号权限 / 数据库 / 部署**

- [x] A1-A6：拆分 routers/services、注册登录、权限、SQLAlchemy/Alembic、Docker、审计与限流
- [ ] 与 rxc 对齐生产 `.env`（CORS、Secure Cookie、LLM）后再做正式部署验证

**dkd（B）核心业务后端**

- [x] B1-B5：项目、邀请、任务、打卡、质量评价
- [x] B6：手动贡献账本（确认/争议）
- [ ] B7：历史项目查询页面对齐；归档只读体验需 czc 配合

**czc（C）前端产品 / 交互**

- [x] 登录注册、项目空间、看板、打卡、评价、贡献、Agent 对话
- [ ] C1：拆分 `frontend/src/main.jsx`，引入 Router / Query / TypeScript
- [ ] C9：继续把报告页做成独立页面，去掉剩余装饰按钮
- [ ] C10：不要提前做阶段四假画像

**rxc（D）AI / 数据分析 / 平台接入 / 质量（本轮）**

- [x] D1 加深：技能 40% / 质量 30% / 效率 20% / 负载 10%；仅 member 默认候选；超负载排除；无样本中性分 0.5；规则 + LLM 语义匹配/理由润色；四维证据；批量建议；采纳/手选留痕
- [ ] D1 待联调：真实 LLM/Embedding Key 下的语义质量（不阻塞无 Key 演示）
- [x] D2：`/members/load` 与 `/risks`（本轮冻结）
- [x] D3：`/weekly-report`（本轮冻结）
- [x] D4：Agent 只读项目事实（本轮冻结）
- [ ] D5：GitHub / 飞书 / 腾讯文档接入（等 dkd 的 B6 稳定后做，不阻塞阶段二演示）
- [ ] D6：长期画像（阶段四）
- [ ] D7：完整联调与演示手册；当前仅保留少量核心测试

阶段二接口：

- `GET /api/projects/{id}/recommendations`
- `POST /api/projects/{id}/recommendations/batch`
- `GET /api/projects/{id}/recommendations/history`
- `POST /api/projects/{id}/recommendations/{rec_id}/decide`
- `GET /api/projects/{id}/members/load`
- `GET /api/projects/{id}/risks`
- `GET /api/projects/{id}/weekly-report`
- `POST /api/projects/{id}/agent/chat`

本地验证：

```powershell
python -m pytest -q backend/test_stage2.py backend/test_contract_p1.py backend/test_agent.py backend/test_stage1.py
cd frontend
npm run build
```

## 前端路由


前端使用 Hash 路由，兼容 Vite 开发服务器和 Docker 中 FastAPI 的静态托管。工作区页面地址示例：

```text
#/projects/{project_id}/overview
#/projects/{project_id}/tasks
#/projects/{project_id}/recommendations
#/projects/{project_id}/contributions
#/projects/{project_id}/report
#/projects/{project_id}/agent
#/projects/{project_id}/members
#/projects/{project_id}/worklog
#/projects/new
```

页面切换会写入浏览器历史，浏览器的后退/前进按钮、刷新和复制链接都能恢复当前页面。