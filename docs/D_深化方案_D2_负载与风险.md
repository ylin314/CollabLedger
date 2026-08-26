# D2 成员负载与风险分析 - 深化方案

> 面向开发会话(session / goal)的可执行方案文档。目标:把当前"纯任务计数负载 + 规则罗列风险"深化为「加权负载 + 可排序风险 + LLM 风险总结 + 失败回退」的完整能力。
> 状态:已与负责人确认决策(见「决策记录」)。开发顺序:D3 → D4 → D2,本文件是第三个执行。

## 1. 现状对照

### 1.1 文档要求(团队分工 D2 / 产品路线图阶段二)
- 接口:`GET /api/projects/{project_id}/members/load`、`GET /api/projects/{project_id}/risks`。
- 验收:负载统计与任务状态一致;风险列表包含任务、状态、截止时间;风险判断规则可解释;覆盖延期任务、临近截止任务、无负责人任务、关键任务无人承接。

### 1.2 当前实现(backend/services/analytics.py)
| 能力 | 现状 |
| --- | --- |
| 负载 | 纯计数:进行中/已分配/暂停/延期任务数 ÷ 最大并发数;`load_ratio`、`load_level`(low/normal/high) |
| 风险 | 规则四类:延期、临近截止(3 天)、无负责人、高负载;另有"7 天无活动" |
| 可解释 | 每条带 `rule` 说明 |
| 排序 | 无显式严重度排序,按任务扫描顺序 |
| LLM | 无总结,无归因 |

### 1.3 差距结论
- **负载加权**:只按任务数,未反映"延期任务更重、已分配较轻、工时占比"。
- **风险排序**:未按严重度排序输出。
- **风险总结**:无 LLM 自然语言归因与下步建议。

## 2. 决策记录(已确认)
| 项 | 决策 |
| --- | --- |
| 负载口径 | 升级为加权负载;默认权重兼容原计数,避免前端误解 ✅ |
| 负载趋势 | 本轮不做(留待 D6 长期画像) ✅ |
| 风险总结 | 增加 LLM 自然语言总结 + 按严重度排序 ✅ |
| 风险排序 | LLM 失败回退规则排序(高风险在前) ✅ |

## 3. 深化设计

### 3.1 加权负载(internal_member_load)
字段保持向后兼容,新增加权维度,不破坏 `current_task_count` 语义:

```text
任务状态权重(默认):
  in_progress = 1.0
  paused      = 0.5
  assigned    = 0.6
  overdue     = 1.3      # 延期任务负担最重
权重可经环境变量覆盖:LOAD_WEIGHT_IN_PROGRESS / LOAD_WEIGHT_PAUSED / LOAD_WEIGHT_ASSIGNED / LOAD_WEIGHT_OVERDUE
```

- 加权负载 = Σ(状态权重) ÷ max_concurrent_tasks。
- 新增响应字段:
  - `weighted_load`:加权值(round 2)
  - `weighted_level`:按加权比计算 low/medium/high(沿用阈值 <0.5 / ≤0.8 / >0.8)
  - `weighted_overdue_tasks`:该成员延期任务数(归类展示)
  - `rule` 内注明权重说明
- 保留原 `load_ratio` / `load_level`(基于任务数值)为避免前端回归;`overloaded` 仍以 `current_task_count >= max_concurrent_tasks` 为准。

### 3.2 风险严重度排序(internal_project_risks)
- 为每条风险新增 `severity` 字段(整数,0-100),覆盖原 `level` 字符串:
  - overdue_task = 90
  - upcoming_deadline = 70
  - unassigned_task = 60
  - high_member_load = 65
  - no_recent_activity = 30
- 输出时按 `severity` 降序排序,`risks[0]` 即最该关注项。
- 保留 `level`(high/medium/low)不变;`rule` 说明保留。
- 前端已有字段不受影响,新增字段为增量。

### 3.3 LLM 风险总结
复用 `recommend.llm_json`,在 `/risks` 响应中增加 `summary` 字段:

```json
{
  "project_id": 1,
  "generated_at": "...",
  "count": 3,
  "summary": "当前最需要关注:任务X已延期、任务Y无人接。建议优先处理延期调度与指派。",
  "summary_source": "llm" | "rule",
  "risks": [ { "type":"overdue_task", "severity": 90, "level":"high", "message":"...", "rule":"..." } ],
  "rule": "覆盖延期、临近截止、无负责人和高负载四类风险;按严重度排序"
}
```

- `summary` 由 LLM 基于 `risks` 生成(注入事实,禁止编造、不点名批斗)。
- LLM 失败/未配置 → `summary_source="rule"`,回退为规则拼接文本(如:"当前共有 N 个风险,优先关注 …"),接口不失败。
- 增加 `GET /risks?summarize=0` 可选参数跳过 LLM(性能/批量场景),默认 `summarize=1`(仅当存在风险)。

### 3.4 关键任务无人承接
在 `internal_project_risks` 中补充该场景:高优先级(`priority='high'`)且 `status='unassigned'` 的任务,除 `unassigned_task` 外增加 `critical_unassigned`(severity=95),让"关键任务无人承接"成为独立高阶风险。

## 4. 涉及文件(实施顺序)
1. `backend/services/analytics.py`:加权负载、severity、排序、LLM summary。
2. `backend/routers/analytics.py`:`/risks` 增加 `summarize` 查询参数;响应透传。
3. `backend/services/recommend.py`(或直接 import `llm_json`):复用 JSON 提取,不复制实现。
4. `backend/schemas.py`:如新增请求模型(建议用 query param,无需 schema)。
5. `docs/API接口契约.md` / `README.md`:补充 D2 新字段。
6. `backend/test/test_stage2.py`(或新增聚焦测试):加权负载/排序/回退最少覆盖。

> 若 `weights` 常量化时注意与 `recommend.py` 的负载因子保持一致(推荐接口优先使用加权负载)。

## 5. 验收标准(映射 D2 文档验收 + 深化)
- `/members/load` 返回保留 load_ratio/load_level,并新增 weighted_load / weighted_level / weighted_overdue_tasks。
- `/risks` 返回按 severity 降序;含"关键任务无人承接"如存在。
- 配置 LLM 时 `/risks?summarize=1` 返回 `summary`+`summary_source="llm"`;不配置或消费失败 `summary_source="rule"` 且接口不报错。
- 权重可被环境变量覆盖;默认权重与文档一致。
- 现有前端调用不破坏(字段向后兼容)。
- 新增最少 2 个聚焦测试:加权负载、风险排序/回退。

## 6. 本地验证命令
```powershell
python scripts/seed_stage2_demo.py
$env:COLLAB_DB = "$PWD\stage2-demo.db"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 负载(含 weighted_load 字段)
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/members/load'
# 风险(按严重度排序,含 summary)
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/risks'
# 跳过 LLM 总结
Invoke-RestMethod 'http://127.0.0.1:8000/api/projects/1/risks?summarize=0'
```

## 7. 完成标志
- [ ] `weighted_load` / `weighted_level` / 延期任务计数字段实现并验证。
- [ ] 风险按 severity 降序,`status` / `due_date` / 负责人字段在风险项中可见。
- [ ] LLM summary 上线,规则回退可用,失败不报错。
- [ ] 权重环境变量生效。
- [ ] README 与 API 文档同步。
- [ ] 提交:中文 commit 前缀 `feat:`,例如 `feat:D2负载加权与风险严重度排序`。