# D6 长期画像与跨项目协作 - 深化方案与完成记录

> 适用日期：2026-08-30。执行顺序：D5 → D6 → D7。D6 已完成代码整改，浏览器、重启、PostgreSQL 和容器证据由 D7 收口。

## 1. 已确认产品决策

| 决策项 | 结论 |
|---|---|
| 授权层级 | 全局开关 + 项目级覆盖，不做第三档用途级 UI |
| 默认值 | 同团队跨项目分析默认启用 |
| 关闭语义 | 默认冻结：停止使用但保留真实团队数据 |
| 删除语义 | 可选彻底删除画像授权/派生数据；不级联删除同一真实项目的任务、贡献、工时 |
| API 契约 | API 契约字段为对外标准；深化算法字段附加返回 |
| 技能证据 | users.skills 只作自报技能/冷启动，不冒充历史完成证据 |
| 贡献口径 | 只统计 confirmed；pending/disputed 不进入画像和活跃月份 |
| 隐私边界 | 不读取 Agent 对话、桌面、聊天或设备数据；不输出人格/道德评价和公开排名 |

## 2. 画像模型

### 2.1 来源范围

- 本人查看：其 active/left membership 对应且未删除的项目。
- 他人查看：双方必须仍在至少一个未删除项目中 active；聚合数据只来自目标用户当前授权的来源项目。
- D1 项目内推荐：当前项目事实由 D1 原有链路读取；历史画像 fallback 只读取用户授权的来源项目。

### 2.2 对外字段

`GET /api/users/me/profile` 与 `GET /api/users/{user_id}/profile` 返回：

- 契约字段：`project_count`、`completed_task_count`、`average_quality`、`efficiency`、`on_time_rate`、`top_skills`、`collaboration_types`、`data_sources`、`generated_at`。
- 附加计算字段：`projects_count`、`quality_samples`、`average_efficiency`、`efficiency_samples`、`on_time_samples`、`skill_families`、`skill_strength`、`contributions_total`、`active_months`、`declared_skills`、`source_projects`、`calculation_notes`、`updated_at`。

### 2.3 算法口径

1. 技能强度：任务标题/类型命中技能族；完成率乘质量分。自报技能仅在 `declared_skills` 和冷启动推荐出现。
2. 质量：任务 review/quality 加权均值，近 90 天权重 1.0、更早 0.5。
3. 效率：已完成任务的衰减加权 `Σactual_hours / Σestimated_hours`；不与 work_logs 混加。
4. 准时率：只统计设置截止日期的已完成任务，优先使用 complete task_log 时间。
5. 贡献类型：只统计 confirmed 且未删除贡献，按 kind 返回 count/ratio。
6. 活跃月份：任务、confirmed 贡献、work_logs 的月份去重。

## 3. 授权与删除

接口：

- `GET/PATCH /api/users/me/authorizations`
- `DELETE /api/users/me/profile-data`

项目覆盖的值：

- `true`：该项目允许。
- `false`：该项目关闭。
- `null`：删除覆盖，重新跟随全局。

关闭全局且没有允许覆盖时为 `frozen`；删除后为 `deleted`。任一项目重新允许或全局重新启用时恢复 `retained`。授权读取不隐式写库，写入只发生在用户明确 PATCH/DELETE 时。

## 4. 跨项目合作关系

`GET /api/users/me/collaborations` 只统计：

- 双方均曾参与（active/left）的同一未删除项目；
- 双方都允许该项目用于分析；
- 共同任务要求双方均为负责人或任务参与者。

合作分：

$$
score = \min(100, 30 \times shared\_project\_count + 5 \times \min(shared\_task\_count, 10))
$$

响应返回来源项目与公式，不生成“可靠、勤奋、拖延”等推测性标签。

## 5. 长期任务方向

`GET /api/users/me/recommendations`：

- 有授权历史任务时，从真实技能强度、质量和效率生成方向，返回 `sample_count/data_sources/source_project_ids`。
- 无历史时，仅从自报技能生成固定 50 分冷启动项，`cold_start=true`、`sample_count=0`。
- frozen/deleted 返回空数组和明确说明。

该接口是个人方向建议，不修改项目任务、负责人、评审人或导师。

## 6. 前端

独立侧栏入口“我的长期画像”，提供：

- 契约指标、算法字段、数据来源与计算口径；
- 全局开关和逐项目覆盖；
- 冻结说明与彻底删除二次确认；
- 双边授权合作关系；
- 历史/冷启动长期方向；
- 无数据诚实空态。

成员卡片仍可查看同项目成员画像；退出项目后后端返回 403。390px CSS 已提供单列布局，真实浏览器行为留 D7。

## 7. 实现位置

- 聚合：`backend/services/profile.py`
- 授权：`backend/services/profile_authorization.py`
- 合作/方向：`backend/services/collaboration_profile.py`
- 路由：`backend/routers/auth_users.py`
- 前端：`frontend/src/features/profile/ProfileModal.jsx`、`frontend/src/App.jsx`
- 测试：`backend/test/test_profile.py`、`backend/test/test_d1_authorization.py`、`backend/test/test_d6_collaboration.py`、`frontend/src/features/profile/ProfileModal.test.tsx`

## 8. 验收状态

- [x] API 契约字段与深化字段兼容。
- [x] confirmed / pending / disputed 口径正确。
- [x] 自报技能不冒充历史完成证据。
- [x] 全局开关、项目覆盖、冻结和删除。
- [x] 双边授权合作关系。
- [x] 历史方向与诚实冷启动。
- [x] 退出项目后他人画像 403。
- [x] 后端 D6/D1/profile 专项与全量测试通过。
- [x] 前端组件测试、typecheck、build 通过。
- [ ] D7：SQLite 重启、旧库、PostgreSQL、Docker、桌面/390px 浏览器验收。