import { useEffect, useState } from "react";
import { getJson, sendJson } from "../../api/client";
import { sourceLabel } from "../../shared/core";
import { PageTitle, RecommendCard } from "../../shared/components";

function ReviewerPrompt({ taskId, taskTitle, members, assignedUserId, onSaved, onSkip }) {
  const [reviewerId, setReviewerId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reviewers = (members || []).filter((member) => {
    const id = Number(member.user_id || member.id);
    return id !== Number(assignedUserId) && ["owner", "member", "viewer"].includes(member.role);
  });

  async function save() {
    if (!reviewerId || busy) return;
    setBusy(true);
    setError("");
    try {
      const updated = await sendJson(`/api/tasks/${taskId}`, {
        method: "PATCH",
        body: JSON.stringify({ reviewer_id: Number(reviewerId) }),
      });
      await onSaved(updated);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal recommend-modal reviewer-prompt">
        <div className="modal-head">
          <div>
            <span className="eyebrow">OPTIONAL REVIEWER</span>
            <h2>负责人已更新</h2>
          </div>
          <button onClick={onSkip}>×</button>
        </div>
        <p className="modal-sub">
          「{taskTitle}」已完成负责人指派。可选：同时把某人设为评审人。系统不会自动修改评审人。
        </p>
        <label>
          选择评审人（可跳过）
          <select value={reviewerId} onChange={(event) => setReviewerId(event.target.value)}>
            <option value="">暂不设置</option>
            {reviewers.map((member) => (
              <option key={member.user_id || member.id} value={member.user_id || member.id}>
                {member.name}{member.role === "viewer" ? "（只读成员）" : ""}
              </option>
            ))}
          </select>
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button className="ghost-button" onClick={onSkip}>跳过</button>
          <button className="primary-button" disabled={!reviewerId || busy} onClick={save}>
            {busy ? "保存中…" : "确认设置评审人"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RecommendModal({
  task,
  project,
  members,
  onClose,
  onToast,
  setProject,
  currentUserId,
}) {
  const [payload, setPayload] = useState(null);
  const [reviewerPrompt, setReviewerPrompt] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const assignable = (members || []).filter(
    (member) => member.role === "member",
  );
  useEffect(() => {
    (async () => {
      try {
        const query = task.id
          ? new URLSearchParams({ task_id: String(task.id) })
          : new URLSearchParams({
              task_name: task.title,
              task_type: task.task_type || "",
              estimated_hours: String(task.estimated_hours || 1),
            });
        const response = await getJson(
          `/api/projects/${project.id}/recommendations?${query}`,
        );
        setPayload(response);
        setSelected(response.recommendations?.[0]?.user_id || "");
      } catch (reason) {
        setError(reason.message);
      } finally {
        setBusy(false);
      }
    })();
  }, []);
  async function decide(userId, note) {
    if (!task.id) {
      onToast("任务尚未创建，建议仅供预览，不会自动指派");
      return;
    }
    if (!payload?.recommendation_id) {
      onToast("还没有推荐记录");
      return;
    }
    try {
      const result = await sendJson(
        `/api/projects/${project.id}/recommendations/${payload.recommendation_id}/decide`,
        {
          method: "POST",
          body: JSON.stringify({ user_id: Number(userId), note }),
        },
      );
      const chosen = (members || []).find(
        (member) =>
          member.user_id === Number(userId) || member.id === Number(userId),
      );
      setProject((projectState) => ({
        ...projectState,
        tasks: projectState.tasks.map((existing) =>
          existing.id === task.id
            ? {
                ...existing,
                ...result.task,
                assignee_name: chosen?.name || result.task.assignee_name,
              }
            : existing,
        ),
      }));
      onToast(
        result.changed
          ? `已将「${task.title}」改派给${chosen?.name || "选定成员"}`
          : `已将「${task.title}」分配给${chosen?.name || "选定成员"}`,
      );
      const canSetReviewer =
        Number(currentUserId) === Number(project.owner_id) ||
        Number(currentUserId) === Number(task.created_by);
      if (canSetReviewer) {
        setReviewerPrompt({
          taskId: task.id,
          taskTitle: task.title,
          assignedUserId: Number(userId),
        });
      } else {
        onClose();
      }
    } catch (reason) {
      setError(reason.message);
    }
  }
  const results = payload?.recommendations || [];
  return (
    <>
      <div className="modal-backdrop">
        <div className="modal recommend-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">AI RECOMMENDATION</span>
            <h2>负责人建议</h2>
            <p className="modal-sub">
              「{task.title}」 · 推荐仅供参考，不构成成员排名，也不会自动指派
            </p>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        {busy ? (
          <div className="recommend-loading">
            <span className="loader" />
            正在计算匹配度…
          </div>
        ) : error ? (
          <div className="form-error">{error}</div>
        ) : (
          <>
            {payload?.comparison?.summary && (
              <p className="recommend-summary">{payload.comparison.summary}</p>
            )}
            <p className="muted-note">
              技能来源 {sourceLabel(payload?.skill_source)} · 理由来源{" "}
              {sourceLabel(payload?.reason_source)} · 不会自动指派
            </p>
            {payload?.errors && Object.keys(payload.errors).length > 0 && (
              <div className="degrade-note">
                <strong>⚠ AI 服务暂不可用，已自动回退规则计算</strong>
<span>网络较差时可在服务端 .env 调大 RECOMMEND_LLM_TIMEOUT（默认 30 秒）后重启，即可恢复 AI 增强。</span>
                {payload.errors.skill_error ? (
                  <span>技能：{payload.errors.skill_error}</span>
                ) : null}
                {payload.errors.reason_error ? (
                  <span>理由：{payload.errors.reason_error}</span>
                ) : null}
              </div>
            )}
            <div className="recommend-list">
              {results.map((item) => (
                <RecommendCard
                  key={item.user_id}
                  item={item}
                  selected={selected === item.user_id}
                  onSelect={setSelected}
                  onAccept={(item) =>
                    decide(item.user_id, item.reasons?.summary || "采纳推荐")
                  }
                />
              ))}
              {!results.length && (
                <div className="empty-state">暂无可推荐成员</div>
              )}
            </div>
            {payload?.excluded?.length ? (
              <div className="excluded-box">
                <strong>未进入候选</strong>
                {payload.excluded.map((item) => (
                  <p key={item.user_id}>
                    {item.name}：{item.reason}
                  </p>
                ))}
              </div>
            ) : null}
            <label>
              也可以手选其他成员
              <select
                value={selected}
                onChange={(event) => setSelected(Number(event.target.value))}
              >
                <option value="">请选择</option>
                {assignable.map((member) => (
                  <option
                    value={member.user_id || member.id}
                    key={member.user_id || member.id}
                  >
                    {member.name}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <div className="recommend-foot">
          <span>
            ◉ {payload?.disclaimer || "推荐仅供参考，最终由组长决定。"}
          </span>
          <div className="title-actions">
            <button className="ghost-button" onClick={onClose}>
              关闭
            </button>
            {task.id && selected && (
              <button
                className="primary-button"
                onClick={() => decide(selected, "手工指定负责人")}
              >
                按所选指派
              </button>
            )}
          </div>
        </div>
        </div>
      </div>
      {reviewerPrompt && (
        <ReviewerPrompt
          taskId={reviewerPrompt.taskId}
          taskTitle={reviewerPrompt.taskTitle}
          members={members}
          assignedUserId={reviewerPrompt.assignedUserId}
          onSaved={(updated) => {
            setProject((projectState) => ({
              ...projectState,
              tasks: projectState.tasks.map((existing) =>
                existing.id === reviewerPrompt.taskId
                  ? { ...existing, ...updated }
                  : existing,
              ),
            }));
            onToast("评审人已设置");
            setReviewerPrompt(null);
            onClose();
          }}
          onSkip={() => {
            setReviewerPrompt(null);
            onClose();
          }}
        />
      )}
    </>
  );
}

function RecommendationsView({
  project,
  members,
  canWrite,
  currentUserId,
  onRecommend,
  onToast,
  setProject,
  onReload,
}) {
  const [batch, setBatch] = useState(null);
  const [reviewerPrompt, setReviewerPrompt] = useState(null);
  const [history, setHistory] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const unassigned = (project.tasks || []).filter(
    (task) => task.status === "unassigned" || !task.assignee_id,
  );
  async function loadHistory() {
    try {
      setHistory(
        await getJson(`/api/projects/${project.id}/recommendations/history`),
      );
    } catch (reason) {
      setError(reason.message);
    }
  }
  useEffect(() => {
    loadHistory();
  }, [project.id]);
  async function generate() {
    setBusy(true);
    setError("");
    try {
      const payload = await sendJson(
        `/api/projects/${project.id}/recommendations/batch`,
        { method: "POST", body: JSON.stringify({ limit: 3 }) },
      );
      setBatch(payload);
      await loadHistory();
      onToast(`已为 ${payload.count} 项未分配任务生成建议`);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }
  async function decide(recId, userId, title) {
    try {
      const result = await sendJson(
        `/api/projects/${project.id}/recommendations/${recId}/decide`,
        {
          method: "POST",
          body: JSON.stringify({ user_id: userId, note: "批量页采纳推荐" }),
        },
      );
      setProject((projectState) => ({
        ...projectState,
        tasks: projectState.tasks.map((existing) =>
          existing.id === result.task.id
            ? { ...existing, ...result.task }
            : existing,
        ),
      }));
      onToast(`已将「${title}」分配给选定成员`);
      await onReload();
      await loadHistory();
      const task = (project.tasks || []).find(
        (existing) => existing.id === result.task.id,
      );
      const canSetReviewer =
        Number(currentUserId) === Number(project.owner_id) ||
        Number(currentUserId) === Number(task?.created_by);
      if (canSetReviewer) {
        setReviewerPrompt({
          taskId: result.task.id,
          taskTitle: title,
          assignedUserId: Number(userId),
        });
      }
    } catch (reason) {
      setError(reason.message);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="TASK RECOMMEND"
        title="未分配任务建议"
        action={
          canWrite ? (
            <button
              className="primary-button"
              disabled={busy || !unassigned.length}
              onClick={generate}
            >
              {busy ? "生成中…" : "一键给所有未分配任务出建议"}
            </button>
          ) : null
        }
      />
      {error && <div className="form-error">{error}</div>}
      <div className="stage2-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>未分配任务</h2>
              <p>共 {unassigned.length} 项，建议生成后仍由组长确认</p>
            </div>
          </div>
          {unassigned.length ? (
            unassigned.map((task) => (
              <div className="weekly-member-row" key={task.id}>
                <strong>{task.title}</strong>
                <button
                  className="text-button"
                  onClick={() => onRecommend(task)}
                >
                  查看建议 →
                </button>
              </div>
            ))
          ) : (
            <div className="empty-state">当前没有未分配任务</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>最近推荐记录</h2>
              <p>记录是否采纳、采纳了谁、是否改成别人</p>
            </div>
          </div>
          {(history?.items || []).length ? (
            history.items.slice(0, 8).map((item) => {
              const nameOf = (id) =>
                (members || []).find(
                  (m) =>
                    Number(m.user_id) === Number(id) ||
                    Number(m.id) === Number(id),
                )?.name || "";
              return (
                <div className="weekly-member-row" key={item.id}>
                  <div>
                    <strong>{item.task_name || "未命名任务"}</strong>
                    <span>
                      {item.status_label || item.status} · 推荐{" "}
                      {item.top?.name || "暂无候选"}
                      {item.top?.score ? `· ${item.top.score} 分` : ""}
                    </span>
                    <span className="history-meta">
                      {item.mode === "batch" ? "批量生成" : "单任务生成"} ·{" "}
                      {sourceLabel(item.source)}
                      {nameOf(item.accepted_user_id)
                        ? ` · 采纳：${nameOf(item.accepted_user_id)}`
                        : ""}
                      {item.status === "manual"
                        ? ` · 改派给：${nameOf(item.assigned_user_id) || "其他成员"}`
                        : ""}
                    </span>
                  </div>
                  <span>
                    {item.created_at?.slice(0, 16)?.replace("T", " ")}
                  </span>
                </div>
              );
            })
          ) : (
            <div className="empty-state">还没有推荐记录</div>
          )}
        </section>
      </div>
      {(batch?.items || []).map((item) => (
        <section className="panel" key={item.recommendation_id}>
          <div className="panel-header">
            <div>
              <h2>{item.task.task_name}</h2>
              <p>
                {item.source === "rule" ? "规则推荐" : "规则 + AI 语义匹配"} ·{" "}
                {item.disclaimer}
              </p>
            </div>
          </div>
          {item.comparison?.summary && (
            <p className="recommend-summary">{item.comparison.summary}</p>
          )}
          <div className="recommend-list">
            {(item.recommendations || []).map((candidate) => (
              <RecommendCard
                key={candidate.user_id}
                item={candidate}
                selected={false}
                onSelect={() => {}}
                onAccept={
                  canWrite
                    ? (rec) =>
                        decide(
                          item.recommendation_id,
                          rec.user_id,
                          item.task.task_name,
                        )
                    : null
                }
              />
            ))}
          </div>
          {item.excluded?.length ? (
            <div className="excluded-box">
              {item.excluded.map((row) => (
                <p key={row.user_id}>
                  {row.name}：{row.reason}
                </p>
              ))}
            </div>
          ) : null}
        </section>
      ))}
      {reviewerPrompt && (
        <ReviewerPrompt
          taskId={reviewerPrompt.taskId}
          taskTitle={reviewerPrompt.taskTitle}
          members={members}
          assignedUserId={reviewerPrompt.assignedUserId}
          onSaved={async (updated) => {
            setProject((projectState) => ({
              ...projectState,
              tasks: projectState.tasks.map((existing) =>
                existing.id === reviewerPrompt.taskId
                  ? { ...existing, ...updated }
                  : existing,
              ),
            }));
            onToast("评审人已设置");
            setReviewerPrompt(null);
            await onReload();
          }}
          onSkip={() => setReviewerPrompt(null)}
        />
      )}
    </>
  );
}

export { RecommendModal, RecommendationsView };
