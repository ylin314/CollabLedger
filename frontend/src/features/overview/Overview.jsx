import {
  ArrowRight,
  CalendarDays,
  BellRing,
  Plus,
  TrendingUp,
  Users,
} from "lucide-react";
import { useState } from "react";
import { formatDate, greetingStamp, greetingTitle } from "../../shared/core";
import {
  MemberCard,
  Metric,
  PageTitle,
  TaskRow,
} from "../../shared/components";
import { ProfileModal } from "../profile/ProfileModal";

function Overview({
  auth,
  project,
  memberStats,
  tasks,
  progress,
  completed,
  overdue,
  activeTasks,
  risks,
  weekly,
  memberLoad,
  diagnostics,
  onNavigate,
  onAction,
  onRecommend,
  canWrite,
  canManageTask,
}) {
  const [profileMember, setProfileMember] = useState(null);
  const riskError = diagnostics?.risksError || "";
  const loadError = diagnostics?.memberLoadError || "";
  const riskCount = risks?.count ?? (risks?.risks || []).length;
  const highLoad = (memberLoad?.members || []).filter(
    (item) => item.weighted_level === "high",
  );
  const highLoadNames = highLoad.map((item) => item.name).join("、");
  const reminderTitle = riskError || loadError
    ? "风险或负载数据加载失败"
    : highLoad.length
      ? `${highLoadNames}的加权负载偏高`
      : riskCount
        ? `${riskCount} 件事需要确认`
        : "本周进度正常";
  const reminderCopy = riskError || loadError
    ? "当前无法判断项目是否安全，请刷新后重试，不能把接口故障当作暂无风险。"
    : highLoad.length
      ? "先确认现有任务的优先级，再决定是否继续安排新任务。"
      : riskCount
        ? "有任务的负责人或截止时间需要确认。"
        : "目前没有需要特别处理的事项。";
  const reminderSummary = riskError || loadError
    ? "数据不可用"
    : riskCount
      ? `${riskCount} 项待确认${highLoad.length ? `，${highLoadNames}暂不适合接新任务` : ""}`
      : "暂无待处理事项";
  const weeklyHint = weekly
    ? `本周完成 ${weekly.summary?.tasks_completed || 0} 项，打卡 ${weekly.summary?.checkin_count || 0} 次`
    : "周报数据加载中";
  return (
    <>
      <PageTitle
        eyebrow={greetingStamp()}
        title={greetingTitle(auth?.name)}
        action={
          canWrite ? (
            <button
              className="primary-button"
              onClick={() => onNavigate("tasks")}
            >
              <Plus aria-hidden="true" /> 新建任务
            </button>
          ) : null
        }
      />
      {(riskError || loadError) && (
        <div className="form-error">
          {riskError && <span>风险数据加载失败：{riskError}</span>}
          {riskError && loadError && "；"}
          {loadError && <span>负载数据加载失败：{loadError}</span>}
        </div>
      )}
      <div className="overview-grid">
        <section className="hero-card">
          <div className="hero-copy">
            <span className="live-pill">
              <i /> 项目进行中
            </span>
            <h2>{project.name}</h2>
            <p>{project.description || "让每一份协作成果都被看见。"}</p>
            <div className="hero-meta">
              <span>
                <CalendarDays aria-hidden="true" /> {formatDate(project.start_date)} —{" "}
                {formatDate(project.end_date)}
              </span>
              <span><Users aria-hidden="true" /> {memberStats.length} 位成员</span>
            </div>
          </div>
          <div
            className="progress-ring"
            style={{ "--progress": `${progress * 3.6}deg` }}
          >
            <div>
              <strong>{progress}%</strong>
              <span>项目进度</span>
            </div>
          </div>
        </section>
        <div className="metric-row">
          <Metric
            label="进行中任务"
            value={activeTasks.length}
            hint={weeklyHint}
            trend="neutral"
            color="amber"
          />
          <Metric
            label="已完成"
            value={completed}
            hint={`共 ${tasks.length} 项任务`}
            trend="neutral"
            color="green"
          />
          <Metric
            label="需要关注"
            value={riskError ? "—" : riskCount}
            hint={riskError ? "风险接口不可用" : riskCount ? reminderCopy : "目前无需处理"}
            trend={riskCount ? "down" : "neutral"}
            color="red"
          />
        </div>
      </div>
      <div className="section-heading">
        <div>
          <h2>协作状态</h2>
        </div>
        <button
          className="text-button"
          onClick={() => onNavigate("contributions")}
        >
          查看贡献账本 <ArrowRight aria-hidden="true" />
        </button>
      </div>
      <div className="member-grid">
        {memberStats.map((m, i) => (
          <MemberCard
            key={m.id}
            member={m}
            index={i}
            onProfile={(member) => setProfileMember({ ...member, id: member.user_id || member.id })}
          />
        ))}
      </div>
      <div className="dashboard-columns">
        <section className="panel task-panel">
          <div className="panel-header">
            <div>
              <h2>需要你关注的任务</h2>
            </div>
            <button className="text-button" onClick={() => onNavigate("tasks")}>
              查看全部 <ArrowRight aria-hidden="true" />
            </button>
          </div>
          <div className="task-list">
            {tasks
              .filter((t) => t.status !== "completed")
              .slice(0, 4)
              .map((t) => (
                <TaskRow
                  key={t.id}
                  task={t}
                  onAction={onAction}
                  onRecommend={canWrite ? onRecommend : null}
                  canManageTask={canManageTask}
                />
              ))}
          </div>
        </section>
        <section className="panel insight-panel">
          <div className="panel-header">
            <div>
              <h2>本周提醒</h2>
            </div>
            <span className="sparkle"><BellRing aria-hidden="true" /></span>
          </div>
          <div className="insight-main">
            <div className="insight-icon"><TrendingUp aria-hidden="true" /></div>
            <div>
              <strong>{reminderTitle}</strong>
              <p>{reminderCopy}</p>
            </div>
          </div>
          <div className="insight-line">
            <span>待处理</span>
            <strong>{reminderSummary}</strong>
            <button
              className="agent-action-button"
              onClick={() => onNavigate("agent")}
            >
              交给 Agent 分析
            </button>
          </div>
        </section>
      </div>
      {profileMember && (
        <ProfileModal
          user={profileMember}
          onClose={() => setProfileMember(null)}
        />
      )}
    </>
  );
}

export { Overview };
