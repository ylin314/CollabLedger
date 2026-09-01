import { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { getJson, sendJson } from "../../api/client";
import { initials } from "../../shared/core";
import { PageTitle } from "../../shared/components";

function ReportView({ project, report, memberStats, tasks, weekly, risks, diagnostics }) {
  const [weeklyData, setWeeklyData] = useState(weekly);
  const [history, setHistory] = useState([]);
  const [exportFormat, setExportFormat] = useState("markdown");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const riskError = diagnostics?.risksError || "";
  useEffect(() => {
    setWeeklyData(weekly);
  }, [weekly]);
  useEffect(() => {
    getJson(`/api/projects/${project.id}/weekly-report/history`)
      .then((payload) => setHistory(payload.items || []))
      .catch((reason) => setError(reason.message));
  }, [project.id]);
  const rows =
    report?.members ||
    memberStats.map((m) => ({
      user_id: m.id,
      name: m.name,
      tasks_total: tasks.filter((t) => t.assignee_id === m.user_id).length,
      tasks_completed: m.done,
      tasks_overdue: tasks.filter(
        (t) =>
          t.assignee_id === m.user_id &&
          ["overdue", "unfinished"].includes(t.status),
      ).length,
      average_quality: m.quality === "—" ? null : Number(m.quality),
      actual_hours: tasks
        .filter((t) => t.assignee_id === m.user_id)
        .reduce((a, t) => a + (t.actual_hours || 0), 0),
    }));
  const total = report?.overall?.tasks_total ?? tasks.length;
  const done =
    report?.overall?.tasks_completed ??
    tasks.filter((t) => t.status === "completed").length;
  async function exportReport() {
    window.open(
      `/api/projects/${project.id}/report/export?format=${exportFormat}`,
      "_blank",
    );
  }
  async function refreshWeekly() {
    setRefreshing(true);
    setError("");
    try {
      const payload = await sendJson(`/api/projects/${project.id}/weekly-report`, {
        method: "POST",
      });
      setWeeklyData(payload);
      const historyPayload = await getJson(
        `/api/projects/${project.id}/weekly-report/history`,
      );
      setHistory(historyPayload.items || []);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setRefreshing(false);
    }
  }
  const currentWeekly = weeklyData || weekly;
  return (
    <>
      <PageTitle
        eyebrow="PROJECT REPORT"
        title="项目贡献报告"
        action={
          <div className="report-actions">
            <select
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value)}
              aria-label="报告导出格式"
            >
              <option value="markdown">Markdown</option>
            </select>
            <button
              className="ghost-button icon-text-button"
              onClick={exportReport}
            >
              <Download size={14} />
              导出报告
            </button>
          </div>
        }
      />
      {error && <div className="form-error report-error">{error}</div>}
      <div className="report-highlight">
        <div>
          <span className="eyebrow">PROJECT PULSE</span>
          <h2>小组整体进度</h2>
        </div>
        <div className="big-progress">
          <strong>{total ? Math.round((done / total) * 100) : 0}%</strong>
          <div>
            <div className="progress-track">
              <i style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
            </div>
            <span>整体完成度</span>
          </div>
        </div>
      </div>
      <div className="stage2-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>本周周报</h2>
              <p>
                {currentWeekly?.period
                  ? `${currentWeekly.period.start_date} 至 ${currentWeekly.period.end_date}`
                  : "基于真实任务、打卡和贡献"}
              </p>
            </div>
            <button
              className="icon-button-inline"
              title={currentWeekly?.exists ? "刷新本周周报" : "生成本周周报"}
              disabled={refreshing}
              onClick={refreshWeekly}
            >
              <RefreshCw size={14} />
              {refreshing ? "生成中" : currentWeekly?.exists ? "刷新周报" : "生成周报"}
            </button>
          </div>
          <ul className="fact-list">
            <li>完成 {currentWeekly?.summary?.tasks_completed ?? 0} 项</li>
            <li>进行中 {currentWeekly?.summary?.tasks_in_progress ?? 0} 项</li>
            <li>延期 {currentWeekly?.summary?.tasks_overdue ?? 0} 项</li>
            <li>确认贡献 {currentWeekly?.summary?.contribution_count ?? 0} 项</li>
            <li>待确认 {currentWeekly?.summary?.pending_contribution_count ?? 0} 项</li>
            <li>打卡工时 {currentWeekly?.summary?.checkin_hours ?? 0}h</li>
            <li>任务工时 {currentWeekly?.summary?.task_hours ?? 0}h</li>
            <li>有效工时 {currentWeekly?.summary?.actual_hours ?? 0}h（打卡优先）</li>
          </ul>
          {currentWeekly?.exists && currentWeekly?.source !== "llm" && (
            <div className="degrade-note weekly-degrade">
              <strong>⚠ 本报告为规则降级版，未接入 AI 分析</strong>
              <span>点击右上角「刷新周报」重新生成 AI 增强版（需要服务端已配置 LLM）。</span>
            </div>
          )}
          {currentWeekly?.insight_struct && (
            <div className="ai-insight-card">
              <div className="ai-insight-head">
                <strong>AI 分析</strong>
                <span className="source-label">AI 增强分析</span>
              </div>
              {currentWeekly.insight_struct.highlights?.length > 0 && (
                <div className="ai-insight-block">
                  <h4>本周亮点</h4>
                  <ul>
                    {currentWeekly.insight_struct.highlights.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}
              {currentWeekly.insight_struct.risks?.length > 0 && (
                <div className="ai-insight-block">
                  <h4>风险与归因</h4>
                  <ul>
                    {currentWeekly.insight_struct.risks.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}
              {currentWeekly.insight_struct.actions?.length > 0 && (
                <div className="ai-insight-block">
                  <h4>下步建议</h4>
                  <ul>
                    {currentWeekly.insight_struct.actions.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          {!currentWeekly?.insight_struct && currentWeekly?.insight && (
            <p className="weekly-insight">{currentWeekly.insight}</p>
          )}
          <p className="muted-note">
            {currentWeekly?.disclaimer || "周报不虚构事实。"} ·{" "}
            {currentWeekly?.source === "llm" ? "AI 增强" : "规则生成"}
          </p>
          <details className="weekly-history">
            <summary>历史周报（{history.length}）</summary>
            {history.map((item) => (
              <div className="weekly-history-row" key={item.id}>
                <strong>
                  {item.period_start} 至 {item.period_end}
                </strong>
                <span>
                  完成 {item.tasks_completed || 0} 项 · 打卡{" "}
                  {item.checkin_count || 0} 次 · 风险 {item.risks_count || 0} 条
                </span>
              </div>
            ))}
          </details>
        </section>
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>项目风险</h2>
              <p>
                {risks?.summary ||
                  risks?.rule ||
                  "延期、临近截止、无负责人和高负载"}
              </p>
            </div>
            <span className="source-label">
              {risks?.summary_source === "llm"
                ? "AI 总结"
                : risks?.llm_status === "failed"
                  ? "AI 失败，规则回退"
                  : "规则总结"}
            </span>
          </div>
          <div className="risk-list">
            {riskError ? (
              <div className="form-error">风险数据加载失败：{riskError}</div>
            ) : (risks?.risks || []).length ? (
              (risks.risks || []).map((item, index) => (
                <div
                  className={`risk-item ${item.level}`}
                  key={`${item.type}-${item.task_id || item.user_id || index}`}
                >
                  <strong>{item.message}</strong>
                  <span>
                    严重度 {item.severity ?? "—"} · {item.rule}
                    {item.weighted_load != null
                      ? ` · 加权负载 ${item.weighted_load}`
                      : ""}
                  </span>
                </div>
              ))
            ) : (
              <div className="empty-state">暂无明显风险</div>
            )}
          </div>
          {risks?.llm_status === "failed" && risks?.llm_error && (
            <p className="muted-note">AI 风险总结失败：{risks.llm_error}</p>
          )}
        </section>
      </div>
      <div className="report-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>成员贡献概览</h2>
              <p>不形成公开排名，仅用于项目复盘</p>
            </div>
            <span className="sparkle">✦</span>
          </div>
          <div className="report-table">
            <div className="report-row report-head">
              <span>成员</span>
              <span>完成任务</span>
              <span>延期</span>
              <span>平均质量</span>
              <span>实际耗时</span>
            </div>
            {rows.map((r) => (
              <div className="report-row" key={r.user_id}>
                <span className="assignee-inline">
                  <span className="tiny-avatar">{initials(r.name)}</span>
                  <strong>{r.name}</strong>
                </span>
                <span>
                  {r.tasks_completed} / {r.tasks_total}
                </span>
                <span className={r.tasks_overdue ? "danger-text" : ""}>
                  {r.tasks_overdue}
                </span>
                <span>
                  {r.average_quality ? (
                    <>
                      <b>{r.average_quality}</b>
                      <small> / 5</small>
                    </>
                  ) : (
                    "—"
                  )}
                </span>
                <span>{r.actual_hours || 0}h</span>
              </div>
            ))}
          </div>
        </section>
        <section className="panel report-insight">
          <div className="panel-header">
            <div>
              <h2>本周成员产出</h2>
              <p>来自任务完成、打卡和确认贡献</p>
            </div>
          </div>
          <div className="weekly-member-list">
            {(currentWeekly?.exists ? currentWeekly.members || [] : []).map((item) => (
              <div className="weekly-member-row" key={item.user_id}>
                <strong>{item.name}</strong>
                <span>
                  完成 {item.completed_tasks ?? 0} 项 · 确认贡献 {item.contribution_count ?? 0} 项 ·
                  待确认 {item.pending_contribution_count ?? 0} 项
                </span>
                <span>
                  打卡工时 {item.checkin_hours ?? 0}h · 任务工时 {item.task_hours ?? 0}h ·
                  有效工时 {item.actual_hours ?? 0}h（{item.hours_source === "checkin" ? "打卡优先" : "任务工时"}）
                </span>
                {item.summary && (
                  <p className="member-summary-line">
                    {item.summary}
                    <span className=
                      "source-label member-summary-source"
                    >
                      {item.summary_source === "llm" ? "AI" : "规则"}
                    </span>
                  </p>
                )}
              </div>
            ))}
            {!currentWeekly?.exists && (
              <div className="empty-state">尚未生成本周期周报，请点击“生成周报”。</div>
            )}
          </div>
          <p className="muted-note">
            画像与跨项目推荐属于阶段四，当前只展示本项目事实。
          </p>
        </section>
      </div>
    </>
  );
}

export { ReportView };



