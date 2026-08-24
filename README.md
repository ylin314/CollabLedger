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

## 后端启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

项目根目录的 `.env` 是 Agent 的唯一配置入口。后端会自动读取它；Docker Compose 也会把同一组变量注入容器。`.env` 已加入 `.gitignore`，不会提交到 Git。

当前 `.env` 使用 OpenAI Chat Completions 兼容协议（不使用 Responses API）：

```dotenv
LLM_BASE_URL=https://aigw.saurlax.com/
LLM_API_KEY=你的APIKey
LLM_MODEL=deepseek-v4-flash
LLM_CHAT_COMPLETIONS_URL=
```

`LLM_CHAT_COMPLETIONS_URL` 留空时自动请求 `${LLM_BASE_URL}/v1/chat/completions`；也可以显式填写完整的 Chat Completions 地址。

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

前端在后端 API 不可用时会自动切换到内置演示数据，便于直接体验界面；恢复 API 后，创建任务、任务生命周期、贡献记录和推荐会写入 SQLite。

首次使用时，如果数据库中没有项目，打开系统会直接显示“创建你的第一个项目”页面。填写项目名称、类型、周期和组长信息后，系统会先创建用户，再创建真实项目，之后所有任务和 Agent 对话都会使用该项目，不再使用演示项目。

也可以通过 API 创建：

```powershell
$owner = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/users -ContentType 'application/json' -Body '{"name":"张三","skills":["Python","后端"],"status":"online"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/projects -ContentType 'application/json' -Body (@{name='软件工程课程大作业';project_type='课程项目';owner_id=$owner.id} | ConvertTo-Json)
```

隐私边界：本 MVP 只记录项目任务、主动填写的贡献和协作分析，不采集私人聊天、桌面、摄像头、键盘、鼠标或屏幕数据；推荐和 Agent 只提供可解释建议，不生成成员排名或“摸鱼”判断。

### Agent 四层结构

- `tool`：读取项目任务、成员负载、风险、报告，并调用规则推荐器。
- `memory`：按项目和会话保存 Agent 对话记忆，使用 SQLite 持久化。
- `plan`：先根据问题规划需要读取的工具，再执行工具，避免模型凭空猜测。
- `llm`：调用 OpenAI Chat Completions 兼容协议 `POST /v1/chat/completions`，不是 Responses API。

可通过 `GET /api/agent/config` 查看脱敏后的 URL、模型和配置状态；完整 API Key 永远不会由接口返回。
