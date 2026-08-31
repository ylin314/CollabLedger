import { useEffect, useMemo, useState } from "react";
import { ArrowUpDown, Pencil, SlidersHorizontal, Trash2, X } from "lucide-react";
import { getJson, sendJson } from "../../api/client";
import { statusMeta } from "../../shared/core";
import { PageTitle, RecommendCard, TaskCard } from "../../shared/components";

function TasksView({
  tasks,
  members,
  onAction,
  onCreate,
  onRecommend,
  onBatch,
  canWrite,
  canManageTask,
  canEditTask,
  canDeleteTask,
  onUpdate,
  onDelete,
  canReviewTask,
  onReview,
  onToast,
  project,
  onReload,
}) {
  const [filter, setFilter] = useState("all");
  const [assignee, setAssignee] = useState("all");
  const [sort, setSort] = useState("due_date");
  const [detailTask, setDetailTask] = useState(null);
  const [editTask, setEditTask] = useState(null);
  const filtered = useMemo(() => {
    const result = tasks.filter((task) => {
      if (filter !== "all" && task.status !== filter) return false;
      if (assignee === "unassigned" && task.assignee_id) return false;
      if (
        assignee !== "all" &&
        assignee !== "unassigned" &&
        Number(task.assignee_id) !== Number(assignee)
      )
        return false;
      return true;
    });
    return [...result].sort((left, right) => {
      if (sort === "priority") {
        const weight = { high: 3, medium: 2, low: 1 };
        return (weight[right.priority] || 0) - (weight[left.priority] || 0);
      }
      if (sort === "created_at")
        return String(right.created_at || "").localeCompare(
          String(left.created_at || ""),
        );
      return String(left.due_date || "9999-12-31").localeCompare(
        String(right.due_date || "9999-12-31"),
      );
    });
  }, [assignee, filter, sort, tasks]);
  const columns = [
    "unassigned",
    "assigned",
    "in_progress",
    "paused",
    "completed",
    "overdue",
    "unfinished",
  ];
  const unassignedCount = tasks.filter(
    (t) => t.status === "unassigned" || !t.assignee_id,
  ).length;
  return (
    <>
      <PageTitle
        eyebrow="TASK BOARD"
        title="任务看板"
        action={
          canWrite ? (
            <div className="title-actions">
              <button className="ghost-button" onClick={onBatch}>
                ✦ 一键建议未分配
              </button>
              <button className="primary-button" onClick={onCreate}>
                ＋ 新建任务
              </button>
            </div>
          ) : null
        }
      />
      <div className="board-toolbar">
        <div className="filter-tabs">
          {[
            ["all", "全部"],
            ["in_progress", "进行中"],
            ["unassigned", "待分配"],
            ["completed", "已完成"],
            ["overdue", "延期"],
          ].map(([id, label]) => (
            <button
              className={filter === id ? "active" : ""}
              key={id}
              onClick={() => setFilter(id)}
            >
              {label}
              <span>
                {id === "all"
                  ? tasks.length
                  : tasks.filter((t) => t.status === id).length}
              </span>
            </button>
          ))}
        </div>
        <div className="board-actions">
          {canWrite && unassignedCount > 0 && (
            <button className="ghost-button" onClick={onBatch}>
              未分配 {unassignedCount} 项可出建议
            </button>
          )}
          <label className="board-select">
            <SlidersHorizontal size={14} />
            <select
              value={assignee}
              onChange={(event) => setAssignee(event.target.value)}
              aria-label="按负责人筛选"
            >
              <option value="all">全部负责人</option>
              <option value="unassigned">尚未分配</option>
              {members.map((member) => (
                <option
                  key={member.user_id || member.id}
                  value={member.user_id || member.id}
                >
                  {member.name}
                </option>
              ))}
            </select>
          </label>
          <label className="board-select">
            <ArrowUpDown size={14} />
            <select
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              aria-label="任务排序"
            >
              <option value="due_date">截止日期</option>
              <option value="priority">优先级</option>
              <option value="created_at">创建时间</option>
            </select>
          </label>
        </div>
      </div>
      <div className="board-grid">
        {columns.map((status) => {
          const list = filtered.filter((t) => t.status === status);
          const m = statusMeta[status];
          return (
            <div className="board-column" key={status}>
              <div className="column-header">
                <span className={`column-dot ${m.tone}`} />
                <strong>{m.label}</strong>
                <span className="column-count">{list.length}</span>
                {status === "unassigned" && canWrite && (
                  <button onClick={onBatch}>✦</button>
                )}
                {canWrite && <button onClick={onCreate}>＋</button>}
              </div>
              <div className="column-cards">
                {list.map((t) => (
                  <TaskCard
                    key={t.id}
                    task={t}
                    members={members}
                    onAction={onAction}
                    onRecommend={canWrite ? onRecommend : null}
                    canManageTask={canManageTask}
                    onOpen={setDetailTask}
                  />
                ))}
                {!list.length && <div className="empty-column">暂无任务</div>}
              </div>
            </div>
          );
        })}
      </div>
      {detailTask && (
        <TaskDetailModal
          task={detailTask}
          onClose={() => setDetailTask(null)}
          onReload={onReload}
          onEdit={(task) => {
            setDetailTask(null);
            setEditTask(task);
          }}
          onDelete={onDelete}
          canEdit={canEditTask?.(detailTask)}
          canDelete={canDeleteTask?.(detailTask)}
          canReview={canReviewTask?.(detailTask)}
          onReview={(task) => {
            setDetailTask(null);
            onReview(task);
          }}
        />
      )}
      {editTask && (
        <TaskModal
          members={members}
          project={project}
          initialTask={editTask}
          onClose={() => setEditTask(null)}
          onSave={async (data) => {
            const saved = await onUpdate(editTask, data);
            if (saved) setEditTask(null);
          }}
          onToast={onToast}
        />
      )}
    </>
  );
}

