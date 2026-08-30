# D3 周报自动生成 - 深化方案

> 面向开发会话(session / goal)的可执行方案文档。目标:把当前"规则版拼接周报"深化为「真实数据 + LLM 增强 + 历史留痕 + 失败回退」的完整功能。
> 状态:已与负责人确认决策(见「决策记录」)。开发顺序:D3 → D4 → D2。

## 1. 现状对照

### 1.1 文档要求(团队分工 D3 / 产品路线图阶段二)
- 汇总本周任务、打卡、贡献;汇总风险与建议。
- 接口:`GET /api/projects/{project_id}/weekly-report`。
- 验收:基于真实数据、不虚构事实;包含整体进度、成员产出、风险和建议;LLM 失败时可生成规则版周报。

### 1.2 当前实现(backend/services/analytics.py)
- `internal_weekly_report()`:纯 SQL 统计 + 规则拼接。
- `highlights` 取本周完成的前 5 个任务;`next_actions` 是 if-else 规则。
- 无 LLM 路径、无历史持久化、无回看能力、无逐成员自然语言摘要。

### 1.3 差距结论
- 缺少「LLM 增强路径」(文档要求 LLM 失败回退规则版,即默认应优先 LLM)。
- 缺少历史留痕与回看上周。
- 缺少逐成员可解释摘要与整体洞察。

## 2. 决策记录(已确认)
| 项 | 决策 |
| --- | --- |
| 历史周报 | 建表持久化,支持回看上周 ✅ |
| LLM 参与范围 | 逐成员摘要 + 整体洞察/建议 两段 ✅ |
| 失败回退 | 任一段 LLM 失败,该段回退规则文本 ✅ |
| Markdown 导出 | 保留现有 `format=markdown`,中文 PDF 本轮不做 ✅ |
| 数据边界 | 以 SQLite 为主要验收环境;PostgreSQL 同步补模型 ✅ |

## 3. 深化设计

### 3.1 数据表:weekly_reports
新增表,持久化每周生成的周报快照,支撑回看。

```sql
CREATE TABLE IF NOT EXISTS weekly_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  period_start TEXT NOT NULL,          -- YYYY-MM-DD(周一)
  period_end TEXT NOT NULL,            -- YYYY-MM-DD(周日)
  payload TEXT NOT NULL DEFAULT '{}',  -- 完整周报 JSON
  source TEXT NOT NULL DEFAULT 'rule', -- llm | rule | mixed
  llm_error TEXT,                       -- 记录 LLM 失败原因(可空)
  created_by INTEGER,                  -- 首次触发生成/刷新的用户
  created_at TEXT NOT NULL,
  updated_at TEXT,
  UNIQUE(project_id, period_start, period_end),
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
  FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);
```

兼容策略:
- **SQLite**:`backend/db.py` 当前 `SCHEMA_VERSION = 4`。本轮 D3 将其递增为 `5`,在 `SCHEMA_SQL` 尾部追加 `CREATE TABLE IF NOT EXISTS weekly_reports`。全新表无需 `_add_columns`,`initialize()` 幂等执行即可。
- **PostgreSQL**:`backend/models.py` 新增 `WeeklyReport` SQLAlchemy 模型(字段同上);由于现有 PG 初始化只保证既有表,本表通过 `Base.metadata.create_all` 自动创建;验收以 SQLite 为准,PG 仅保证模型可建。

### 3.2 接口
保留现有端点语义,增强回看与持久化:

```text
GET /api/projects/{project_id}/weekly-report
    ?week_start=YYYY-MM-DD     # 可选,默认本周周一;格式非法返回 422
    &format=json|markdown      # 默认 json
    &refresh=1                 # 可选;为 1 时强制重新生成并覆盖本周
```

- 首次访问某周:实时生成并**落库**(生成人=当前用户)。
- 再次访问同周:直接读库返回(除非 `refresh=1`)。
- `format=markdown` 沿用 `_weekly_markdown`,但改为读取 payload 中的 LLM 文本。

```text
GET /api/projects/{project_id}/weekly-report/history
    ?limit=20&before=YYYY-MM-DD   # 可选,按周期倒序
```

- 返回该项目的周报历史(不含大 payload,只含周期/摘要计数/source/生成时间)。
- 回看上周:调用 `GET /weekly-report?week_start=<上周周一>` 即可,命中已落库记录。

### 3.3 LLM 增强(两段)
复用 `backend/services/recommend.py` 的 `llm_json(prompt, timeout)` 与 `_extract_json`(已验证的同一调用链),在 `analytics.py` 新增两个内部函数:

1. `_llm_member_summaries(project_id, week, member_stats) -> (dict[user_id, str], source, err)`
   - 输入:本周成员统计(已完成数/进行中/延期/打卡/工时/贡献)。
   - 输出:每个成员 1 句中文产出摘要,只基于注入事实。
   - 失败:返回 `{source:"rule", err}` 由调用方决定。

