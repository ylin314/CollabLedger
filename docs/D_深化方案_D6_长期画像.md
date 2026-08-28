# D6 长期画像 - 深化方案

> 面向开发会话（session / goal）的可执行方案文档。目标：把散落在任务、评价、打卡、贡献里的历史数据聚合为「成员长期画像」，并让 D1 推荐在没有当前项目样本时用画像兜底。不做评审人/导师观察者等复杂角色（B 领域）。
> 状态：已与负责人确认决策（见「决策记录」）。执行顺序：D5 → D6 → D7，本文件第二个执行。

## 1. 现状对照

### 1.1 文档要求（团队分工 3.12 长期画像、产品路线图阶段四）
- 历史项目、画像、跨项目授权；README：画像页不要用假数据。

### 1.2 当前实现
| 能力 | 现状 |
| --- | --- |
| 推荐 | D1 仅在「当前项目」内打匹配度，候选=member，无历史数据时中性分 0.5 |
| 成员资料 | users.skills（技能标签）、max_concurrent_tasks；memberships 记录项目与角色 |
| 历史数据 | tasks（quality/actual_hours/estimated_hours/status）、task_reviews（quality/comment）、work_logs（工时）、contributions（confirmed）已落库 |
| 画像 | 无任何聚合画像；无跨项目推荐；无「无样本」兜底 |

### 1.3 差距结论
- 成员只有「当前项目数据」，缺少跨项目历史资产视图。
- 新成员/新项目样本不足时，推荐无法利用其历史表现。

## 2. 决策记录（已确认）
| 项 | 决策 |
| --- | --- |
| 画像范围 | 技能画像 + 跨项目推荐；不做评审人/导师观察者 ✅ |
| 数据来源 | 只用现有真实数据（tasks / task_reviews / work_logs / contributions），不引外部 ✅ |
| 画像展示 | add 成员卡片/详情展示画像（雷达/条形），不造假数据；阶段四假画像页不提前做 ✅ |
| 跨项目推荐 | 当前项目样本不足时用画像兜底（`profile_source` 标记）✅ |
| 时间衰减 | 近期数据权重更高（近 90 天权重 1.0，更早按 0.5 衰减），避免陈旧画像 |
| 画像刷新 | 读取时按需聚合（不建常驻表），数据变化即新画像；如需缓存列 backlog |

## 3. 深化设计

### 3.1 画像维度（与 D1 四维对齐）
对每个成员计算 `profile`：
1. **技能画像**：从 `users.skills` + 历史任务 `task_type` 提取技能族（复用 D1 的 ONTO 技能族同义映射 `backend/services/recommend.py`）。
   - `skill_families`：出现次数 ≥2 的技能族。
   - `skill_strength`：按该技能族下任务完成率 × 平均质量加权。
2. **质量画像**：`average_quality` = 加权均值（task_reviews.quality / tasks.quality），样本数 `quality_samples`。
3. **效率画像**：`average_efficiency` = 历史任务 Σactual_hours ÷ Σestimated_hours（<1 表示比预估快，>1 慢）；样本 `efficiency_samples`。
4. **历史贡献**：`contributions_total`（confirmed 计数）、`projects_count`（参与项目数）、`active_months`。

### 3.2 计算位置
- 新增 `backend/services/profile.py`：`build_profile(user_id)` 纯函数聚合，不直接访问 Request。
- 新增接口：
  - `GET /api/users/{user_id}/profile` → `{user_id, name, skill_families, skill_strength, average_quality, quality_samples, average_efficiency, efficiency_samples, contributions_total, projects_count, active_months, updated_at}`。
  - 鉴权：仅本人或同项目成员可查看（`ensure_project_access` 或 owner 全局）。
- 推荐接入：`backend/services/recommend.py` 候选打分时，若当前项目样本不足（如 quality/efficiency 样本 <2），用 `build_profile` 的画像值替代中性分，并在响应 `recommendations[].profile_source` 标记 `historical`；否则 `current`。

### 3.3 前端
- 成员卡片（Overview `MemberCard`）增加「画像」入口，点击弹出成员画像面板：
  - 技能族 chips（带强度进度条）、质量 / 效率 / 历史项目 / 总贡献数值。
  - 明确文案「基于历史项目聚合，仅本组成员可见」；无数据时显示「暂无历史画像」。
- 推荐卡片若 `profile_source=profile`，在原因区标注「参考历史画像」。

### 3.4 数据一致性
- 画像只读聚合，不写缓存表 → 无过期问题、无脏数据。
- 隐私：画像只含项目内协作事实（任务/评价/工时/贡献），不采集个人设备/聊天数据（对齐 AGENTS.md 隐私边界）。

## 4. 测试
- `backend/test/test_profile.py`（mock DB 种子）：
  - build_profile 对无历史成员返回空画像（不抛错）
  - 有 samples 时 average_quality / efficiency 计算正确；时间衰减生效
  - 推荐样本不足时走 profile_source=profile，样本足时为 current
  - 权限：他人 profile 非项目成员返回 403
- 保持后端测试全绿 + 前端 build 通过。

## 5. 验收标准
- [ ] `GET /api/users/{id}/profile` 返回 8 个画像字段，数据与 tasks/reviews/work_logs/contributions 一致
- [ ] 无历史数据成员显示空画像而非假数据
- [ ] 推荐在样本不足时引用画像兜底，响应带 profile_source 标记
- [ ] 前端成员卡片可打开画像详情，推荐卡片标注画像来源
- [ ] `pytest backend/` 全绿（含新增 test_profile.py）
- [ ] `cd frontend && npm run build` 通过
- [ ] README 阶段四 TODO 更新：画像已实现，跨项目授权仍为未实现