function TaskDetailModal({
  task,
  onClose,
  onReload,
  onEdit,
  onDelete,
  canEdit,
  canDelete,
  canReview,
  onReview,
}) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getJson(`/api/tasks/${task.id}`),
      getJson(`/api/tasks/${task.id}/logs`),
      getJson(`/api/tasks/${task.id}/checkins?page_size=100`),
      getJson(`/api/tasks/${task.id}/review`).catch((reason) =>
        reason.status === 404 ? null : Promise.reject(reason),
      ),
      getJson(`/api/tasks/${task.id}/review/history`),
    ])
      .then(([item, logs, checkins, review, reviewHistory]) => {
        if (!cancelled)
          setDetail({
            item,
            logs: logs.items || [],
            checkins: checkins.items || [],
            review,
            reviewHistory: reviewHistory.items || [],
          });
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [task.id]);
  const item = detail?.item || task;
  return (
    <div className="modal-backdrop">
      <div className="modal task-detail-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">TASK DETAIL</span>
            <h2>{item.title}</h2>
          </div>
          <button title="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        {error ? (
          <div className="form-error">{error}</div>
        ) : !detail ? (
          <div className="loading-state">正在加载任务详情…</div>
        ) : (
          <div className="task-detail-content">
            <div className="task-detail-facts">
              <span className={`tag ${statusMeta[item.status]?.tone}`}>
                {statusMeta[item.status]?.label}
              </span>
              <span>
                优先级：
                {{ high: "高", medium: "中", low: "低" }[item.priority] || "中"}
              </span>
              <span>负责人：{item.assignee_name || "未分配"}</span>
              <span>评审人：{item.reviewer_name || "未指定"}</span>
              <span>截止：{item.due_date || "未设置"}</span>
              <span>
                工时：{item.actual_hours || 0} / {item.estimated_hours || 0}h
              </span>
            </div>
            {item.description && (
              <p className="task-detail-description">{item.description}</p>
            )}
            <div className="task-detail-grid">
              <section>
                <h3>状态日志</h3>
                {detail.logs.length ? (
                  detail.logs.map((log) => (
                    <div className="timeline-row" key={log.id}>
                      <strong>
                        {statusMeta[log.to_status]?.label || log.action}
                      </strong>
                      <span>
                        {log.user_name || "系统"} ·{" "}
                        {log.at?.slice(0, 16).replace("T", " ")}
                      </span>
                      {log.note && <p>{log.note}</p>}
                    </div>
                  ))
                ) : (
                  <div className="empty-state compact">暂无日志</div>
                )}
              </section>
              <section>
                <h3>主动打卡</h3>
                {detail.checkins.length ? (
                  detail.checkins.map((checkin) => (
                    <div className="timeline-row" key={checkin.id}>
                      <strong>{checkin.user_name}</strong>
                      <span>
                        {checkin.hours || 0}h ·{" "}
                        {checkin.created_at?.slice(0, 16).replace("T", " ")}
                      </span>
                      <p>{checkin.content}</p>
                      {checkin.blockers && <em>阻塞：{checkin.blockers}</em>}
                    </div>
                  ))
                ) : (
                  <div className="empty-state compact">暂无打卡</div>
                )}
              </section>
            </div>
            <section className="review-history">
              <h3>质量评价</h3>
              {detail.review ? (
                <div className="review-current">
                  <strong>
                    {Number(detail.review.quality).toFixed(1)} / 5
                  </strong>
                  <span>{detail.review.reviewer_name}</span>
                  <p>{detail.review.comment || "未填写评价说明"}</p>
                </div>
              ) : (
                <div className="empty-state compact">任务尚未评价</div>
              )}
              {detail.reviewHistory.length > 1 && (
                <details>
                  <summary>
                    查看历史评价（{detail.reviewHistory.length}）
                  </summary>
                  {detail.reviewHistory.map((row) => (
                    <div className="timeline-row" key={row.id}>
                      <strong>{Number(row.quality).toFixed(1)} / 5</strong>
                      <span>
                        {row.reviewer_name} ·{" "}
                        {row.created_at?.slice(0, 16).replace("T", " ")}
                      </span>
                      <p>{row.comment || "未填写说明"}</p>
                    </div>
                  ))}
                </details>
              )}
            </section>
          </div>
        )}
        {confirmDelete && (
          <div className="danger-confirm">
            <div>
              <strong>确认删除「{item.title}」？</strong>
              <span>任务将从看板移除，已有记录不会再出现在项目统计中。</span>
            </div>
            <button
              className="ghost-button"
              disabled={deleting}
              onClick={() => setConfirmDelete(false)}
            >
              取消
            </button>
            <button
              className="danger-button"
              disabled={deleting}
              onClick={async () => {
                setDeleting(true);
                const deleted = await onDelete(item);
                setDeleting(false);
                if (deleted) onClose();
              }}
            >
              {deleting ? "正在删除…" : "确认删除"}
            </button>
          </div>
        )}
        <div className="modal-actions task-detail-actions">
          {canDelete && !confirmDelete && (
            <button
              className="danger-text-button"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={16} /> 删除任务
            </button>
          )}
          {canEdit && (
            <button className="ghost-button" onClick={() => onEdit(item)}>
              <Pencil size={16} /> 编辑任务
            </button>
          )}
          {canReview && (
            <button className="primary-button" onClick={() => onReview(item)}>
              评价任务
            </button>
          )}
          <button
            className="ghost-button"
            onClick={() => {
              onReload?.();
              onClose();
            }}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

