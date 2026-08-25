# Repository Guidelines

本指南面向 CollabLedger（协作账本）仓库的贡献者：面向小组作业的智能协作与贡献管理系统 MVP。

## Project Structure & Module Organization

- `backend/`：FastAPI 后端源码。`main.py` 为应用入口；`agent/` 为四层协作 Agent（`tools.py` 工具层、`memory.py` 记忆层、`plan.py` 规划层、`llm.py` 模型调用层）。
- `frontend/`：Vite + React 前端。入口为 `src/main.jsx`，开发服务器将 `/api` 代理到后端 8000 端口。
- `docs/`：中文产品文档（项目书、路线图、赛题原文）。
- 根目录：`Dockerfile`、`compose.yaml`（一键部署）、`requirements.txt`（后端依赖）、`.env.example`（配置模板）。

- 角色对应：A=ly（后端底座）、B=dkd（核心业务）、C=czc（前端）、D=rxc（AI/数据/接入/质量）；任务编号 A1/B1/C1/D1 保持不变。
- GitNexus 本地索引目录为 `.gitnexus/`，已忽略；需要时在仓库根执行 `gitnexus analyze . --index-only --name CollabLedger`。

## Build, Test, and Development Commands

```powershell
# 一键部署（推荐）
docker compose up -d --build

# 后端本地开发（项目根目录）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000

# 前端开发与生产构建
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173
npm run build    # 产物 frontend/dist，由后端自动托管

# 运行测试
pytest backend/
```

## Coding Style & Naming Conventions

- 后端：Python，遵循 PEP 8（4 空格缩进）；模块、函数、变量使用 `snake_case`。
- 前端：JavaScript/JSX，2 空格缩进；组件使用 `PascalCase`，普通变量使用 `camelCase`。
- 文档与用户可见文案一律使用中文；含中文的文件读写必须使用 UTF-8 无 BOM。
- 当前未配置 lint/format 工具，提交前请自行保持风格一致。

## Testing Guidelines

- 框架：`pytest` + FastAPI `TestClient`，测试文件位于 `backend/test_*.py`。
- 命名：测试函数以 `test_` 开头，描述单一行为（如 `test_core_flow`）。
- 运行：`pytest backend/`；测试应使用临时数据库（`tmp_path` + `monkeypatch`），不依赖真实 `.env` 或外部 LLM。

## Commit & Pull Request Guidelines

- 提交信息使用中文，采用 `<type>:<描述>` 前缀，如 `doc:新增路线图`、`feat:...`、`fix:...`。
- 每个提交保持原子性：一个功能或修复对应一个提交，便于 review 与回退。
- PR 需说明改动内容、关联 issue、验证方式（测试输出或截图）；涉及多人协作时先拉取远端最新代码再开发。
- 禁止提交 `.env`、`*.db`、`.gitnexus/`、`frontend/node_modules/`、`frontend/dist/`、Playwright 产物（已在 `.gitignore`）。

## Security & Configuration Tips

- Agent 配置唯一入口为根目录 `.env`（从 `.env.example` 复制），Docker 与本地后端共用同一组变量。
- LLM 使用 OpenAI Chat Completions 兼容协议；`LLM_API_KEY` 绝不提交到 Git，接口返回的配置信息必须脱敏。
- 隐私边界：只记录任务、贡献与协作分析，不采集聊天、桌面、摄像头等个人数据。