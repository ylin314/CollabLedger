# D7 收尾与演示 - 深化方案

> 面向开发会话（session / goal）的可执行方案文档。目标：把 D1-D6 的成果收口为「可验收、可演示、可部署」的完整交付：补齐核心测试、接入 GitHub Actions CI、编写 15 分钟演示手册、与 A 对齐生产部署。执行顺序：D5 → D6 → D7，本文件最后一个执行。
> 状态：已与负责人确认决策（见「决策记录」）。

## 1. 现状对照

### 1.1 文档要求（团队分工 D7 质量 / 联调 / 演示、README 角色 TODO）
- README 角色 TODO D7 条目：「完整联调与演示手册；当前仅保留少量核心测试」。
- AGENTS.md 规则 23：核心链路落地前不大量写测试；D7 是核心链路已贯通后的收尾阶段，补齐测试经负责人明确批准。

### 1.2 当前实现
| 能力 | 现状 |
| --- | --- |
| 测试 | 36 个用例（backend/test/ 下 9 个文件），覆盖核心链路但接口级、AI 边界、安全覆盖不足 |
| CI | 无 `.github/workflows`，无任何自动化检查 |
| 演示 | 无可执行演示手册；有 `scripts/seed_stage2_demo.py` 可重复种子 |
| 部署 | `Dockerfile` + `compose.yaml` / `compose.postgres.yaml` / `compose.https.yaml` 已存在；生产 `.env` 未与 A 对齐验证 |

### 1.3 差距结论
- 测试只覆盖核心链路，逐接口、AI 模块边界、安全用例不足，多人协作下回归风险高。
- 无 CI：PR 合入前无自动验证。
- 无演示手册：评审/答辩时无法快速走查完整流程。
- 生产部署未最终验证：与 A 的 `.env` 对齐、Docker 一键启动未做。

## 2. 决策记录（已确认）
| 项 | 决策 |
| --- | --- |
| 测试目标 | 补到约 80-120 用例：核心路由每接口 1-2 例 + AI 边界 + 安全；不追求覆盖率数字 ✅ |
| CI | GitHub Actions：`python -m pytest backend/` + `cd frontend && npm ci && npm run build`；PR + main 触发 ✅ |
| 演示手册 | 新增 `docs/演示手册.md`：seed → 启动 → 15 分钟全流程走查 + 真实截图占位 ✅ |
| 部署校验 | 与 A（ly）对齐生产 `.env`（CORS / Secure Cookie / LLM）后做 Docker 一键验证 ✅ |
| 测试框架 | 沿用 pytest + FastAPI TestClient，不引入新框架 ✅ |

## 3. 深化设计

### 3.1 测试补齐策略（目标约 80-120 用例）
按「先核心后外围、每接口成对覆盖」补齐，不过度追求覆盖率数字：

| 测试文件 | 覆盖内容 | 用例数（约） |
| --- | --- | --- |
| 既有 9 文件（36 例） | 核心链路保持全绿 | 36 |
| test_api.py 扩充 | 注册/登录/项目/邀请/任务/打卡/评价/贡献，每接口 1 成功 + 1 失败/边界 | +22 |
| test_agent.py 扩充 | 工具白名单、多步循环、来源引用、摘要压缩失败回退 | +10 |
| test_security.py 扩充 | 未登录 401、越权 403、token 脱敏、限流 | +8 |
| test_integrations_github.py（D5 新增） | OAuth state 校验、同步、去重、GitHub API 失败回退 | +8 |
| test_profile.py（D6 新增） | 画像聚合、时间衰减、推荐 profile_source 兜底、权限 403 | +8 |

- 原则：每个接口至少 1 个成功用例 + 1 个失败/边界用例；AI 模块只测边界与回退，不 mock 真实 LLM 长链路。
- 不写前端 UI 端到端测试（用演示手册人工走查代替，降低维护成本）。

### 3.2 CI（.github/workflows/ci.yml）
- 触发：`pull_request` + push 到 `main`。
- 后端 job：`actions/checkout` → `setup-python@v5`（3.11）→ `pip install -r requirements.txt` → `python -m pytest backend/ -q`。
- 前端 job：`setup-node@v4`（20）→ `npm ci` → `npm run build`。
- 缓存：pip / npm 缓存加速。
- 门槛：PR 必须 CI 全绿才可合并；D5/D6/D7 各自 PR 都带此 CI。

### 3.3 演示手册（docs/演示手册.md）
1. 准备：`python scripts/seed_stage2_demo.py` 生成种子数据；`.env` 说明（LLM 可选，无 key 自动回退规则路径）。
2. 启动：后端 `uvicorn backend.main:app --reload --port 8000` + 前端 `npm run dev`（或 `docker compose up -d --build` 一键）。
3. 15 分钟走查脚本（每步含操作 + 预期结果 + 截图占位）：
   - 0-2 分钟：登录/注册、进入项目空间、看板与任务
   - 2-5 分钟：打卡、评价、贡献账本（手动贡献确认/争议）
   - 5-8 分钟：推荐（匹配度/证据标签/降级提示）、负载与风险（加权与排序）
   - 8-11 分钟：周报生成与历史回看、Agent 多步对话（来源引用）
   - 11-13 分钟：GitHub 连接 → 同步 → pending → confirmed（来源徽标 + evidence 链接）
   - 13-15 分钟：成员画像、总结页
4. 截图占位：步骤标注「截图：<文件名>.png」，演示前用真实截图替换；禁止假数据。

### 3.4 部署校验（与 A 对齐）
- 与 A（ly）确认生产 `.env`：`CORS_ORIGINS`、`COOKIE_SECURE`、`LLM_API_KEY`、数据库连接。
- 验证：`docker compose up -d --build` → 健康检查 → 前端可访问 → 核心链路冒烟。
- 若 A 未就绪，在 README 与本文档中记录「待 A 确认」项，不阻塞 D5/D6 合并。

## 4. 测试（D7 自身的验证）
- `python -m pytest backend/ -q` 全绿，用例数落在 80-120 区间。
- `cd frontend && npm run build` 通过。
- CI YAML 语法与触发路径核对（推送后看 Actions 结果）。
- 演示手册按 15 分钟脚本实际走查一遍，补齐截图。

## 5. 验收标准
- [ ] 测试用例数达到约 80-120，`pytest backend/` 全绿
- [ ] `.github/workflows/ci.yml` 生效：PR 与 main push 自动跑后端测试 + 前端构建
- [ ] `docs/演示手册.md` 存在且可按 15 分钟脚本走查完整流程
- [ ] 与 A 对齐生产 `.env` 并完成 Docker 一键启动验证（或明确记录待 A 确认项）
- [ ] README 角色 TODO 更新：D5/D6/D7 全部勾选，阶段三/四状态同步
- [ ] 三份方案文档（D5/D6/D7）已提交 dev_D，逐个发 PR 合并