function TaskModal({
  members,
  project,
  initialTask = null,
  onClose,
  onSave,
  onToast,
}) {
  const editing = Boolean(initialTask);
  const [form, setForm] = useState(() => ({
    title: initialTask?.title || "",
    description: initialTask?.description || "",
    task_type: initialTask?.task_type || "其他",
    estimated_hours: initialTask?.estimated_hours ?? 4,
    due_date: initialTask?.due_date || "",
    assignee_id: initialTask?.assignee_id
      ? String(initialTask.assignee_id)
      : "",
    reviewer_id: initialTask?.reviewer_id
      ? String(initialTask.reviewer_id)
      : "",
    priority: initialTask?.priority || "medium",
    participant_ids: initialTask?.participant_ids || [],
  }));
  const [preview, setPreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const update = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  async function previewRecommend() {
    if (!form.title.trim()) {
      onToast("请先填写任务名称，再查看建议");
      return;
    }
    setPreviewBusy(true);
    try {
      const query = new URLSearchParams({
        task_name: form.title.trim(),
        task_type: form.task_type || "",
        estimated_hours: String(form.estimated_hours || 1),
      });
      setPreview(
        await getJson(`/api/projects/${project.id}/recommendations?${query}`),
      );
    } catch (reason) {
      onToast(reason.message);
    } finally {
      setPreviewBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <div className="modal recommend-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">{editing ? "EDIT TASK" : "NEW TASK"}</span>
            <h2>{editing ? "编辑任务" : "创建新任务"}</h2>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        <label>
          任务名称
          <input
            autoFocus
            value={form.title}
            onChange={(e) => update("title", e.target.value)}
            placeholder="例如：完成项目 PPT"
          />
        </label>
        <label>
          任务描述
          <textarea
            value={form.description}
            onChange={(e) => update("description", e.target.value)}
            placeholder="补充任务背景和交付标准…"
          />
        </label>
        <div className="form-row">
          <label>
            任务类型
            <select
              value={form.task_type}
              onChange={(e) => update("task_type", e.target.value)}
            >
              <option>其他</option>
              <option>前端</option>
              <option>后端</option>
              <option>数据库</option>
              <option>文档</option>
              <option>汇报</option>
            </select>
          </label>
          <label>
            预计耗时（小时）
            <input
              type="number"
              min="0"
              value={form.estimated_hours}
              onChange={(e) =>
                update("estimated_hours", Number(e.target.value))
              }
            />
          </label>
        </div>
        <div className="member-picker task-participant-picker">
          <span>共同参与者</span>
          <small>负责人会自动计入参与者</small>
          {members.map((member) => {
            const id = member.user_id || member.id;
            return <label className="checkbox-row" key={id}><input type="checkbox" checked={form.participant_ids.includes(id)} onChange={(e) => update("participant_ids", e.target.checked ? [...new Set([...form.participant_ids, id])] : form.participant_ids.filter((item) => item !== id))} />{member.name}</label>;
          })}
        </div>
        <div className="form-row">
          <label>
            优先级
            <select
              value={form.priority}
              onChange={(e) => update("priority", e.target.value)}
            >
              <option value="low">低</option>
              <option value="medium">中</option>
              <option value="high">高</option>
            </select>
          </label>
          <label>
            任务评审人
            <select
              value={form.reviewer_id}
              onChange={(e) => update("reviewer_id", e.target.value)}
            >
              <option value="">暂不指定</option>
              {members.map((m) => (
                <option
                  value={m.user_id || m.id}
                  key={m.user_id || m.id}
                >
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-row">
          <label>
            截止日期
            <input
              type="date"
              value={form.due_date}
              onChange={(e) => update("due_date", e.target.value)}
            />
          </label>
          <label>
            负责人（可稍后分配）
            <select
              value={form.assignee_id}
              onChange={(e) => update("assignee_id", e.target.value)}
            >
              <option value="">暂不分配</option>
              {members.map((m) => (
                <option
                  value={m.user_id || m.id}
                  key={m.user_id || m.id}
                >
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!editing && (
          <div className="title-actions">
            <button
              className="ghost-button"
              disabled={previewBusy}
              onClick={previewRecommend}
            >
              {previewBusy ? "计算中…" : "先看建议，不自动指派"}
            </button>
          </div>
        )}
        {!editing && preview && (
          <div className="recommend-preview">
            <p className="muted-note">{preview.disclaimer}</p>
            {(preview.recommendations || []).map((item) => (
              <RecommendCard
                key={item.user_id}
                item={item}
                selected={String(form.assignee_id) === String(item.user_id)}
                onSelect={(id) => update("assignee_id", String(id))}
                onAccept={(rec) => {
                  update("assignee_id", String(rec.user_id));
                  onToast(`已预选 ${rec.name}，仍需点击创建任务`);
                }}
              />
            ))}
            {preview.excluded?.length ? (
              <div className="excluded-box">
                {preview.excluded.map((item) => (
                  <p key={item.user_id}>
                    {item.name}：{item.reason}
                  </p>
                ))}
              </div>
            ) : null}
          </div>
        )}
        <div className="modal-actions">
          <button className="ghost-button" onClick={onClose}>
            取消
          </button>
          <button
            className="primary-button"
            disabled={!form.title.trim()}
            onClick={() =>
              onSave({
                ...form,
                assignee_id: form.assignee_id ? Number(form.assignee_id) : null,
                estimated_hours: Number(form.estimated_hours),
                due_date: form.due_date || null,
                reviewer_id: form.reviewer_id ? Number(form.reviewer_id) : null,
                participant_ids: form.participant_ids,
              })
            }
          >
            {editing ? "保存修改" : "创建任务"}
          </button>
        </div>
      </div>
    </div>
  );
}

function WorklogModal({ tasks, role, user, onClose, onToast, onSaved }) {
  const availableTasks = tasks.filter(
    (task) => role === "owner" || task.assignee_id === user.id || (task.participant_ids || []).includes(user.id),
  );
  const [form, setForm] = useState({
    task_id: availableTasks[0]?.id || "",
    content: "",
    hours: 1,
    blockers: "",
  });
  const [busy, setBusy] = useState(false);
  async function save() {
    if (!form.task_id || !form.content.trim()) return;
    setBusy(true);
    try {
      await sendJson(`/api/tasks/${form.task_id}/checkins`, {
        method: "POST",
        body: JSON.stringify({
          content: form.content.trim(),
          hours: Number(form.hours),
          blockers: form.blockers.trim() || null,
        }),
      });
      onToast("任务打卡已保存");
      // 先关闭弹窗立即离开打卡页；数据刷新后台执行，
      // 避免迟到的 onSaved 回调把用户刚点击的页面拉回总览。
      onClose();
      void onSaved?.();
    } catch (error) {
      setBusy(false);
      onToast(error.message);
    }
  }
  return (
    <div className="modal-backdrop">
      <div className="modal worklog-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">TASK CHECK-IN</span>
            <h2>今日主动打卡</h2>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        {availableTasks.length ? (
          <>
            <label>
              关联任务
              <select
                value={form.task_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, task_id: Number(e.target.value) }))
                }
              >
                {availableTasks.map((task) => (
                  <option value={task.id} key={task.id}>
                    {task.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              完成内容
              <textarea
                autoFocus
                value={form.content}
                onChange={(e) =>
                  setForm((f) => ({ ...f, content: e.target.value }))
                }
                placeholder="完成了什么、下一步是什么？"
              />
            </label>
            <div className="form-row">
              <label>
                投入小时
                <input
                  type="number"
                  min="0"
                  max="24"
                  step="0.5"
                  value={form.hours}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, hours: e.target.value }))
                  }
                />
              </label>
              <label>
                阻塞事项
                <input
                  value={form.blockers}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, blockers: e.target.value }))
                  }
                  placeholder="没有可留空"
                />
              </label>
            </div>
            <div className="modal-actions">
              <button className="ghost-button" onClick={onClose}>
                取消
              </button>
              <button
                className="primary-button"
                disabled={busy || !form.content.trim()}
                onClick={save}
              >
                {busy ? "保存中…" : "保存打卡"}
              </button>
            </div>
          </>
        ) : (
          <div className="empty-state">
            当前没有可打卡的任务。普通成员只能为自己负责的任务打卡。
          </div>
        )}
      </div>
    </div>
  );
}