2. `_llm_overall_insight(project_id, week, summary, risks) -> (str, source, err)`
   - 输出:整体进度一句 + 风险归因一句 + 下步建议一句,不含排名。
   - 失败:回退规则 `next_actions`。

**LLM 契约**:只返回 JSON,禁止编造;无成员评价/排名/人格标签;prompt 内注入本周真实统计与风险列表;用时 `LLM_TIMEOUT_SECONDS`(默认 45s)。

### 3.4 失败回退总策略
- 任一 LLM 段失败 → 该段 `source="rule"`,文本用当前规则版内容。
- 记录 `llm_error` 到 `weekly_reports.llm_error`,但**不阻塞接口**。
- 整体 `source` 取值:`llm`(两段都成功)/ `mixed`(部分成功)/ `rule`(未配置或全失败)。
- 无 `.env` 时自动回退规则(与 D1 一致)。

### 3.5 响应结构示例(json)
```json
{
  "project_id": 1,
  "period": {"start_date": "2026-08-24", "end_date": "2026-08-30", "week_start": "2026-08-24"},
  "summary": { "tasks_total": 10, "tasks_completed": 4, "tasks_in_progress": 2, "tasks_overdue": 1, "checkin_count": 8, "contribution_count": 3, "actual_hours": 18.5 },
  "highlights": [],
  "risks": [],
  "members": [{"user_id":1, "name":"张三", "completed_tasks":2, "checkin_count":3, "actual_hours":6, "summary":"本周完成 2 项任务...", "summary_source":"llm"}],
  "insight": "整体进度正常...", "insight_source":"llm",
  "next_actions": ["..."],
  "source": "llm",
  "disclaimer": "周报只汇总已有项目事实，不虚构完成情况。",
  "generated_at": "...",
  "stored": true
}
```

## 4. 涉及文件(实施顺序)
1. `backend/db.py`:SCHEMA_VERSION→5(基于 main 当前 `4`),追加表;若当前 dev_D 已是 5 则取宏。
2. `backend/models.py`:新增 `WeeklyReport` 模型。
3. `backend/services/analytics.py`:重构 `internal_weekly_report` 为 生成+落库;新增 `_llm_*`、`get_weekly_report(project_id, week_start, refresh)`、`list_weekly_reports(project_id, limit, before)`。
4. `backend/routers/analytics.py`:`weekly-report` 增加查询参数;新增 `weekly-report/history`。
5. `backend/schemas.py`:如需要请求模型(建议复用 query param,不必新增 schema;若加则 `WeeklyHistoryIn`).
6. `docs/DATABASE_SCHEMA.md`:补 weekly_reports 表。
7. `README.md`:D3 状态标注「已深化」并更新接口清单。

> 实施时以仓库最新为准:文件路径/函数名如有漂移,以实际代码为准,但保持上述对外契约不变。

## 5. 验收标准(映射 D3 文档验收 + 加分项)
- 首次访问本周生成并落库;再次访问命中库中的同一周期(`stored=true`),数据一致。
- 指定 `week_start` 回看上周:能读到上周落库记录;未生成过则实时生成。
- 配置 `LLM_API_KEY` 时生成 `source="llm"` 或 `mixed`;不配置时 `source="rule"` 且接口不报错。
- 成员摘要与整体洞察都基于真实统计,不含排行/人格标签/虚构数字。
- `refresh=1` 触发重新生成并更新该周期记录(不产生重复行)。
- 历史接口返回周期倒序,含 source/摘要/时间。
- `pytest backend/` 全量通过;新增针对性测试覆盖:首访落库、回看、refresh 覆盖、无 LLM 回退(见 6)。

## 6. 本地验证命令
```powershell
# 重置阶段二演示数据(会重建 demo 库)
python scripts/seed_stage2_demo.py

# 启动后端(设置 LLM 环境变量后 LLM 生效;不设置则走规则回退)
$env:COLLAB_DB = "$PWD\stage2-demo.db"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 查询本周：只读，不存在时返回 exists=false，不会落库
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/weekly-report'
# 周报页面明确点击生成/刷新后用 POST 落库；重复 POST 不产生重复行
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/weekly-report' -Method Post
# 回看上周：GET 只读；需要生成时由页面对同周期 POST
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/weekly-report?week_start=2026-08-17'
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/weekly-report?week_start=2026-08-17' -Method Post
# 历史
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/weekly-report/history'
# 强制刷新：周报页再次点击按钮，使用同一路径 POST
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/weekly-report' -Method Post
```

## 7. 完成标志
- [ ] `weekly_reports` 表在 SQLite 初始化自动创建,重复启动幂等。
- [ ] 上述接口全部实现并通过本地验证。
- [ ] 无 LLM 时全链路走规则回退不报错。
- [ ] 小于全量 pytest 的现有测试 + 新增最少 2 个聚焦测试通过(按项目「先核心后测试」策略,不铺开大量测试)。
- [ ] README 与 DATABASE_SCHEMA 已同步。
- [ ] 提交:中文 commit 前缀 `feat:`,例如 `feat:D3周报LLM增强与历史留痕`。