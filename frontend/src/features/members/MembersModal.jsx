import { useEffect, useState } from "react";
import { formatApiError, getJson, sendJson } from "../../api/client";
import { absoluteInviteUrl, copyText, initials } from "../../shared/core";

function MembersModal({ project, currentUser, onClose, onUpdated, onToast }) {
  const [members, setMembers] = useState(project.members || []);
  const [form, setForm] = useState({
    email: "",
    role: "member",
  });
  const [invite, setInvite] = useState(null);
  const [invitations, setInvitations] = useState([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getJson(`/api/projects/${project.id}/invitations`)
      .then((payload) => setInvitations(payload.items || []))
      .catch((error) => onToast(formatApiError(error)));
  }, [project.id]);
  async function createInvite() {
    setBusy(true);
    try {
      const result = await sendJson(`/api/projects/${project.id}/invitations`, {
        method: "POST",
        body: JSON.stringify({
          role: form.role,
          email: form.email.trim() || null,
          expires_in_hours: 168,
          max_uses: 10,
        }),
      });
      setInvite(result);
      setInvitations((items) => [result, ...items]);
      onToast("邀请链接已生成");
    } catch (error) {
      onToast(formatApiError(error));
    } finally {
      setBusy(false);
    }
  }
  async function removeMember(member) {
    const memberId = member.user_id || member.id;
    try {
      await sendJson(`/api/projects/${project.id}/members/${memberId}`, {
        method: "DELETE",
      });
      const next = members.filter(
        (item) => (item.user_id || item.id) !== memberId,
      );
      setMembers(next);
      onUpdated({ members: next });
      onToast(`已移除成员 ${member.name}`);
    } catch (error) {
      onToast(formatApiError(error));
    }
  }
  async function revokeInvitation(item) {
    try {
      await sendJson(`/api/invitations/${item.id}/revoke`, { method: "POST" });
      setInvitations((rows) =>
        rows.map((row) =>
          row.id === item.id ? { ...row, revoked: true } : row,
        ),
      );
      onToast("邀请已撤销");
    } catch (error) {
      onToast(formatApiError(error));
    }
  }
  async function updateRole(member, role) {
    try {
      const updated = await sendJson(
        `/api/projects/${project.id}/members/${member.user_id}`,
        { method: "PATCH", body: JSON.stringify({ role }) },
      );
      const next = members.map((m) =>
        m.user_id === member.user_id ? { ...m, ...updated, role } : m,
      );
      setMembers(next);
      onUpdated({ members: next });
    } catch {
      onToast("角色更新失败");
    }
  }
  const inviteUrl = absoluteInviteUrl(invite);
  async function copyInvite() {
    try {
      if (!(await copyText(inviteUrl)))
        throw new Error("clipboard unavailable");
      onToast("完整邀请链接已复制");
    } catch {
      onToast("复制失败，请手动复制邀请链接");
    }
  }
  return (
    <div className="modal-backdrop">
      <div className="modal members-modal">
        <div className="modal-head">
          <div>
            <h2>成员与邀请</h2>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        <div className="member-manage-list">
          {members.map((m) => {
            const memberId = m.user_id || m.id;
            return (
              <div className="member-manage-row" key={memberId}>
                <div className="avatar avatar-0">{initials(m.name)}</div>
                <div className="member-manage-info">
                  <strong title={m.name}>{m.name}</strong>
                  <span
                    title={`${m.email || "未设置邮箱"} · ${(m.skills || []).join("、") || "未填写技能"}`}
                  >
                    {m.email || "未设置邮箱"} ·{" "}
                    {(m.skills || []).join("、") || "未填写技能"}
                  </span>
                </div>
                <select
                  value={m.role || "member"}
                  disabled={memberId === project.owner_id}
                  onChange={(e) => updateRole(m, e.target.value)}
                >
                  <option value="owner">组长</option>
                  <option value="member">成员</option>
                  <option value="viewer">只读</option>
                </select>
                {memberId !== project.owner_id && (
                  <button
                    className="member-remove"
                    onClick={() => removeMember(m)}
                  >
                    移除
                  </button>
                )}
              </div>
            );
          })}
        </div>
        <div className="form-section-title">添加成员</div>
        <form
          className="member-add-form"
          onSubmit={(event) => {
            event.preventDefault();
            createInvite();
          }}
        >
          <div className="form-row">
            <label>
              邀请邮箱（可选）
              <input
                type="email"
                value={form.email}
                onChange={(e) =>
                  setForm((f) => ({ ...f, email: e.target.value }))
                }
                placeholder="留空则生成通用邀请"
              />
            </label>
            <label>
              角色
              <select
                value={form.role}
                onChange={(e) =>
                  setForm((f) => ({ ...f, role: e.target.value }))
                }
              >
                <option value="member">成员</option>
                <option value="viewer">只读</option>
              </select>
            </label>
          </div>
          <div className="modal-actions">
            <button
              type="button"
              className="primary-button"
              disabled={busy}
              onClick={createInvite}
            >
              {busy ? "生成中…" : "生成邀请链接"}
            </button>
          </div>
        </form>
        {invite && (
          <div className="invite-result">
            <strong>邀请已生成</strong>
            <span title={inviteUrl}>{inviteUrl}</span>
            <button type="button" className="ghost-button" onClick={copyInvite}>
              复制完整链接
            </button>
          </div>
        )}
        <div className="form-section-title invite-history-title">邀请记录</div>
        <div className="invitation-list">
          {invitations.length ? (
            invitations.map((item) => (
              <div className="invitation-row" key={item.id}>
                <div>
                  <strong>{item.email || "通用邀请"}</strong>
                  <span>
                    {item.role === "viewer" ? "只读" : "成员"} · 已使用{" "}
                    {item.used_count || 0}/{item.max_uses || 1}
                  </span>
                </div>
                <span className={item.revoked ? "danger-text" : ""}>
                  {item.revoked
                    ? "已撤销"
                    : item.accepted_at
                      ? "已接受"
                      : "有效"}
                </span>
                {!item.revoked && !item.accepted_at && (
                  <button onClick={() => revokeInvitation(item)}>撤销</button>
                )}
              </div>
            ))
          ) : (
            <div className="empty-state compact">还没有邀请记录</div>
          )}
        </div>
      </div>
    </div>
  );
}

export { MembersModal };