function QualityReviewModal({
  project,
  task,
  currentUser,
  members,
  onClose,
  onSaved,
}) {
  const [score, setScore] = useState(task.quality || 4);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    try {
      const review = await sendJson(`/api/tasks/${task.id}/review`, {
        method: "POST",
        body: JSON.stringify({
          quality: Number(score),
          comment: comment.trim() || null,
        }),
      });
      onSaved(review.quality);
      onClose();
    } catch (error) {
      alert(error.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <div className="modal quality-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">QUALITY REVIEW</span>
            <h2>评价任务交付质量</h2>
            <p className="modal-sub">
              「{task.title}」 ·{" "}
              {task.assignee_name ||
                members.find((m) => m.id === task.assignee_id)?.name ||
                "未分配"}
            </p>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        <label>
          质量评分{" "}
          <span className="quality-value">{Number(score).toFixed(1)} / 5</span>
          <input
            className="quality-range"
            type="range"
            min="0"
            max="5"
            step="0.5"
            value={score}
            onChange={(e) => setScore(e.target.value)}
          />
        </label>
        <label>
          评价说明
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="从完成度、准确性、协作交付等方面记录事实…"
          />
        </label>
        <div className="modal-actions">
          <button className="ghost-button" onClick={onClose}>
            稍后评价
          </button>
          <button className="primary-button" disabled={busy} onClick={submit}>
            {busy ? "保存中…" : "提交评价"}
          </button>
        </div>
      </div>
    </div>
  );
}

export {
  TasksView,
  TaskModal,
  TaskDetailModal,
  WorklogModal,
  QualityReviewModal,
};
