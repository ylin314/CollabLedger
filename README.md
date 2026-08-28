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

当前 `.env` 使用 StepFun 的 OpenAI Chat Completions 兼容协议（不使用 Responses API），模型为 `step-3.7-flash`。完整模板见 `.env.example`：

```dotenv
LLM_BASE_URL=https://api.stepfun.com/step_plan/v1
LLM_API_KEY=你的APIKey
LLM_MODEL=step-3.7-flash
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

首次使用时，如果当前登录用户还没有项目，系统会直接显示“创建你的第一个项目”页面。填写项目名称、类型和周期后，当前用户自动成为项目负责人；之后所有任务和 Agent 对话都会归属于这个真实项目。

也可以通过 API 创建：

```powershell
$owner = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/users -ContentType 'application/json' -Body '{"name":"张三","skills":["Python","后端"],"status":"online"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/projects -ContentType 'application/json' -Body (@{name='软件工程课程大作业';project_type='课程项目';owner_id=$owner.id} | ConvertTo-Json)
```

隐私边界：本 MVP 只记录项目任务、主动填写的贡献和协作分析，不采集私人聊天、桌面、摄像头、键盘、鼠标或屏幕数据；推荐和 Agent 只提供可解释建议，不生成成员排名或“摸鱼”判断。

### Agent 四层结构

- `tool`：读取项目任务、成员负载、风险、报告，并调用规则推荐器。
- `memory`：按项目和会话保存 Agent 对话记忆，使用当前配置的 SQLite 或 PostgreSQL 数据库持久化；长对话自动压缩为 `role=summary` 摘要，摘要失败不丢消息。
- `plan`：先根据问题规划需要读取的工具，再执行工具；LLM 可在白名单内继续追加工具调用（ReAct 简化版多步循环），每步结果结构化注入。
- `llm`：调用 OpenAI Chat Completions 兼容协议 `POST /v1/chat/completions`，不是 Responses API；结构化决策返回 JSON，失败自动重试并回退规则。

可通过 `GET /api/agent/config` 查看脱敏后的 URL、模型和配置状态；完整 API Key 永远不会由接口返回。

## 当前阶段与角色 TODO

更新时间：2026-08-28。分支约定：`main` 只合可运行代码；角色对应：A=ly，B=dkd，C=czc，D=rxc。任务编号（A1/B1/C1/D1）保持不变。

| 阶段 | 目标 | 状态 | 说明 |
| --- | --- | --- | --- |
| 阶段一 基础功能 P0 | 注册登录、项目、邀请、任务、打卡、评价、看板 | 功能闭环完成，工程收尾 | 邀请接受、项目设置、任务编辑/删除和归档只读体验均已接入前端 |
| 阶段二 AI 功能 P1 | 推荐、负载、匹配度、风险、周报、Agent 对话 | 核心完成，联调收尾 | 推荐四维证据、排除原因、采纳留痕、风险、历史周报、Agent 会话/引用/工具轨迹均已接入真实页面 |
| 阶段三 贡献系统 P1 | 手动贡献 + 外部平台接入 | 部分完成 | 手动贡献/确认/争议已有；GitHub 等接入未做 |
| 阶段四 长期协作 P2 | 历史项目、班级成员池、履历、画像与跨项目授权 | 部分完成 | 历史项目、动态班级和同班成员履历已完成；画像计算与更细粒度授权仍待完善 |

本阶段已补齐动态班级成员池、项目临时队伍、项目成员软退出、任务多人参与、班级成员页面和同班成员跨项目协作履历接口。画像计算仍应建立在真实任务与贡献数据上，不自动生成未经确认的个人标签。

### 角色 TODO

**ly（A）后端底座 / 账号权限 / 数据库 / 部署**

- [x] A1-A6：拆分 routers/services、注册登录、权限、SQLAlchemy/Alembic、Docker、审计与限流
- [ ] 与 rxc 对齐生产 `.env`（CORS、Secure Cookie、LLM）后再做正式部署验证

**dkd（B）核心业务后端**

- [x] B1-B5：项目、邀请、任务、打卡、质量评价
- [x] B6：手动贡献账本（确认/争议）
- [ ] B7：历史项目查询页面对齐；归档只读体验需 czc 配合

**czc（C）前端产品 / 交互**

- [x] C1：登录注册、项目创建/切换、总览、看板、打卡、评价、贡献、推荐、报告与 Agent 对话
- [x] C2：完成 `main.tsx`、功能目录拆分、集中 API 层、类型定义、显式 React Router 路由、TanStack Query 项目数据查询和 Vitest 测试工程；存量 JSX 可继续渐进迁移，不阻塞当前验收
- [x] C3：项目创建、详情、切换、编辑、归档/恢复和二次确认删除已完成；项目切换器可访问已归档项目
- [x] C4：成员角色、移除、邀请生成/撤销、邀请链接落地和登录后接受邀请已完成
- [x] C5：任务看板、创建、筛选排序、详情、字段编辑、权限删除、状态流转、日志/打卡/评价历史和评审人已完成
- [x] C6-C8：打卡保存后刷新、已完成任务可再次进入评价、评价权限/历史、贡献筛选与确认/争议均已形成闭环
- [x] C9：报告独立页面地址、周报历史与刷新、风险来源/严重度、Markdown/PDF 导出、Agent 会话/引用/工具轨迹；已清理无功能按钮
- [x] C10：前端视觉重构：数字账本视觉、Lucide 图标、MiSans/得意黑字体、桌面与 390px 窄屏适配
- [ ] C11：历史项目页面、班级成员页、同班成员履历查看已完成；仍待补充画像计算和更细粒度数据授权，不使用假数据

**rxc（D）AI / 数据分析 / 平台接入 / 质量（本轮）**

- [x] D1 加深：技能 40% / 质量 30% / 效率 20% / 负载 10%；仅 member 默认候选；超负载排除；无样本中性分 0.5；规则 + LLM 语义匹配/理由润色；四维证据；批量建议；采纳/手选留痕
- [x] D1 本轮：技能族匹配（后端/前端/文档等同义词，不再只靠字面子串）、候选人对比解释、推荐历史中文状态、可重复演示种子 `scripts/seed_stage2_demo.py`
- [x] D1 已接入真实 LLM：StepFun `step-3.7-flash`，语义匹配与理由润色已验证；无 `.env` 时仍自动回退规则路径
- [x] D1 深化二轮：LLM 理由注入任务描述+四维事实（数值化理由、低匹配候选指明方向）；前端 AI 降级提示（degrade-note）；推荐卡片结构化证据标签（技能族/样本数/负载/来源）；推荐历史页展示批量/单任务、来源、采纳人、改派对象
- [x] D2 深化：加权负载（`weighted_load` / `weighted_level` / `weighted_overdue_tasks`，权重可用环境变量覆盖）＋风险按 `severity` 降序（`critical_unassigned` 关键任务无人承接）＋LLM 风险总结（`summary`/`summary_source`，失败规则回退，`summarize=0` 可跳过）
- [x] D3 深化：`/weekly-report` LLM 增强 + 历史留痕（`weekly-reports` 表、`week_start`/`refresh`、`/weekly-report/history`）
- [x] D4 深化：Agent 多步推理循环（ReAct 简化版）＋六个只读工具＋`tool_trace`/`citations` 来源引用＋会话摘要压缩（失败自动规则兜底）
- [ ] D5：GitHub / 飞书 / 腾讯文档接入（等 dkd 的 B6 稳定后做，不阻塞阶段二演示）
- [ ] D6：长期画像（阶段四）
- [ ] D7：完整联调、端到端自动化与演示手册；当前后端 37 个 pytest、前端 3 个 Vitest 文件共 8 项测试均通过

### 下一步优先级

1. **P0 交付质量**：补完整演示数据、Playwright 端到端脚本、演示手册和生产部署验收。
2. **P1 前端维护**：继续将存量 JSX 渐进迁移为 TSX，并把更多业务请求改为声明式 Query/Mutation。
3. **P1 阶段三**：先接 GitHub OAuth、仓库绑定和手动同步，再扩展文档/会议平台；外部事件统一进入待确认贡献。
4. **P2 长期协作**：由 B7/D6 先补画像来源、跨项目聚合与授权接口，再完成 C10 剩余页面。

阶段二接口：

- `GET /api/projects/{id}/recommendations`
- `POST /api/projects/{id}/recommendations/batch`
- `GET /api/projects/{id}/recommendations/history`
- `POST /api/projects/{id}/recommendations/{rec_id}/decide`
- `GET /api/projects/{id}/members/load`
- `GET /api/projects/{id}/risks`
- `GET /api/projects/{id}/weekly-report`
- `GET /api/projects/{id}/weekly-report/history`
- `POST /api/projects/{id}/agent/chat`

本地验证：

```powershell
python -m pytest -q backend/test_stage2.py
python scripts/seed_stage2_demo.py  # 重置阶段二演示数据
$env:COLLAB_DB="$PWD\stage2-demo.db"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
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
