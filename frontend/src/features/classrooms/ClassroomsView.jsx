import { useEffect, useState } from "react";
import { getJson, sendJson, formatApiError } from "../../api/client";
import { ArrowLeft, History, Plus, ReceiptText, TimerReset, UserRound, Users } from "lucide-react";

function ClassroomsView({ currentUser, onToast, onBack }) {
  const [classrooms, setClassrooms] = useState([]);
  const [selected, setSelected] = useState(null);
  const [members, setMembers] = useState([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [history, setHistory] = useState(null);
  async function load() {
    const data = await getJson("/api/classrooms");
    setClassrooms(data.items || []);
    setSelected((current) => {
      const next = data.items?.find((item) => item.id === current?.id);
      return next || data.items?.[0] || null;
    });
  }
  useEffect(() => { load().catch((e) => onToast?.(formatApiError(e))); }, []);
  useEffect(() => { if (selected) getJson(`/api/classrooms/${selected.id}/members`).then((d) => setMembers(d.items || [])).catch((e) => onToast?.(formatApiError(e))); }, [selected]);
  async function createClassroom() {
    if (!name.trim()) return;
    try { const created = await sendJson("/api/classrooms", { method: "POST", body: JSON.stringify({ name: name.trim() }) }); setName(""); setSelected(created); await load(); onToast?.("班级已创建"); } catch (e) { onToast?.(formatApiError(e)); }
  }
  async function addMember() {
    if (!selected || !email.trim()) return;
    try { await sendJson(`/api/classrooms/${selected.id}/members`, { method: "POST", body: JSON.stringify({ email: email.trim() }) }); setEmail(""); const d = await getJson(`/api/classrooms/${selected.id}/members`); setMembers(d.items || []); onToast?.("成员已加入班级"); } catch (e) { onToast?.(formatApiError(e)); }
  }
  async function removeMember(member) {
    if (!selected || member.user_id === currentUser?.id) return;
    try { await sendJson(`/api/classrooms/${selected.id}/members/${member.user_id}`, { method: "DELETE" }); setMembers((items) => items.filter((item) => item.user_id !== member.user_id)); onToast?.("成员已退出班级成员池"); } catch (e) { onToast?.(formatApiError(e)); }
  }
  async function showHistory(member) {
    try { setHistory(await getJson(`/api/users/profile/${member.user_id}/history`)); } catch (e) { onToast?.(formatApiError(e)); }
  }
  return <div className="app-shell classroom-shell"><aside className="sidebar">
    <div className="brand"><div className="brand-mark"><ReceiptText aria-hidden="true" /></div><div><div className="brand-name">协作账本</div><div className="brand-sub">COLLAB LEDGER</div></div></div>
    <div className="workspace-label">我的工作区</div>
    <div className="project-switch"><select value="classrooms" disabled><option value="classrooms">班级成员</option></select></div>
    <nav><button className="nav-item" onClick={onBack}><ArrowLeft className="nav-icon" size={17} /> 返回项目</button><button className="nav-item"><span className="nav-icon">⌂</span> 项目总览</button><button className="nav-item"><span className="nav-icon">▦</span> 任务看板</button><button className="nav-item"><span className="nav-icon">◈</span> 任务推荐</button><button className="nav-item"><span className="nav-icon">▤</span> 贡献账本</button><button className="nav-item selected"><Users className="nav-icon" size={17} /> 班级成员</button></nav>
    <div className="sidebar-tools"><button className="side-tool" onClick={onBack}><History aria-hidden="true" /> 历史项目</button><button className="side-tool" onClick={onBack}><TimerReset aria-hidden="true" /> 今日打卡</button><button className="side-tool" onClick={onBack}><UserRound aria-hidden="true" /> 我的画像</button><button className="side-tool" onClick={onBack}><Plus aria-hidden="true" /> 新建项目</button></div>
    <div className="sidebar-bottom"><div className="profile-chip"><div className="avatar avatar-me">{(currentUser?.name || "我").slice(0, 1)}</div><div className="profile-chip-main"><strong>{currentUser?.name || "当前用户"}</strong><span>查看个人资料</span></div></div></div>
  </aside><main className="main-content"><div className="page-section classroom-page">
    <div className="page-heading"><div><button className="back-button classroom-back" onClick={onBack}><ArrowLeft size={15} /> 返回项目</button><h1>班级与成员</h1></div><div className="inline-create"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="新班级名称" /><button className="primary-button" onClick={createClassroom}>创建班级</button></div></div>
    <div className="classroom-layout"><aside className="classroom-list">{classrooms.map((item) => <button className={selected?.id === item.id ? "classroom-item selected" : "classroom-item"} key={item.id} onClick={() => { setSelected(item); setHistory(null); }}><strong>{item.name}</strong><span>{item.member_count} 位成员 · {item.project_count} 个项目</span></button>)}{!classrooms.length && <div className="empty-state">还没有班级，先创建一个成员池。</div>}</aside>
      <section className="panel classroom-members"><div className="section-head"><div><h2>{selected?.name || "选择一个班级"}</h2><span>{members.length} 位当前成员</span></div>{selected && <div className="inline-create"><input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="按邮箱添加已注册成员" /><button className="ghost-button" onClick={addMember}>添加成员</button></div>}</div>{members.map((member) => <div className="member-row" key={member.user_id}><div><strong>{member.name}</strong><span>{member.email || "未填写邮箱"} · {member.role === "owner" ? "创建者" : member.role === "teacher" ? "教师" : "学生"}</span></div><div className="row-actions"><button className="text-button" onClick={() => showHistory(member)}>查看协作履历</button>{member.user_id !== currentUser?.id && member.role !== "owner" && <button className="text-button danger" onClick={() => removeMember(member)}>退出成员池</button>}</div></div>)}{history && <div className="profile-history"><h3>{history.user.name} 的协作履历</h3><p>参与 {history.projects.length} 个项目 · 负责或参与 {history.tasks.length} 项任务 · 记录 {history.contributions.length} 条贡献</p>{history.projects.slice(0, 6).map((item) => <div key={item.id}>{item.name} · {item.status === "active" ? "进行中" : "已归档"}</div>)}</div>}</section>
    </div></div></main></div>;
}

export { ClassroomsView };
