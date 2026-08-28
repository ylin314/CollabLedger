import { useEffect, useState } from "react";
import { ReceiptText } from "lucide-react";
import { sendJson } from "../../api/client";

function CreateProjectView({ currentUser, onCreated, onCancel }) {
  const [classrooms, setClassrooms] = useState([]);
  const [classroomMembers, setClassroomMembers] = useState([]);
  const [classroomId, setClassroomId] = useState("");
  const [memberIds, setMemberIds] = useState([]);
  const [newClassroom, setNewClassroom] = useState("");
  const [form, setForm] = useState({
    name: "",
    project_type: "课程项目",
    description: "",
    start_date: "",
    end_date: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { sendJson("/api/classrooms").then((data) => { const items = data.items || []; setClassrooms(items); if (items[0]) setClassroomId(String(items[0].id)); }).catch(() => {}); }, []);
  useEffect(() => { if (!classroomId) return; sendJson(`/api/classrooms/${classroomId}/members`).then((data) => setClassroomMembers(data.items || [])).catch(() => setClassroomMembers([])); }, [classroomId]);
  const update = (key, value) =>
    setForm((current) => ({ ...current, [key]: value }));
  async function submit(event) {
    event.preventDefault();
    setError("");
    if (!form.name.trim()) {
      setError("请填写项目名称");
      return;
    }
    setBusy(true);
    try {
      let selectedClassroomId = classroomId ? Number(classroomId) : null;
      if (!selectedClassroomId && newClassroom.trim()) {
        const createdClassroom = await sendJson("/api/classrooms", { method: "POST", body: JSON.stringify({ name: newClassroom.trim() }) });
        selectedClassroomId = createdClassroom.id;
      }
      const project = await sendJson("/api/projects", {
        method: "POST",
        body: JSON.stringify({
          name: form.name.trim(),
          project_type: form.project_type,
          description: form.description.trim() || null,
          start_date: form.start_date || null,
          end_date: form.end_date || null,
          classroom_id: selectedClassroomId,
          member_ids: memberIds,
        }),
      });
      await onCreated(project);
    } catch (err) {
      setError("创建失败，请确认后端已启动，并检查项目名称和负责人信息。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="create-project-screen">
      <div className="create-project-card">
        <div className="brand create-brand">
          <div className="brand-mark"><ReceiptText aria-hidden="true" /></div>
          <div>
            <div className="brand-name">协作账本</div>
            <div className="brand-sub">COLLAB LEDGER</div>
          </div>
        </div>
        <h1>创建你的第一个项目</h1>
        <form onSubmit={submit}>
          <div className="form-row">
            <label>
              项目名称
              <input
                autoFocus
                value={form.name}
                onChange={(e) => update("name", e.target.value)}
                placeholder="例如：软件工程课程大作业"
              />
            </label>
            <label>
              项目类型
              <select
                value={form.project_type}
                onChange={(e) => update("project_type", e.target.value)}
              >
                <option>课程项目</option>
                <option>竞赛项目</option>
                <option>科研项目</option>
                <option>其他</option>
              </select>
            </label>
          </div>
          <div className="form-section-title">临时队伍</div>
          <label>
            所属班级
            <select value={classroomId} onChange={(e) => { setClassroomId(e.target.value); setMemberIds([]); }}>
              <option value="">新建一个班级成员池</option>
              {classrooms.map((item) => <option key={item.id} value={item.id}>{item.name}（{item.member_count}人）</option>)}
            </select>
          </label>
          {!classroomId && <label>新班级名称<input value={newClassroom} onChange={(e) => setNewClassroom(e.target.value)} placeholder="例如：软件工程 2026 春" /></label>}
          {classroomMembers.length > 0 && <div className="member-picker"><span>加入这次项目的人</span>{classroomMembers.map((member) => <label className="checkbox-row" key={member.user_id}><input type="checkbox" checked={memberIds.includes(member.user_id)} onChange={(e) => setMemberIds((ids) => e.target.checked ? [...new Set([...ids, member.user_id])] : ids.filter((id) => id !== member.user_id))} />{member.name}{member.user_id === currentUser?.id ? "（我）" : ""}</label>)}</div>}
          <label>
            项目简介
            <textarea
              value={form.description}
              onChange={(e) => update("description", e.target.value)}
              placeholder="说明项目目标和协作范围…"
            />
          </label>
          <div className="form-row">
            <label>
              开始日期
              <input
                type="date"
                value={form.start_date}
                onChange={(e) => update("start_date", e.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                type="date"
                value={form.end_date}
                onChange={(e) => update("end_date", e.target.value)}
              />
            </label>
          </div>
          <div className="form-section-title">创建者</div>
          <p className="modal-sub">
            当前登录用户 {currentUser?.name} 将自动成为项目负责人。
          </p>
          {error && <div className="form-error">{error}</div>}
          <div className="create-form-actions">
            {onCancel && (
              <button type="button" className="ghost-button" onClick={onCancel}>
                返回当前项目
              </button>
            )}
            <button className="primary-button create-submit" disabled={busy}>
              {busy ? "正在创建…" : "创建项目并进入工作台 →"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export { CreateProjectView };
