import { CalendarDays, Clock3, Plus, GitBranch } from "lucide-react";
import {
  avatarColors,
  dimLabel,
  formatDate,
  initials,
  sourceLabel,
  statusMeta,
} from "./core";

function PageTitle({ eyebrow = "", title, action = null }) {
  const showEyebrow = /[\u3400-\u9fff]/.test(eyebrow || "");
  return (
    <div className="page-title">
      <div>
        {showEyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
      </div>
      {action}
    </div>
  );
}

function Metric({ label, value, hint, trend, color }) {
  return (
    <div className={`metric-card ${color}`}>
      <div className="metric-top">
        <span>{label}</span>
        <span className={`trend ${trend}`}>
          {trend === "up" ? "↗" : trend === "down" ? "↘" : "—"}
        </span>
      </div>
      <strong>{value}</strong>
      <small>{hint}</small>
    </div>
  );
}

function MemberCard({ member, index, onProfile }) {
  const status = {
    online: ["协作中", "online"],
    busy: ["专注中", "busy"],
    away: ["暂离", "away"],
    offline: ["离线", "offline"],
  }[member.status] || ["协作中", "online"];
  const loadLevel =
    member.load_level ||
    (member.current >= member.max_concurrent_tasks
      ? "high"
      : member.current / member.max_concurrent_tasks > 0.5
        ? "normal"
        : "low");
  return (
    <div className="member-card">
      <div className="member-head">
        <div
          className={`avatar avatar-${index % avatarColors.length}`}
          style={{ background: avatarColors[index % avatarColors.length] }}
        >
          {member.avatar_url ? <img className="avatar-image" src={member.avatar_url} alt="" /> : initials(member.name)}
        </div>
        <div>
          <strong>{member.name}</strong>
          <span className={`status ${status[1]}`}>
            <i />
            {status[0]}
          </span>
        </div>
        <span className={`load-pill ${loadLevel}`}>
          {member.load_label ||
            { low: "低负载", normal: "正常", high: "高负载" }[loadLevel]}
        </span>
      </div>
      <div className="member-skills">
        {(member.skills || []).slice(0, 3).map((s) => (
          <span key={s}>{s}</span>
        ))}
      </div>
      <div className="member-foot">
        <span>
          当前任务{" "}
          <b>
            {member.current}/{member.max_concurrent_tasks}
          </b>
        </span>
        <span>
          本周完成 <b>{member.done}</b>
        </span>
        {onProfile && (
          <button className="profile-link" onClick={() => onProfile(member)}>
            画像
          </button>
        )}
        {member.github_username && (
          <a className="github-link" href={`https://github.com/${encodeURIComponent(member.github_username)}`} target="_blank" rel="noreferrer" title={`打开 ${member.github_username} 的 GitHub`} onClick={(event) => event.stopPropagation()}>
            <GitBranch size={13} /> GitHub
          </a>
        )}
      </div>
      <div className={`load-bar ${loadLevel}`}>
        <i
          style={{
            width: `${Math.min(100, (member.current / Math.max(1, member.max_concurrent_tasks)) * 100)}%`,
          }}
        />
      </div>
    </div>
  );
}

function TaskRow({ task, onAction, onRecommend, canManageTask }) {
  const meta = statusMeta[task.status] || statusMeta.unassigned;
  const action =
    task.status === "in_progress"
      ? "complete"
      : task.status === "paused"
        ? "resume"
        : task.status === "assigned"
          ? "start"
          : null;
  return (
    <div className="task-row">
      <div className={`priority-dot ${meta.tone}`} />
      <div className="task-row-main">
        <strong>{task.title}</strong>
        <div className="task-row-meta">
          <span className={`tag ${meta.tone}`}>{meta.label}</span>
          <span>截止 {formatDate(task.due_date)}</span>
          {task.assignee_name ? (
            <span className="assignee-inline">
              <span className="tiny-avatar">
                {initials(task.assignee_name)}
              </span>
              {task.assignee_name}
            </span>
          ) : onRecommend ? (
            <button className="assign-link" onClick={() => onRecommend(task)}>
              <Plus aria-hidden="true" /> 分配负责人
            </button>
          ) : (
            <span>尚未分配</span>
          )}
        </div>
      </div>
      {action && canManageTask?.(task) && (
        <button className="row-action" onClick={() => onAction(task, action)}>
          {action === "complete"
            ? "完成"
            : action === "resume"
              ? "继续"
              : "开始"}
        </button>
      )}
    </div>
  );
}

function TaskCard({ task, onAction, onRecommend, canManageTask, onOpen }) {
  const m = statusMeta[task.status];
  const action =
    task.status === "in_progress"
      ? "complete"
      : task.status === "assigned"
        ? "start"
        : task.status === "paused"
          ? "resume"
          : null;
  return (
    <article className="task-card">
      <div className="task-card-top">
        <span className={`tag ${m.tone}`}>{m.label}</span>
        <button
          className="kebab"
          title="查看任务详情"
          onClick={() => onOpen?.(task)}
        >
          查看
        </button>
      </div>
      <button className="task-card-title" onClick={() => onOpen?.(task)}>
        <h3>{task.title}</h3>
      </button>
      {task.description && <p>{task.description}</p>}
      <div className="task-card-info">
        <span><CalendarDays aria-hidden="true" /> {formatDate(task.due_date)}</span>
        <span><Clock3 aria-hidden="true" /> {task.estimated_hours || "—"}h</span>
      </div>
      <div className="task-card-bottom">
        {task.assignee_name ? (
          <span className="assignee-inline">
            <span className="tiny-avatar">{initials(task.assignee_name)}</span>
            {task.assignee_name}
          </span>
        ) : onRecommend ? (
          <button className="assign-link" onClick={() => onRecommend(task)}>
            <Plus aria-hidden="true" /> 分配负责人
          </button>
        ) : (
          <span>尚未分配</span>
        )}
        {action && canManageTask?.(task) && (
          <button
            className="mini-action"
            onClick={() => onAction(task, action)}
          >
            {action === "complete"
              ? "完成任务"
              : action === "resume"
                ? "继续"
                : "开始"}
          </button>
        )}
      </div>
    </article>
  );
}

function SkillBar({ label, value, color }) {
  return (
    <div className="skill-bar">
      <div>
        <span>{label}</span>
        <b>{value}%</b>
      </div>
      <div className="progress-track">
        <i className={color} style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

function RecommendCard({ item, selected, onSelect, onAccept }) {
  const dims = item.dimensions || {};
  return (
    <div
      className={`recommend-item ${selected ? "selected" : ""}`}
      onClick={() => onSelect(item.user_id)}
    >
      <div className="avatar avatar-0">{initials(item.name)}</div>
      <div className="recommend-main">
        <div className="recommend-name">
          <strong>{item.name}</strong>
          <span className="score">
            匹配度 {Math.round(item.score)}
            <small>%</small>
          </span>
        </div>
        <p className="recommend-summary">{item.reasons?.summary}</p>
        {item.reasons?.contrast && (
          <p className="recommend-contrast">{item.reasons.contrast}</p>
        )}
        <div className="dimension-grid">
          {["skill", "quality", "efficiency", "load"].map((key) => {
            const dim = dims[key] || {};
            return (
              <div className="dimension-row" key={key}>
                <span>{dimLabel(key)}</span>
                <div className="match-track">
                  <i
                    style={{ width: `${Math.round((dim.score || 0) * 100)}%` }}
                  />
                </div>
                <b>{Math.round((dim.score || 0) * 100)}</b>
              </div>
            );
          })}
        </div>
        <div className="reason-chips">
          {(dims.skill?.skill_families || []).slice(0, 3).map((family) => (
            <span key={family}>{family}</span>
          ))}
          {dims.quality?.samples > 0 && (
            <span>质量样本 {dims.quality.samples} 条</span>
          )}
          {dims.efficiency?.samples > 0 && (
            <span>工时样本 {dims.efficiency.samples} 条</span>
          )}
          {dims.load?.current_load ? (
            <span>负载 {dims.load.current_load}</span>
          ) : null}
          <span className={`source-chip ${item.source !== "rule" ? "ai" : ""}`}>
            {sourceLabel(item.source || dims.skill?.source)}
          </span>
          {item.profile_source === "historical" && (
            <span className="source-chip ai">参考历史画像</span>
          )}
        </div>
        <p className="muted-note">{dims.skill?.note}</p>
      </div>
      {onAccept && (
        <button
          className="choose-button"
          onClick={(event) => {
            event.stopPropagation();
            onAccept(item);
          }}
        >
          采纳
        </button>
      )}
    </div>
  );
}

export {
  PageTitle,
  Metric,
  MemberCard,
  TaskRow,
  TaskCard,
  SkillBar,
  RecommendCard,
};
