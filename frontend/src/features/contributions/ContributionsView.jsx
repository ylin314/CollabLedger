import { useState } from "react";
import { sendJson } from "../../api/client";
import { formatDate } from "../../shared/core";
import { Metric, PageTitle } from "../../shared/components";

function ContributionsView({
  project,
  members,
  currentUser,
  role,
  canWrite,
  online,
  setProject,
  onToast,
}) {
  const [kind, setKind] = useState("all");
  const [memberId, setMemberId] = useState("all");
  const [status, setStatus] = useState("all");
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState(null);
  const contributions = project.contributions || [];
  const list = contributions.filter((contribution) => {
    if (kind !== "all" && contribution.kind !== kind) return false;
    if (status !== "all" && contribution.status !== status) return false;
    if (memberId !== "all" && Number(contribution.user_id) !== Number(memberId))
      return false;
    return true;
  });
  async function save(data) {
    try {
      const c = await sendJson(`/api/projects/${project.id}/contributions`, {
        method: "POST",
        body: JSON.stringify(data),
      });
      setProject((p) => ({
        ...p,
        contributions: [c, ...(p.contributions || [])],
      }));
      onToast("贡献记录已保存");
    } catch (error) {
      onToast(error.message);
    }
    setOpen(false);
  }
  async function decide(contribution, action, note) {
    try {
      const result = await sendJson(
        `/api/contributions/${contribution.id}/${action}`,
        {
          method: "POST",
          body: JSON.stringify({ note: note.trim() || null }),
        },
      );
      setProject((current) => ({
        ...current,
        contributions: current.contributions.map((item) =>
          item.id === contribution.id ? { ...item, ...result } : item,
        ),
      }));
      setDecision(null);
      onToast(action === "confirm" ? "贡献已确认" : "贡献已标记为争议");
    } catch (error) {
      onToast(error.message);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="CONTRIBUTION LEDGER"
        title="贡献账本"
        action={
          canWrite ? (
            <button className="primary-button" onClick={() => setOpen(true)}>
              ＋ 记录贡献
            </button>
          ) : null
        }
      />
      <div className="ledger-summary">
        <Metric
          label="本周贡献"
          value={contributions.length}
          hint="条可追溯记录"
          trend="up"
          color="blue"
        />
        <Metric
          label="代码提交"
          value={contributions
            .filter((c) => c.kind === "code")
            .reduce((a, c) => a + (c.quantity || 1), 0)}
          hint="次 commit / 变更"
          trend="up"
          color="purple"
        />
        <Metric
          label="活跃成员"
          value={new Set(contributions.map((c) => c.user_id)).size}
          hint={`共 ${members.length} 位成员`}
          trend="neutral"
          color="green"
        />
      </div>
      <div className="ledger-panel panel">
        <div className="ledger-header">
          <div className="filter-tabs compact">
            {[
              ["all", "全部"],
              ["code", "代码"],
              ["document", "文档"],
              ["meeting", "会议"],
              ["research", "调研"],
              ["test", "测试"],
              ["design", "设计"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={kind === id ? "active" : ""}
                onClick={() => setKind(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="ledger-filters">
            <select
              value={memberId}
              onChange={(event) => setMemberId(event.target.value)}
              aria-label="按成员筛选"
            >
              <option value="all">全部成员</option>
              {members.map((member) => (
                <option
                  key={member.user_id || member.id}
                  value={member.user_id || member.id}
                >
                  {member.name}
                </option>
              ))}
            </select>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              aria-label="按确认状态筛选"
            >
              <option value="all">全部状态</option>
              <option value="pending">待确认</option>
              <option value="confirmed">已确认</option>
              <option value="disputed">有争议</option>
            </select>
          </div>
        </div>
        <div className="ledger-list">
          {list.map((c, i) => (
            <ContributionItem
              key={c.id}
              c={c}
              i={i}
              isOwner={role === "owner"}
              onDecide={setDecision}
            />
          ))}
          {!list.length && (
            <div className="empty-state">还没有这类贡献记录</div>
          )}
        </div>
      </div>
      {open && (
        <ContributionModal
          members={
            role === "owner"
              ? members
              : members.filter((m) => m.user_id === currentUser.id)
          }
          onClose={() => setOpen(false)}
          onSave={save}
        />
      )}
      {decision && (
        <ContributionDecisionModal
          contribution={decision.contribution}
          action={decision.action}
          onClose={() => setDecision(null)}
          onSave={(note) =>
            decide(decision.contribution, decision.action, note)
          }
        />
      )}
    </>
  );
}

function ContributionItem({ c, i, isOwner, onDecide }) {
  const icons = {
    code: ["⌘", "purple"],
    document: ["▤", "blue"],
    meeting: ["◉", "amber"],
    task: ["✓", "green"],
    other: ["✦", "slate"],
  };
  const [icon, tone] = icons[c.kind] || icons.other;
  return (
    <div className="contribution-item">
      <div className={`contribution-icon ${tone}`}>{icon}</div>
      <div className="contribution-main">
        <strong>{c.title || "未命名贡献"}</strong>
        <p>{c.description || "成员提交了一条项目产出记录"}</p>
        <span>
          {c.user_name} · {formatDate(c.occurred_at || c.created_at)}
        </span>
        <div className="contribution-meta">
          <span className={`contribution-status ${c.status || "pending"}`}>
            {{ pending: "待确认", confirmed: "已确认", disputed: "有争议" }[
              c.status
            ] || "待确认"}
          </span>
          {c.evidence_url && (
            <a href={c.evidence_url} target="_blank" rel="noreferrer">
              查看证明
            </a>
          )}
        </div>
      </div>
      {isOwner && c.status !== "confirmed" && (
        <div className="contribution-actions">
          <button
            onClick={() => onDecide({ contribution: c, action: "confirm" })}
          >
            确认
          </button>
          <button
            className="danger-text"
            onClick={() => onDecide({ contribution: c, action: "dispute" })}
          >
            标记争议
          </button>
        </div>
      )}
      <div className="contribution-qty">
        <strong>{c.quantity || 1}</strong>
        <span>{c.kind === "code" ? "次" : "项"}</span>
      </div>
    </div>
  );
}

function ContributionModal({ members, onClose, onSave }) {
  const [form, setForm] = useState({
    user_id: members[0]?.id || "",
    kind: "code",
    title: "",
    description: "",
    quantity: 1,
    evidence_url: "",
  });
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">LOG CONTRIBUTION</span>
            <h2>记录一条贡献</h2>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        <div className="form-row">
          <label>
            成员
            <select
              value={form.user_id}
              onChange={(e) =>
                setForm((f) => ({ ...f, user_id: Number(e.target.value) }))
              }
            >
              {members.map((m) => (
                <option value={m.id} key={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            贡献类型
            <select
              value={form.kind}
              onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))}
            >
              <option value="code">代码 / Commit</option>
              <option value="document">文档</option>
              <option value="meeting">会议</option>
              <option value="research">调研</option>
              <option value="test">测试</option>
              <option value="design">设计</option>
              <option value="other">其他</option>
            </select>
          </label>
        </div>
        <label>
          贡献标题
          <input
            autoFocus
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="例如：完成 API 鉴权模块"
          />
        </label>
        <label>
          证明链接
          <input
            type="url"
            value={form.evidence_url}
            onChange={(e) =>
              setForm((f) => ({ ...f, evidence_url: e.target.value }))
            }
            placeholder="可填写文档、PR 或截图链接"
          />
        </label>
        <label>
          补充说明
          <textarea
            value={form.description}
            onChange={(e) =>
              setForm((f) => ({ ...f, description: e.target.value }))
            }
            placeholder="写下可被复盘的事实…"
          />
        </label>
        <label>
          数量 / 次数
          <input
            type="number"
            min="1"
            value={form.quantity}
            onChange={(e) =>
              setForm((f) => ({ ...f, quantity: Number(e.target.value) }))
            }
          />
        </label>
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
                evidence_url: form.evidence_url.trim() || null,
              })
            }
          >
            保存记录
          </button>
        </div>
      </div>
    </div>
  );
}

function ContributionDecisionModal({ contribution, action, onClose, onSave }) {
  const [note, setNote] = useState("");
  const disputed = action === "dispute";
  return (
    <div className="modal-backdrop">
      <div className="modal decision-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">CONTRIBUTION REVIEW</span>
            <h2>{disputed ? "标记贡献争议" : "确认贡献"}</h2>
            <p className="modal-sub">「{contribution.title}」</p>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        <label>
          {disputed ? "争议说明" : "确认备注"}
          <textarea
            autoFocus
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={
              disputed ? "说明需要补充或核实的事实" : "可选：记录确认依据"
            }
          />
        </label>
        <div className="modal-actions">
          <button className="ghost-button" onClick={onClose}>
            取消
          </button>
          <button
            className={disputed ? "danger-button" : "primary-button"}
            disabled={disputed && !note.trim()}
            onClick={() => onSave(note)}
          >
            {disputed ? "确认标记争议" : "确认这条贡献"}
          </button>
        </div>
      </div>
    </div>
  );
}

export {
  ContributionsView,
  ContributionItem,
  ContributionModal,
  ContributionDecisionModal,
};
