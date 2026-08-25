import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const statusMeta = {
  unassigned: { label: '未分配', tone: 'slate' }, assigned: { label: '待开始', tone: 'blue' }, in_progress: { label: '进行中', tone: 'amber' }, paused: { label: '已暂停', tone: 'purple' }, completed: { label: '已完成', tone: 'green' }, overdue: { label: '延期', tone: 'red' }, unfinished: { label: '未完成', tone: 'red' },
}
const nav = [{ id: 'overview', icon: '⌂', label: '项目总览' }, { id: 'tasks', icon: '✓', label: '任务看板' }, { id: 'recommendations', icon: '✦', label: '任务推荐' }, { id: 'contributions', icon: '◈', label: '贡献账本' }, { id: 'report', icon: '▥', label: '贡献报告' }, { id: 'agent', icon: '✦', label: '协作 Agent' }]
const avatarColors = ['#ffe4bd', '#d7e8ff', '#d9f3e3', '#eddcff', '#ffd9e0']

let handleUnauthorized = null
async function request(url, options = {}) {
  const headers = new Headers(options.headers || {})
  if (options.body != null && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(url, { ...options, credentials: 'include', headers })
  let payload = null
  if (response.status !== 204) {
    const body = await response.text()
    if (body) { try { payload = JSON.parse(body) } catch { payload = body } }
  }
  if (!response.ok) {
    if (response.status === 401) handleUnauthorized?.()
    const error = new Error(payload?.error?.message || '请求失败，请稍后重试')
    error.status = response.status
    error.code = payload?.error?.code
    error.details = payload?.error?.details
    throw error
  }
  return response.status === 204 ? null : payload
}
function formatApiError(err) {
  const details = Array.isArray(err?.details) ? err.details.map(item => item?.message).filter(Boolean) : []
  const unique = [...new Set(details)]
  if (unique.length) return unique.join('；')
  return err?.message || '请求失败，请稍后重试'
}
function emailLooksValid(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || '').trim())
}
function getJson(url) { return request(url) }
function sendJson(url, options = {}) { return request(url, options) }
function initials(name = '') { return name.slice(0, 1) }
function formatDate(value) { if (!value) return '未设置'; const d = new Date(value); return `${d.getMonth() + 1}月${d.getDate()}日` }
function greetingStamp(date = new Date()) { return `${['周日','周一','周二','周三','周四','周五','周六'][date.getDay()]} · ${date.getMonth() + 1}月${date.getDate()}日` }
function greetingTitle(name) { const hour = new Date().getHours(); const hello = hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'; return `${hello}，${name || '同学'} 👋` }

const routePages = new Set(['overview', 'tasks', 'recommendations', 'contributions', 'report', 'agent', 'members', 'worklog', 'new'])
function readRoute() {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const parts = raw.split('/').filter(Boolean)
  if (parts[0] === 'projects' && parts[1] === 'new') return { projectId: null, page: 'new' }
  if (parts[0] === 'projects' && parts[1]) {
    const projectId = Number(parts[1])
    const page = routePages.has(parts[2]) ? parts[2] : 'overview'
    return { projectId: Number.isFinite(projectId) ? projectId : null, page }
  }
  return { projectId: null, page: 'overview' }
}
function routeHash(projectId, page = 'overview') { return projectId ? `#/projects/${projectId}/${page}` : '#/projects/new' }
function absoluteInviteUrl(invite) {
  const code = invite?.invite_code || invite?.code || invite?.token
  const route = invite?.invite_url || invite?.url || (code ? `/invite/${encodeURIComponent(code)}` : '')
  if (!route) return ''
  try { return new URL(route, window.location.origin).href } catch { return route }
}
async function copyText(value) {
  if (!value) return false
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); return true }
  const input = document.createElement('textarea')
  input.value = value; input.setAttribute('readonly', ''); input.style.position = 'fixed'; input.style.opacity = '0'
  document.body.appendChild(input); input.select()
  const copied = document.execCommand('copy'); input.remove(); return copied
}

function App() {
  const [auth, setAuth] = useState(undefined)
  const [projects, setProjects] = useState([])
  const [project, setProject] = useState(null)
  const [report, setReport] = useState(null)
  const [risks, setRisks] = useState(null)
  const [memberLoad, setMemberLoad] = useState(null)
  const [weekly, setWeekly] = useState(null)
  const [route, setRoute] = useState(() => readRoute())
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [online, setOnline] = useState(Boolean(auth))
  const [showTask, setShowTask] = useState(false)
  const [recommendTask, setRecommendTask] = useState(null)


  const [reviewTask, setReviewTask] = useState(null)
  const [toast, setToast] = useState('')
  const [query, setQuery] = useState('')

  const active = route.page
  useEffect(() => { const onHashChange = () => setRoute(readRoute()); window.addEventListener('hashchange', onHashChange); return () => window.removeEventListener('hashchange', onHashChange) }, [])
  useEffect(() => {
    handleUnauthorized = () => { setAuth(null); setProject(null); setProjects([]); setReport(null); setRisks(null); setMemberLoad(null); setWeekly(null); setLoadError(''); window.history.replaceState(null, '', '#/login'); setRoute({ projectId: null, page: 'overview' }) }
    let cancelled = false
    getJson('/api/auth/me').then(user => { if (!cancelled) setAuth(user) }).catch(error => { if (!cancelled && error.status !== 401) { setOnline(false); setAuth(null) } })
    return () => { cancelled = true; handleUnauthorized = null }
  }, [])
  useEffect(() => { if (auth) loadProject(route.projectId || undefined) }, [auth])
  useEffect(() => { if (auth && route.projectId && project?.id !== route.projectId) loadProject(route.projectId) }, [auth, route.projectId])
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(''), 2600); return () => clearTimeout(t) } }, [toast])

  async function loadProject(id) {
    setLoading(true); setLoadError('')
    try {
      const listPayload = await getJson('/api/projects?page_size=100')
      const list = listPayload?.items || []
      setProjects(list)
      const rememberedId = Number(localStorage.getItem('collab_project_id'))
      const selectedId = id || (list.some(item => item.id === rememberedId) ? rememberedId : null) || list[0]?.id
      if (!selectedId) {
        setProject(null); setReport(null); setRisks(null); setMemberLoad(null); setWeekly(null); setOnline(true)
        if (window.location.hash !== '#/projects/new') window.history.replaceState(null, '', routeHash(null, 'new'))
        setRoute({ projectId: null, page: 'new' })
        return
      }
      const [detail, membersPayload, tasksPayload, contributionsPayload] = await Promise.all([
        getJson(`/api/projects/${selectedId}`),
        getJson(`/api/projects/${selectedId}/members`),
        getJson(`/api/projects/${selectedId}/tasks?page_size=100`),
        getJson(`/api/projects/${selectedId}/contributions?page_size=100`),
      ])
      const [rep, riskPayload, loadPayload, weeklyPayload] = await Promise.all([
        getJson(`/api/projects/${selectedId}/report`).catch(error => { if (error.status === 401) throw error; return null }),
        getJson(`/api/projects/${selectedId}/risks`).catch(error => { if (error.status === 401) throw error; return null }),
        getJson(`/api/projects/${selectedId}/members/load`).catch(error => { if (error.status === 401) throw error; return null }),
        getJson(`/api/projects/${selectedId}/weekly-report`).catch(error => { if (error.status === 401) throw error; return null }),
      ])
      const loadByUser = Object.fromEntries((loadPayload?.members || []).map(item => [item.user_id, item]))
      const members = (membersPayload?.items || []).map(member => ({ ...member, id: member.user_id, ...(loadByUser[member.user_id] || {}) }))
      const names = Object.fromEntries(members.map(member => [member.user_id, member.name]))
      const tasks = (tasksPayload?.items || []).map(task => ({ ...task, assignee_name: task.assignee_name || names[task.assignee_id] || null }))
      const contributions = contributionsPayload?.items || []
      setProject({ ...detail, members, tasks, contributions })
      setReport(rep); setRisks(riskPayload); setMemberLoad(loadPayload); setWeekly(weeklyPayload); setOnline(true); localStorage.setItem('collab_project_id', String(selectedId))
      if (route.projectId !== selectedId) {
        const page = route.page === 'new' ? 'overview' : route.page
        window.history.replaceState(null, '', routeHash(selectedId, page))
        setRoute({ projectId: selectedId, page })
      }
    } catch (error) {
      if (error.status === 401) return
      setOnline(false); setLoadError(error.message); setToast(error.message)
    } finally { setLoading(false) }
  }

  async function logout() { try { await sendJson('/api/auth/logout', { method: 'POST' }) } catch (error) { if (error.status !== 401) setToast(error.message) } finally { setAuth(null); setProject(null); setProjects([]); setReport(null); setRisks(null); setMemberLoad(null); setWeekly(null); window.history.replaceState(null, '', '#/login'); setRoute({ projectId: null, page: 'overview' }) } }
  function navigate(page, projectId = project?.id, replace = false) { const hash = routeHash(projectId, page); if (replace) { window.history.replaceState(null, '', hash); setRoute({ projectId, page }); return } if (window.location.hash === hash) { setRoute({ projectId, page }); return } window.location.hash = hash }
  async function afterProject(created) { navigate('overview', created.id); await loadProject(created.id) }
  const tasks = project?.tasks || []
  const members = project?.members || []
  const role = project?.current_user_role || projects.find(item => item.id === project?.id)?.role
  const isOwner = role === 'owner'
  const canWrite = project?.status !== 'archived' && (role === 'owner' || role === 'member')
  const canAssign = canWrite
  const canManageTask = task => canWrite && (isOwner || task.assignee_id === auth?.id)
  const activeTasks = tasks.filter(t => ['assigned', 'in_progress', 'paused'].includes(t.status))
  const completed = project?.statistics?.completed_task_count ?? tasks.filter(t => t.status === 'completed').length
  const overdue = project?.statistics?.overdue_task_count ?? tasks.filter(t => ['overdue', 'unfinished'].includes(t.status)).length
  const progress = project?.statistics?.progress ?? (tasks.length ? Math.round(completed / tasks.length * 100) : 0)
  const memberStats = useMemo(() => members.map(m => ({ ...m, current: m.current_task_count ?? tasks.filter(t => t.assignee_id === m.user_id && ['assigned', 'in_progress', 'paused'].includes(t.status)).length, done: tasks.filter(t => t.assignee_id === m.user_id && t.status === 'completed').length, quality: (() => { const q = tasks.filter(t => t.assignee_id === m.user_id && t.quality != null).map(t => t.quality); return q.length ? (q.reduce((a, b) => a + b, 0) / q.length).toFixed(1) : '—' })() })), [members, tasks])

  if (auth === undefined) return <div className="auth-screen"><div className="loading-state">正在恢复登录状态…</div></div>
  if (!auth) return <AuthView onAuthenticated={user => setAuth(user)} />
  if (!loading && loadError && !project) return <div className="app-error-screen"><div><div className="brand-mark">!</div><h1>项目加载失败</h1><p>{loadError}</p><button className="primary-button" onClick={() => loadProject(route.projectId || undefined)}>重新加载</button></div></div>
  if (!loading && (!project || route.page === 'new')) return <CreateProjectView currentUser={auth} onCreated={afterProject} onCancel={project ? () => navigate('overview', project.id, true) : undefined} />

  async function taskAction(task, action) {
    try { const updated = await sendJson(`/api/tasks/${task.id}/${action}`, { method: 'POST',  }); setProject(p => ({ ...p, tasks: p.tasks.map(t => t.id === task.id ? { ...t, ...updated } : t) })); setToast(`已将「${task.title}」标记为${statusMeta[updated.status]?.label || '已更新'}`); if (action === 'complete' && isOwner) setReviewTask({ ...task, ...updated }); await loadProject(project.id) } catch (error) { setToast(error.message) }
  }
  async function createTask(data) { try { const t = await sendJson(`/api/projects/${project.id}/tasks`, { method: 'POST', body: JSON.stringify(data) }); setProject(p => ({ ...p, tasks: [t, ...p.tasks] })); setToast('任务已创建') } catch (error) { setToast(error.message) } setShowTask(false) }
  async function changeProject(id) { navigate('overview', id); await loadProject(id) }

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-mark">▦</div><div><div className="brand-name">协作账本</div><div className="brand-sub">COLLAB LEDGER</div></div></div><div className="workspace-label">我的工作区</div><div className="project-switch"><select value={project?.id || ''} onChange={e => changeProject(Number(e.target.value))}>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div><nav>{nav.map(item => <button key={item.id} className={`nav-item ${active === item.id ? 'selected' : ''}`} onClick={() => navigate(item.id)}><span className="nav-icon">{item.icon}</span>{item.label}{item.id === 'tasks' && overdue > 0 && <span className="nav-badge">{overdue}</span>}</button>)}</nav><div className="sidebar-tools">{isOwner && <button className="side-tool" onClick={() => navigate('members')}>♧ 成员管理</button>}{canWrite && <button className="side-tool" onClick={() => navigate('worklog')}>◷ 今日打卡</button>}<button className="side-tool" onClick={() => navigate('new')}>＋ 新建项目</button></div><div className="sidebar-bottom"><div className="privacy-card"><span className="privacy-icon">◉</span><div><strong>隐私友好设计</strong><p>只记录协作成果，不监控个人设备。</p></div></div><button className="profile-chip" onClick={logout}><div className="avatar avatar-me">{initials(auth.name)}</div><div><strong>{auth.name}</strong><span>退出登录</span></div><span className="more">•••</span></button></div></aside>
    <main className="main-content"><header className="topbar"><div className="breadcrumb">{active !== 'overview' && <button className="back-button" onClick={() => window.history.back()}>← 返回</button>}<span>项目空间</span><b>/</b><strong>{nav.find(n => n.id === active)?.label || ({ members: '成员管理', worklog: '今日打卡', new: '新建项目', recommendations: '任务推荐' }[active] || '项目总览')}</strong></div><div className="top-actions"><div className="search"><span>⌕</span><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索任务、成员…"/></div><button className="icon-button" title="通知">♢<i/></button><button className="help-button">?</button></div></header><div className="page-wrap">{loading ? <div className="loading-state">正在加载项目空间…</div> : <>{active === 'overview' && <Overview auth={auth} project={project} memberStats={memberStats} tasks={tasks} progress={progress} completed={completed} overdue={overdue} activeTasks={activeTasks} online={online} risks={risks} weekly={weekly} memberLoad={memberLoad} onNavigate={navigate} onAction={taskAction} onRecommend={setRecommendTask} canWrite={canWrite} canManageTask={canManageTask}/>} {active === 'tasks' && <TasksView tasks={tasks.filter(t => !query || `${t.title}${t.assignee_name || ''}`.includes(query))} members={members} onAction={taskAction} onCreate={() => setShowTask(true)} onRecommend={setRecommendTask} onBatch={() => navigate('recommendations')} canWrite={canWrite} canManageTask={canManageTask}/>} {active === 'recommendations' && <RecommendationsView project={project} members={members} canWrite={canWrite} onRecommend={setRecommendTask} onToast={setToast} setProject={setProject} onReload={() => loadProject(project.id)}/>} {active === 'contributions' && <ContributionsView project={project} members={members} currentUser={auth} role={role} canWrite={canWrite} online={online} setProject={setProject} onToast={setToast}/>} {active === 'report' && <ReportView project={project} report={report} memberStats={memberStats} tasks={tasks} weekly={weekly} risks={risks}/>} {active === 'agent' && <AgentView project={project} tasks={tasks} online={online} onRecommend={setRecommendTask}/>}{active === 'members' && isOwner && <MembersModal project={project} currentUser={auth} onClose={() => navigate('overview', project.id, true)} onUpdated={detail => setProject(p => ({ ...p, members: detail.members || p.members }))} onToast={setToast}/>} {active === 'worklog' && canWrite && <WorklogModal project={project} tasks={tasks} role={role} user={auth} onClose={() => navigate('overview', project.id, true)} onToast={setToast}/>}</>}</div></main>
    {showTask && canWrite && <TaskModal members={members} project={project} onClose={() => setShowTask(false)} onSave={createTask} onToast={setToast}/>} {recommendTask && canWrite && <RecommendModal task={recommendTask} project={project} members={members} onClose={() => setRecommendTask(null)} onToast={setToast} setProject={setProject}/>} {reviewTask && <QualityReviewModal project={project} task={reviewTask} currentUser={auth} members={members} onClose={() => setReviewTask(null)} onSaved={quality => { setProject(p => ({ ...p, tasks: p.tasks.map(t => t.id === reviewTask.id ? { ...t, quality } : t) })); setToast('质量评价已记录') }} />} {toast && <div className="toast">✓ {toast}</div>}
  </div>
}

function PageTitle({ eyebrow, title, desc, action }) { return <div className="page-title"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{desc && <p>{desc}</p>}</div>{action}</div> }
function Overview({ auth, project, memberStats, tasks, progress, completed, overdue, activeTasks, online, risks, weekly, memberLoad, onNavigate, onAction, onRecommend, canWrite, canManageTask }) {
  const riskCount = risks?.count ?? (risks?.risks || []).length
  const topRisk = (risks?.risks || [])[0]
  const highLoad = (memberLoad?.members || []).filter(item => item.load_level === 'high')
  const weeklyHint = weekly ? `本周完成 ${weekly.summary?.tasks_completed || 0} 项，打卡 ${weekly.summary?.checkin_count || 0} 次` : '周报数据加载中'
  return <><PageTitle eyebrow={greetingStamp()} title={greetingTitle(auth?.name)} desc="这是你们小组本周的协作脉搏。推荐仅供参考，最终由组长决定。" action={canWrite ? <button className="primary-button" onClick={() => onNavigate('tasks')}><span>＋</span> 新建任务</button> : null}/><div className="overview-grid"><section className="hero-card"><div className="hero-copy"><span className="live-pill"><i/> 项目进行中</span><h2>{project.name}</h2><p>{project.description || '让每一份协作成果都被看见。'}</p><div className="hero-meta"><span>◷ {formatDate(project.start_date)} — {formatDate(project.end_date)}</span><span>♧ {memberStats.length} 位成员</span></div></div><div className="progress-ring" style={{ '--progress': `${progress * 3.6}deg` }}><div><strong>{progress}%</strong><span>项目进度</span></div></div></section>
      <div className="metric-row"><Metric label="进行中任务" value={activeTasks.length} hint={weeklyHint} trend="neutral" color="amber"/><Metric label="已完成" value={completed} hint={`共 ${tasks.length} 项任务`} trend="neutral" color="green"/><Metric label="需要关注" value={riskCount} hint={topRisk?.message || '暂无明显风险'} trend={riskCount ? 'down' : 'neutral'} color="red"/></div>
    </div>
    <div className="section-heading"><div><h2>协作状态</h2><p>成员主动同步的工作状态</p></div><button className="text-button" onClick={() => onNavigate('contributions')}>查看贡献账本 →</button></div>
    <div className="member-grid">{memberStats.map((m, i) => <MemberCard key={m.id} member={m} index={i}/>)}</div>
    <div className="dashboard-columns"><section className="panel task-panel"><div className="panel-header"><div><h2>需要你关注的任务</h2><p>按优先级和截止时间排序</p></div><button className="text-button" onClick={() => onNavigate('tasks')}>查看全部 →</button></div><div className="task-list">{tasks.filter(t => t.status !== 'completed').slice(0, 4).map(t => <TaskRow key={t.id} task={t} onAction={onAction} onRecommend={canWrite ? onRecommend : null} canManageTask={canManageTask}/>)}</div></section><section className="panel insight-panel"><div className="panel-header"><div><h2>本周协作洞察</h2><p>基于已有事实生成的提示</p></div><span className="sparkle">✦</span></div><div className="insight-main"><div className="insight-icon">↗</div><div><strong>{topRisk ? '需要优先处理风险' : '当前节奏平稳'}</strong><p>{topRisk ? `${topRisk.message}。${topRisk.rule || ''}` : (weekly?.disclaimer || '周报与风险均基于真实任务、打卡和贡献数据。')}</p></div></div><div className="insight-line"><span>风险提示</span><strong>{riskCount ? `共 ${riskCount} 条风险` : '暂无明显风险'}{highLoad.length ? ` · 高负载 ${highLoad.map(item => item.name).join('、')}` : ''}</strong><button onClick={() => onNavigate('agent')}>让 Agent 分析</button></div><div className="activity-mini"><span className="activity-dot green"/><span>最近一次活动</span><strong>{online ? '数据已与 API 同步' : 'API 暂未连接'}</strong></div></section></div>
  </>
}
function Metric({ label, value, hint, trend, color }) { return <div className={`metric-card ${color}`}><div className="metric-top"><span>{label}</span><span className={`trend ${trend}`}>{trend === 'up' ? '↗' : trend === 'down' ? '↘' : '—'}</span></div><strong>{value}</strong><small>{hint}</small></div> }
function MemberCard({ member, index }) { const status = { online: ['协作中', 'online'], busy: ['专注中', 'busy'], away: ['暂离', 'away'], offline: ['离线', 'offline'] }[member.status] || ['协作中', 'online']; const loadLevel = member.load_level || (member.current >= member.max_concurrent_tasks ? 'high' : member.current / member.max_concurrent_tasks > 0.5 ? 'normal' : 'low'); return <div className="member-card"><div className="member-head"><div className={`avatar avatar-${index % avatarColors.length}`} style={{ background: avatarColors[index % avatarColors.length] }}>{initials(member.name)}</div><div><strong>{member.name}</strong><span className={`status ${status[1]}`}><i/>{status[0]}</span></div><span className={`load-pill ${loadLevel}`}>{member.load_label || ({ low: '低负载', normal: '正常', high: '高负载' }[loadLevel])}</span></div><div className="member-skills">{(member.skills || []).slice(0, 3).map(s => <span key={s}>{s}</span>)}</div><div className="member-foot"><span>当前任务 <b>{member.current}/{member.max_concurrent_tasks}</b></span><span>本周完成 <b>{member.done}</b></span></div><div className={`load-bar ${loadLevel}`}><i style={{ width: `${Math.min(100, member.current / Math.max(1, member.max_concurrent_tasks) * 100)}%` }}/></div></div> }
function TaskRow({ task, onAction, onRecommend, canManageTask }) { const meta = statusMeta[task.status] || statusMeta.unassigned; const action = task.status === 'in_progress' ? 'complete' : task.status === 'paused' ? 'resume' : task.status === 'assigned' ? 'start' : null; return <div className="task-row"><div className={`priority-dot ${meta.tone}`}/><div className="task-row-main"><strong>{task.title}</strong><div className="task-row-meta"><span className={`tag ${meta.tone}`}>{meta.label}</span><span>截止 {formatDate(task.due_date)}</span>{task.assignee_name ? <span className="assignee-inline"><span className="tiny-avatar">{initials(task.assignee_name)}</span>{task.assignee_name}</span>  : (onRecommend ? <button className="assign-link" onClick={() => onRecommend(task)}>＋ 分配负责人</button> : <span>尚未分配</span>)}</div></div>{action && canManageTask?.(task) && <button className="row-action" onClick={() => onAction(task, action)}>{action === 'complete' ? '完成' : action === 'resume' ? '继续' : '开始'}</button>}<button className="row-more">···</button></div> }

function TasksView({ tasks, members, onAction, onCreate, onRecommend, onBatch, canWrite, canManageTask }) {
  const [filter, setFilter] = useState('all')
  const filtered = filter === 'all' ? tasks : tasks.filter(t => t.status === filter)
  const columns = ['unassigned', 'assigned', 'in_progress', 'paused', 'completed', 'overdue', 'unfinished']
  const unassignedCount = tasks.filter(t => t.status === 'unassigned' || !t.assignee_id).length
  return <>
    <PageTitle eyebrow="TASK BOARD" title="任务看板" desc="从开始到完成，记录每一段真实的协作过程。" action={canWrite ? <div className="title-actions"><button className="ghost-button" onClick={onBatch}>✦ 一键建议未分配</button><button className="primary-button" onClick={onCreate}>＋ 新建任务</button></div> : null}/>
    <div className="board-toolbar"><div className="filter-tabs">{[['all', '全部'], ['in_progress', '进行中'], ['unassigned', '待分配'], ['completed', '已完成'], ['overdue', '延期']].map(([id, label]) => <button className={filter === id ? 'active' : ''} key={id} onClick={() => setFilter(id)}>{label}<span>{id === 'all' ? tasks.length : tasks.filter(t => t.status === id).length}</span></button>)}</div><div className="board-actions">{canWrite && unassignedCount > 0 && <button className="ghost-button" onClick={onBatch}>未分配 {unassignedCount} 项可出建议</button>}<button className="ghost-button">≡ 筛选</button><button className="ghost-button">↕ 排序</button></div></div>
    <div className="board-grid">{columns.map(status => { const list = filtered.filter(t => t.status === status); const m = statusMeta[status]; return <div className="board-column" key={status}><div className="column-header"><span className={`column-dot ${m.tone}`}/><strong>{m.label}</strong><span className="column-count">{list.length}</span>{status === 'unassigned' && canWrite && <button onClick={onBatch}>✦</button>}{canWrite && <button onClick={onCreate}>＋</button>}</div><div className="column-cards">{list.map(t => <TaskCard key={t.id} task={t} members={members} onAction={onAction} onRecommend={canWrite ? onRecommend : null} canManageTask={canManageTask}/>)}{!list.length && <div className="empty-column">暂无任务</div>}</div></div> })}</div>
  </>
}
function TaskCard({ task, onAction, onRecommend, canManageTask }) { const m = statusMeta[task.status]; const action = task.status === 'in_progress' ? 'complete' : task.status === 'assigned' ? 'start' : task.status === 'paused' ? 'resume' : null; return <article className="task-card"><div className="task-card-top"><span className={`tag ${m.tone}`}>{m.label}</span><button className="kebab">•••</button></div><h3>{task.title}</h3>{task.description && <p>{task.description}</p>}<div className="task-card-info"><span>◷ {formatDate(task.due_date)}</span><span>◒ {task.estimated_hours || '—'}h</span></div><div className="task-card-bottom">{task.assignee_name ? <span className="assignee-inline"><span className="tiny-avatar">{initials(task.assignee_name)}</span>{task.assignee_name}</span>  : (onRecommend ? <button className="assign-link" onClick={() => onRecommend(task)}>＋ 分配负责人</button> : <span>尚未分配</span>)}{action && canManageTask?.(task) && <button className="mini-action" onClick={() => onAction(task, action)}>{action === 'complete' ? '完成任务' : action === 'resume' ? '继续' : '开始'}</button>}</div></article> }

function ContributionsView({ project, members, currentUser, role, canWrite, online, setProject, onToast }) { const [kind, setKind] = useState('all'); const [open, setOpen] = useState(false); const contributions = project.contributions || []; const list = kind === 'all' ? contributions : contributions.filter(c => c.kind === kind); async function save(data) { try { const c = await sendJson(`/api/projects/${project.id}/contributions`, { method: 'POST', body: JSON.stringify(data) }); setProject(p => ({ ...p, contributions: [c, ...(p.contributions || [])] })); onToast('贡献记录已保存') } catch (error) { onToast(error.message) } setOpen(false) } return <><PageTitle eyebrow="CONTRIBUTION LEDGER" title="贡献账本" desc="记录做了什么，而不是监控正在做什么。" action={canWrite ? <button className="primary-button" onClick={() => setOpen(true)}>＋ 记录贡献</button> : null}/><div className="privacy-banner"><span>◉</span><div><strong>这是一个公平秤，不是监控器</strong><p>成员只会被记录项目相关的产出：任务、代码、文档和会议。数据默认仅对项目组可见。</p></div><span className="sync-state">{online ? '● 已同步' : '◌ API 未连接'}</span></div><div className="ledger-summary"><Metric label="本周贡献" value={contributions.length} hint="条可追溯记录" trend="up" color="blue"/><Metric label="代码提交" value={contributions.filter(c => c.kind === 'code').reduce((a, c) => a + (c.quantity || 1), 0)} hint="次 commit / 变更" trend="up" color="purple"/><Metric label="活跃成员" value={new Set(contributions.map(c => c.user_id)).size} hint={`共 ${members.length} 位成员`} trend="neutral" color="green"/></div><div className="ledger-panel panel"><div className="ledger-header"><div className="filter-tabs compact">{[['all', '全部'], ['code', '代码'], ['document', '文档'], ['meeting', '会议'], ['research', '调研'], ['test', '测试'], ['design', '设计']].map(([id, label]) => <button key={id} className={kind === id ? 'active' : ''} onClick={() => setKind(id)}>{label}</button>)}</div><span className="ledger-note">按时间倒序</span></div><div className="ledger-list">{list.map((c, i) => <ContributionItem key={c.id} c={c} i={i}/>)}{!list.length && <div className="empty-state">还没有这类贡献记录</div>}</div></div>{open && <ContributionModal members={role === 'owner' ? members : members.filter(m => m.user_id === currentUser.id)} onClose={() => setOpen(false)} onSave={save}/>}</> }
function ContributionItem({ c, i }) { const icons = { code: ['⌘', 'purple'], document: ['▤', 'blue'], meeting: ['◉', 'amber'], task: ['✓', 'green'], other: ['✦', 'slate'] }; const [icon, tone] = icons[c.kind] || icons.other; return <div className="contribution-item"><div className={`contribution-icon ${tone}`}>{icon}</div><div className="contribution-main"><strong>{c.title || '未命名贡献'}</strong><p>{c.description || '成员提交了一条项目产出记录'}</p><span>{c.user_name} · {formatDate(c.created_at)}</span></div><div className="contribution-qty"><strong>{c.quantity || 1}</strong><span>{c.kind === 'code' ? '次' : '项'}</span></div></div> }

function ReportView({ project, report, memberStats, tasks, weekly, risks }) { const rows = report?.members || memberStats.map(m => ({ user_id: m.id, name: m.name, tasks_total: tasks.filter(t => t.assignee_id === m.user_id).length, tasks_completed: m.done, tasks_overdue: tasks.filter(t => t.assignee_id === m.user_id && ['overdue', 'unfinished'].includes(t.status)).length, average_quality: m.quality === '—' ? null : Number(m.quality), actual_hours: tasks.filter(t => t.assignee_id === m.user_id).reduce((a, t) => a + (t.actual_hours || 0), 0) })); const total = report?.overall?.tasks_total ?? tasks.length; const done = report?.overall?.tasks_completed ?? tasks.filter(t => t.status === 'completed').length; async function exportReport() { window.open(`/api/projects/${project.id}/report/export?format=markdown`, '_blank') } return <><PageTitle eyebrow="PROJECT REPORT" title="项目贡献报告" desc="用事实回顾协作过程，为组内互评提供依据。" action={<button className="ghost-button" onClick={exportReport}>⇩ 导出报告</button>}/><div className="report-highlight"><div><span className="eyebrow">PROJECT PULSE</span><h2>小组整体进度</h2><p>截至今天，项目已完成 {done} / {total} 项任务。</p></div><div className="big-progress"><strong>{total ? Math.round(done / total * 100) : 0}%</strong><div><div className="progress-track"><i style={{ width: `${total ? done / total * 100 : 0}%` }}/></div><span>整体完成度</span></div></div></div>
    <div className="stage2-grid">
      <section className="panel"><div className="panel-header"><div><h2>本周周报</h2><p>{weekly?.period ? `${weekly.period.start_date} 至 ${weekly.period.end_date}` : '基于真实任务、打卡和贡献'}</p></div></div><ul className="fact-list"><li>完成 {weekly?.summary?.tasks_completed ?? 0} 项</li><li>进行中 {weekly?.summary?.tasks_in_progress ?? 0} 项</li><li>延期 {weekly?.summary?.tasks_overdue ?? 0} 项</li><li>打卡 {weekly?.summary?.checkin_count ?? 0} 次 / {weekly?.summary?.actual_hours ?? 0} 小时</li></ul><p className="muted-note">{weekly?.disclaimer || '周报不虚构事实。'}</p></section>
      <section className="panel"><div className="panel-header"><div><h2>项目风险</h2><p>{risks?.rule || '延期、临近截止、无负责人和高负载'}</p></div></div><div className="risk-list">{(risks?.risks || []).length ? (risks.risks || []).map((item, index) => <div className={`risk-item ${item.level}`} key={`${item.type}-${index}`}><strong>{item.message}</strong><span>{item.rule}</span></div>) : <div className="empty-state">暂无明显风险</div>}</div></section>
    </div>
    <div className="report-grid"><section className="panel"><div className="panel-header"><div><h2>成员贡献概览</h2><p>不形成公开排名，仅用于项目复盘</p></div><span className="sparkle">✦</span></div><div className="report-table"><div className="report-row report-head"><span>成员</span><span>完成任务</span><span>延期</span><span>平均质量</span><span>实际耗时</span></div>{rows.map(r => <div className="report-row" key={r.user_id}><span className="assignee-inline"><span className="tiny-avatar">{initials(r.name)}</span><strong>{r.name}</strong></span><span>{r.tasks_completed} / {r.tasks_total}</span><span className={r.tasks_overdue ? 'danger-text' : ''}>{r.tasks_overdue}</span><span>{r.average_quality ? <><b>{r.average_quality}</b><small> / 5</small></> : '—'}</span><span>{r.actual_hours || 0}h</span></div>)}</div></section><section className="panel report-insight"><div className="panel-header"><div><h2>本周成员产出</h2><p>来自任务完成、打卡和确认贡献</p></div></div><div className="weekly-member-list">{(weekly?.members || rows).map(item => <div className="weekly-member-row" key={item.user_id}><strong>{item.name}</strong><span>完成 {item.completed_tasks ?? item.tasks_completed ?? 0} 项 · 工时 {item.actual_hours || 0}h</span></div>)}</div><p className="muted-note">画像与跨项目推荐属于阶段四，当前只展示本项目事实。</p></section></div></> }
function SkillBar({ label, value, color }) { return <div className="skill-bar"><div><span>{label}</span><b>{value}%</b></div><div className="progress-track"><i className={color} style={{ width: `${value}%` }}/></div></div> }

function AgentView({ project, tasks = [], online, onRecommend }) {
  const unassigned = tasks.find(task => !task.assignee_id) || tasks[0]
  const [messages, setMessages] = useState([{ role: 'agent', text: '你好，我是协作 Agent。可以帮你分析项目风险、生成周报，或推荐任务负责人。' }])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  async function ask(text = input) {
    if (!text.trim() || busy) return
    setMessages(items => [...items, { role: 'user', text }]); setInput(''); setBusy(true)
    try {
      const result = await sendJson(`/api/projects/${project.id}/agent/chat`, { method: 'POST', body: JSON.stringify({ message: text, session_id: 'default' }) })
      const generated = result.generated_at ? new Date(result.generated_at).toLocaleString() : ''
      const meta = [result.source === 'fallback' ? '规则兜底' : 'AI 生成', generated].filter(Boolean).join(' · ')
      setMessages(items => [...items, { role: 'agent', text: result.answer, meta, warning: result.llm_error ? 'LLM 服务暂不可用，以上为规则兜底结果。' : '' }])
    } catch (error) {
      setMessages(items => [...items, { role: 'agent', text: `请求失败：${error.message}` }])
    } finally { setBusy(false) }
  }
  return <><PageTitle eyebrow="COLLAB AGENT" title="协作 Agent" desc="把项目事实变成可执行的下一步建议。" action={<span className={`api-pill ${online ? 'connected' : ''}`}><i/> {online ? 'API 已连接' : 'API 未连接'}</span>}/><div className="agent-layout"><section className="agent-chat panel"><div className="agent-chat-head"><div className="agent-face">✦</div><div><strong>协作 Agent</strong><span>基于项目事实回答，不替你做最终判断</span></div><span className="agent-online"><i/> 在线</span></div><div className="messages">{messages.map((message, index) => <div key={index} className={`message ${message.role}`}><div className="message-avatar">{message.role === 'agent' ? '✦' : '我'}</div><div className="message-bubble">{message.text}{message.meta && <small>{message.meta}</small>}{message.warning && <small>{message.warning}</small>}</div></div>)}{busy && <div className="message agent"><div className="message-avatar">✦</div><div className="message-bubble typing"><i/><i/><i/></div></div>}</div><div className="suggestions"><button onClick={() => ask('目前项目最大的风险是什么？')}>⌁ 目前项目最大的风险是什么？</button><button onClick={() => ask('帮我总结一下这周我们组的工作')}>◷ 总结本周工作</button><button onClick={() => unassigned ? onRecommend(unassigned) : ask('这个任务应该给谁？')}>✦ 谁适合做未分配任务？</button></div><div className="agent-input"><input value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => event.key === 'Enter' && ask()} placeholder="输入你想了解的项目问题…"/><button onClick={() => ask()}>发送 ↑</button></div></section><aside className="agent-side"><div className="panel"><div className="panel-header"><div><h2>可询问 Agent</h2><p>试试这些问题</p></div></div><div className="question-list"><button onClick={() => ask('目前项目最大的风险是什么？')}>目前项目最大的风险是什么？<span>→</span></button><button onClick={() => ask('帮我总结一下这周我们组的工作')}>帮我总结一下这周我们组的工作<span>→</span></button><button onClick={() => unassigned ? onRecommend(unassigned) : ask('这个任务应该给谁？')}>这个任务应该给谁？<span>→</span></button></div></div><div className="agent-note"><span>◎</span><div><strong>AI 只提供建议</strong><p>推荐基于成员主动提供的技能、任务历史和当前负载。最终决定权始终在组长和成员手中。</p></div></div></aside></div></>
}

function TaskModal({ members, project, onClose, onSave, onToast }) {
  const [form, setForm] = useState({ title: '', description: '', task_type: '其他', estimated_hours: 4, due_date: '', assignee_id: '' })
  const [preview, setPreview] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))
  async function previewRecommend() {
    if (!form.title.trim()) { onToast('请先填写任务名称，再查看建议'); return }
    setPreviewBusy(true)
    try {
      const query = new URLSearchParams({ task_name: form.title.trim(), task_type: form.task_type || '', estimated_hours: String(form.estimated_hours || 1) })
      setPreview(await getJson(`/api/projects/${project.id}/recommendations?${query}`))
    } catch (reason) { onToast(reason.message) } finally { setPreviewBusy(false) }
  }
  return <div className="modal-backdrop"><div className="modal recommend-modal"><div className="modal-head"><div><span className="eyebrow">NEW TASK</span><h2>创建新任务</h2></div><button onClick={onClose}>×</button></div><label>任务名称<input autoFocus value={form.title} onChange={e => update('title', e.target.value)} placeholder="例如：完成项目 PPT"/></label><label>任务描述<textarea value={form.description} onChange={e => update('description', e.target.value)} placeholder="补充任务背景和交付标准…"/></label><div className="form-row"><label>任务类型<select value={form.task_type} onChange={e => update('task_type', e.target.value)}><option>其他</option><option>前端</option><option>后端</option><option>数据库</option><option>文档</option><option>汇报</option></select></label><label>预计耗时（小时）<input type="number" min="0" value={form.estimated_hours} onChange={e => update('estimated_hours', Number(e.target.value))}/></label></div><div className="form-row"><label>截止日期<input type="date" value={form.due_date} onChange={e => update('due_date', e.target.value)}/></label><label>负责人（可稍后分配）<select value={form.assignee_id} onChange={e => update('assignee_id', e.target.value)}><option value="">暂不分配</option>{members.map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</select></label></div>
    <div className="title-actions"><button className="ghost-button" disabled={previewBusy} onClick={previewRecommend}>{previewBusy ? '计算中…' : '先看建议，不自动指派'}</button></div>
    {preview && <div className="recommend-preview"><p className="muted-note">{preview.disclaimer}</p>{(preview.recommendations || []).map(item => <RecommendCard key={item.user_id} item={item} selected={String(form.assignee_id) === String(item.user_id)} onSelect={id => update('assignee_id', String(id))} onAccept={rec => { update('assignee_id', String(rec.user_id)); onToast(`已预选 ${rec.name}，仍需点击创建任务`) }}/>)}{preview.excluded?.length ? <div className="excluded-box">{preview.excluded.map(item => <p key={item.user_id}>{item.name}：{item.reason}</p>)}</div> : null}</div>}
    <div className="modal-actions"><button className="ghost-button" onClick={onClose}>取消</button><button className="primary-button" disabled={!form.title.trim()} onClick={() => onSave({ ...form, assignee_id: form.assignee_id ? Number(form.assignee_id) : null, estimated_hours: Number(form.estimated_hours) })}>创建任务</button></div></div></div>
}
function ContributionModal({ members, onClose, onSave }) { const [form, setForm] = useState({ user_id: members[0]?.id || '', kind: 'code', title: '', description: '', quantity: 1 }); return <div className="modal-backdrop"><div className="modal"><div className="modal-head"><div><span className="eyebrow">LOG CONTRIBUTION</span><h2>记录一条贡献</h2></div><button onClick={onClose}>×</button></div><div className="form-row"><label>成员<select value={form.user_id} onChange={e => setForm(f => ({ ...f, user_id: Number(e.target.value) }))}>{members.map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</select></label><label>贡献类型<select value={form.kind} onChange={e => setForm(f => ({ ...f, kind: e.target.value }))}><option value="code">代码 / Commit</option><option value="document">文档</option><option value="meeting">会议</option><option value="research">调研</option><option value="test">测试</option><option value="design">设计</option><option value="other">其他</option></select></label></div><label>贡献标题<input autoFocus value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder="例如：完成 API 鉴权模块"/></label><label>补充说明<textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} placeholder="写下可被复盘的事实…"/></label><label>数量 / 次数<input type="number" min="1" value={form.quantity} onChange={e => setForm(f => ({ ...f, quantity: Number(e.target.value) }))}/></label><div className="modal-actions"><button className="ghost-button" onClick={onClose}>取消</button><button className="primary-button" disabled={!form.title.trim()} onClick={() => onSave(form)}>保存记录</button></div></div></div> }

function dimLabel(key) { return { skill: '技能', quality: '质量', efficiency: '效率', load: '负载' }[key] || key }
function RecommendCard({ item, selected, onSelect, onAccept }) {
  const dims = item.dimensions || {}
  return <div className={`recommend-item ${selected ? 'selected' : ''}`} onClick={() => onSelect(item.user_id)}>
    <div className="avatar avatar-0">{initials(item.name)}</div>
    <div className="recommend-main">
      <div className="recommend-name"><strong>{item.name}</strong><span className="score">匹配度 {Math.round(item.score)}<small>%</small></span></div>
      <p className="recommend-summary">{item.reasons?.summary}</p>
      <div className="dimension-grid">{['skill','quality','efficiency','load'].map(key => { const dim = dims[key] || {}; return <div className="dimension-row" key={key}><span>{dimLabel(key)}</span><div className="match-track"><i style={{ width: `${Math.round((dim.score || 0) * 100)}%` }}/></div><b>{Math.round((dim.score || 0) * 100)}</b></div> })}</div>
      {item.reasons?.evidence && <ul className="reason-evidence">{item.reasons.evidence.map(line => <li key={line}>{line}</li>)}</ul>}
    </div>
    {onAccept && <button className="choose-button" onClick={event => { event.stopPropagation(); onAccept(item) }}>采纳</button>}
  </div>
}
function RecommendModal({ task, project, members, onClose, onToast, setProject }) {
  const [payload, setPayload] = useState(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('')
  const assignable = (members || []).filter(member => member.role === 'member')
  useEffect(() => { (async () => { try { const query = task.id ? new URLSearchParams({ task_id: String(task.id) }) : new URLSearchParams({ task_name: task.title, task_type: task.task_type || '', estimated_hours: String(task.estimated_hours || 1) }); const response = await getJson(`/api/projects/${project.id}/recommendations?${query}`); setPayload(response); setSelected(response.recommendations?.[0]?.user_id || '') } catch (reason) { setError(reason.message) } finally { setBusy(false) } })() }, [])
  async function decide(userId, note) {
    if (!task.id) { onToast('任务尚未创建，建议仅供预览，不会自动指派'); return }
    if (!payload?.recommendation_id) { onToast('还没有推荐记录'); return }
    try {
      const result = await sendJson(`/api/projects/${project.id}/recommendations/${payload.recommendation_id}/decide`, { method: 'POST', body: JSON.stringify({ user_id: Number(userId), note }) })
      const chosen = (members || []).find(member => member.user_id === Number(userId) || member.id === Number(userId))
      setProject(projectState => ({ ...projectState, tasks: projectState.tasks.map(existing => existing.id === task.id ? { ...existing, ...result.task, assignee_name: chosen?.name || result.task.assignee_name } : existing) }))
      onToast(result.changed ? `已将「${task.title}」改派给${chosen?.name || '选定成员'}` : `已将「${task.title}」分配给${chosen?.name || '选定成员'}`)
      onClose()
    } catch (reason) { setError(reason.message) }
  }
  const results = payload?.recommendations || []
  return <div className="modal-backdrop"><div className="modal recommend-modal"><div className="modal-head"><div><span className="eyebrow">AI RECOMMENDATION</span><h2>负责人建议</h2><p className="modal-sub">「{task.title}」 · 推荐仅供参考，不构成成员排名，也不会自动指派</p></div><button onClick={onClose}>×</button></div>
    {busy ? <div className="recommend-loading"><span className="loader"/>正在计算匹配度…</div> : error ? <div className="form-error">{error}</div> : <>
      <div className="recommend-list">{results.map(item => <RecommendCard key={item.user_id} item={item} selected={selected === item.user_id} onSelect={setSelected} onAccept={item => decide(item.user_id, item.reasons?.summary || '采纳推荐')}/>)}{!results.length && <div className="empty-state">暂无可推荐成员</div>}</div>
      {payload?.excluded?.length ? <div className="excluded-box"><strong>未进入候选</strong>{payload.excluded.map(item => <p key={item.user_id}>{item.name}：{item.reason}</p>)}</div> : null}
      <label>也可以手选其他成员<select value={selected} onChange={event => setSelected(Number(event.target.value))}><option value="">请选择</option>{assignable.map(member => <option value={member.user_id || member.id} key={member.user_id || member.id}>{member.name}</option>)}</select></label>
    </>}
    <div className="recommend-foot"><span>◉ {payload?.disclaimer || '推荐仅供参考，最终由组长决定。'}</span><div className="title-actions"><button className="ghost-button" onClick={onClose}>关闭</button>{task.id && selected && <button className="primary-button" onClick={() => decide(selected, '手工指定负责人')}>按所选指派</button>}</div></div>
  </div></div>
}
function RecommendationsView({ project, members, canWrite, onRecommend, onToast, setProject, onReload }) {
  const [batch, setBatch] = useState(null)
  const [history, setHistory] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const unassigned = (project.tasks || []).filter(task => task.status === 'unassigned' || !task.assignee_id)
  async function loadHistory() { try { setHistory(await getJson(`/api/projects/${project.id}/recommendations/history`)) } catch (reason) { setError(reason.message) } }
  useEffect(() => { loadHistory() }, [project.id])
  async function generate() {
    setBusy(true); setError('')
    try { const payload = await sendJson(`/api/projects/${project.id}/recommendations/batch`, { method: 'POST', body: JSON.stringify({ limit: 3 }) }); setBatch(payload); await loadHistory(); onToast(`已为 ${payload.count} 项未分配任务生成建议`) } catch (reason) { setError(reason.message) } finally { setBusy(false) }
  }
  async function decide(recId, userId, title) {
    try {
      const result = await sendJson(`/api/projects/${project.id}/recommendations/${recId}/decide`, { method: 'POST', body: JSON.stringify({ user_id: userId, note: '批量页采纳推荐' }) })
      setProject(projectState => ({ ...projectState, tasks: projectState.tasks.map(existing => existing.id === result.task.id ? { ...existing, ...result.task } : existing) }))
      onToast(`已将「${title}」分配给选定成员`)
      await onReload()
      await loadHistory()
    } catch (reason) { setError(reason.message) }
  }
  return <>
    <PageTitle eyebrow="TASK RECOMMEND" title="未分配任务建议" desc="一键查看所有未分配任务的推荐，不会自动指派。" action={canWrite ? <button className="primary-button" disabled={busy || !unassigned.length} onClick={generate}>{busy ? '生成中…' : '一键给所有未分配任务出建议'}</button> : null}/>
    {error && <div className="form-error">{error}</div>}
    <div className="stage2-grid">
      <section className="panel"><div className="panel-header"><div><h2>未分配任务</h2><p>共 {unassigned.length} 项，建议生成后仍由组长确认</p></div></div>{unassigned.length ? unassigned.map(task => <div className="weekly-member-row" key={task.id}><strong>{task.title}</strong><button className="text-button" onClick={() => onRecommend(task)}>查看建议 →</button></div>) : <div className="empty-state">当前没有未分配任务</div>}</section>
      <section className="panel"><div className="panel-header"><div><h2>最近推荐记录</h2><p>记录是否采纳、采纳了谁、是否改成别人</p></div></div>{(history?.items || []).length ? history.items.slice(0, 8).map(item => <div className="weekly-member-row" key={item.id}><div><strong>{item.task_name || '未命名任务'}</strong><span>{item.status} · {item.top?.name || '暂无候选'} {item.top?.score ? `· ${item.top.score}` : ''}</span></div><span>{item.created_at?.slice(0, 16)?.replace('T', ' ')}</span></div>) : <div className="empty-state">还没有推荐记录</div>}</section>
    </div>
    {(batch?.items || []).map(item => <section className="panel" key={item.recommendation_id}><div className="panel-header"><div><h2>{item.task.task_name}</h2><p>{item.source === 'rule' ? '规则推荐' : '规则 + AI 语义匹配'} · {item.disclaimer}</p></div></div><div className="recommend-list">{(item.recommendations || []).map(candidate => <RecommendCard key={candidate.user_id} item={candidate} selected={false} onSelect={() => {}} onAccept={canWrite ? rec => decide(item.recommendation_id, rec.user_id, item.task.task_name) : null}/>)}</div>{item.excluded?.length ? <div className="excluded-box">{item.excluded.map(row => <p key={row.user_id}>{row.name}：{row.reason}</p>)}</div> : null}</section>)}
  </>
}
function AuthView({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  function fillDemo() {
    setMode('login')
    setError('')
    setForm({ name: '', email: 'owner-demo@example.com', password: 'password-123' })
  }
  async function submit(event) {
    event.preventDefault(); setError(''); setBusy(true)
    try {
      if (!emailLooksValid(form.email)) {
        throw new Error('邮箱格式不正确，请使用类似 rxc@example.com 的地址（需要包含域名，不能是 1@1）')
      }
      const credentials = { email: form.email.trim(), password: form.password }
      if (mode === 'register') await sendJson('/api/auth/register', { method: 'POST', body: JSON.stringify({ name: form.name.trim(), ...credentials }) })
      const result = await sendJson('/api/auth/login', { method: 'POST', body: JSON.stringify(credentials) })
      if (!result?.user?.id) throw new Error('接口未返回有效用户信息')
      onAuthenticated(result.user)
    } catch (err) { setError(formatApiError(err)) } finally { setBusy(false) }
  }
  return <div className="auth-screen"><div className="auth-card"><div className="brand create-brand"><div className="brand-mark">▦</div><div><div className="brand-name">协作账本</div><div className="brand-sub">COLLAB LEDGER</div></div></div><div className="eyebrow">TEAM WORKSPACE</div><h1>{mode === 'login' ? '登录你的协作空间' : '创建一个协作账号'}</h1><p className="create-project-desc">登录状态由安全 Cookie Session 维护，不在浏览器保存访问令牌。</p><form onSubmit={submit}>{mode === 'register' && <label>姓名<input autoFocus value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} required /></label>}<label>邮箱<input type="email" placeholder="例如 rxc@example.com" value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} required /></label><label>密码<input type="password" minLength="8" value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} required /></label>{error && <div className="form-error">{error}</div>}<button className="primary-button create-submit" disabled={busy}>{busy ? '请稍候…' : mode === 'login' ? '登录并进入工作台 →' : '注册账号 →'}</button></form><div className="auth-demo"><span>演示项目账号：owner-demo@example.com / password-123</span><button type="button" className="text-button" onClick={fillDemo}>一键填入演示账号</button></div><button className="auth-switch" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>{mode === 'login' ? '还没有账号？立即注册' : '已有账号？返回登录'}</button></div></div>
}
function MembersModal({ project, currentUser, onClose, onUpdated, onToast }) {
  const [members, setMembers] = useState(project.members || [])
  const [form, setForm] = useState({ name: '', email: '', skills: '', role: 'member' })
  const [invite, setInvite] = useState(null)
  const [busy, setBusy] = useState(false)
  async function createInvite() {
    try { const result = await sendJson(`/api/projects/${project.id}/invitations`, { method: 'POST', body: JSON.stringify({ role: form.role, expires_in_hours: 168, max_uses: 10 }) }); setInvite(result); onToast('邀请链接已生成') } catch { onToast('生成邀请失败，请检查权限') }
  }
  async function updateRole(member, role) {
    try { const updated = await sendJson(`/api/projects/${project.id}/members/${member.user_id}`, { method: 'PATCH', body: JSON.stringify({ role }) }); const next = members.map(m => m.user_id === member.user_id ? { ...m, ...updated, role } : m); setMembers(next); onUpdated({ members: next }) } catch { onToast('角色更新失败') }
  }
  const inviteUrl = absoluteInviteUrl(invite)
  async function copyInvite() {
    try {
      if (!await copyText(inviteUrl)) throw new Error('clipboard unavailable')
      onToast('完整邀请链接已复制')
    } catch { onToast('复制失败，请手动复制邀请链接') }
  }
  return <div className="modal-backdrop"><div className="modal members-modal"><div className="modal-head"><div><span className="eyebrow">TEAM MANAGEMENT</span><h2>成员与邀请</h2><p className="modal-sub">维护成员身份、技能和项目角色。</p></div><button onClick={onClose}>×</button></div><div className="member-manage-list">{members.map(m => { const memberId = m.user_id || m.id; return <div className="member-manage-row" key={memberId}><div className="avatar avatar-0">{initials(m.name)}</div><div className="member-manage-info"><strong title={m.name}>{m.name}</strong><span title={`${m.email || '未设置邮箱'} · ${(m.skills || []).join('、') || '未填写技能'}`}>{m.email || '未设置邮箱'} · {(m.skills || []).join('、') || '未填写技能'}</span></div><select value={m.role || 'member'} disabled={memberId === project.owner_id} onChange={e => updateRole(m, e.target.value)}><option value="owner">组长</option><option value="member">成员</option><option value="viewer">只读</option></select></div> })}</div><div className="form-section-title">添加成员</div><form className="member-add-form" onSubmit={event => { event.preventDefault(); createInvite() }}><div className="form-row"><label>姓名<input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="成员姓名" /></label><label>邮箱<input type="email" value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} placeholder="用于邀请或识别账号" /></label></div><div className="form-row"><label>技能<input value={form.skills} onChange={e => setForm(f => ({ ...f, skills: e.target.value }))} placeholder="前端，测试" /></label><label>角色<select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}><option value="member">成员</option><option value="viewer">只读</option></select></label></div><div className="modal-actions"><button type="button" className="ghost-button" onClick={createInvite}>生成邀请链接</button><button type="button" className="primary-button" disabled={busy} onClick={createInvite}>{busy ? '生成中…' : '生成邀请链接'}</button></div></form>{invite && <div className="invite-result"><strong>邀请已生成</strong><span title={inviteUrl}>{inviteUrl}</span><button type="button" className="ghost-button" onClick={copyInvite}>复制完整链接</button></div>}</div></div>
}

function WorklogModal({ tasks, role, user, onClose, onToast }) {
  const availableTasks = tasks.filter(task => role === 'owner' || task.assignee_id === user.id)
  const [form, setForm] = useState({ task_id: availableTasks[0]?.id || '', content: '', hours: 1, blockers: '' })
  const [busy, setBusy] = useState(false)
  async function save() {
    if (!form.task_id || !form.content.trim()) return
    setBusy(true)
    try {
      await sendJson(`/api/tasks/${form.task_id}/checkins`, {
        method: 'POST',
        body: JSON.stringify({ content: form.content.trim(), hours: Number(form.hours), blockers: form.blockers.trim() || null }),
      })
      onToast('任务打卡已保存'); onClose()
    } catch (error) { onToast(error.message) } finally { setBusy(false) }
  }
  return <div className="modal-backdrop"><div className="modal worklog-modal"><div className="modal-head"><div><span className="eyebrow">TASK CHECK-IN</span><h2>今日主动打卡</h2><p className="modal-sub">只记录你主动填写的任务进展与阻塞。</p></div><button onClick={onClose}>×</button></div>{availableTasks.length ? <><label>关联任务<select value={form.task_id} onChange={e => setForm(f => ({ ...f, task_id: Number(e.target.value) }))}>{availableTasks.map(task => <option value={task.id} key={task.id}>{task.title}</option>)}</select></label><label>完成内容<textarea autoFocus value={form.content} onChange={e => setForm(f => ({ ...f, content: e.target.value }))} placeholder="完成了什么、下一步是什么？" /></label><div className="form-row"><label>投入小时<input type="number" min="0" max="24" step="0.5" value={form.hours} onChange={e => setForm(f => ({ ...f, hours: e.target.value }))} /></label><label>阻塞事项<input value={form.blockers} onChange={e => setForm(f => ({ ...f, blockers: e.target.value }))} placeholder="没有可留空" /></label></div><div className="modal-actions"><button className="ghost-button" onClick={onClose}>取消</button><button className="primary-button" disabled={busy || !form.content.trim()} onClick={save}>{busy ? '保存中…' : '保存打卡'}</button></div></> : <div className="empty-state">当前没有可打卡的任务。普通成员只能为自己负责的任务打卡。</div>}</div></div>
}

function QualityReviewModal({ project, task, currentUser, members, onClose, onSaved }) {
  const [score, setScore] = useState(task.quality || 4)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() { setBusy(true); try { const review = await sendJson(`/api/tasks/${task.id}/review`, { method: 'POST', body: JSON.stringify({ quality: Number(score), comment: comment.trim() || null }) }); onSaved(review.quality); onClose() } catch (error) { alert(error.message) } finally { setBusy(false) } }
  return <div className="modal-backdrop"><div className="modal quality-modal"><div className="modal-head"><div><span className="eyebrow">QUALITY REVIEW</span><h2>评价任务交付质量</h2><p className="modal-sub">「{task.title}」 · {task.assignee_name || members.find(m => m.id === task.assignee_id)?.name || '未分配'}</p></div><button onClick={onClose}>×</button></div><label>质量评分 <span className="quality-value">{Number(score).toFixed(1)} / 5</span><input className="quality-range" type="range" min="0" max="5" step="0.5" value={score} onChange={e => setScore(e.target.value)} /></label><label>评价说明<textarea value={comment} onChange={e => setComment(e.target.value)} placeholder="从完成度、准确性、协作交付等方面记录事实…" /></label><div className="modal-actions"><button className="ghost-button" onClick={onClose}>稍后评价</button><button className="primary-button" disabled={busy} onClick={submit}>{busy ? '保存中…' : '提交评价'}</button></div></div></div>
}

function CreateProjectView({ currentUser, onCreated, onCancel }) {
  const [form, setForm] = useState({ name: '', project_type: '课程项目', description: '', start_date: '', end_date: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  async function submit(event) {
    event.preventDefault(); setError('')
    if (!form.name.trim()) { setError('请填写项目名称'); return }
    setBusy(true)
    try {
      const project = await sendJson('/api/projects', { method: 'POST', body: JSON.stringify({ name: form.name.trim(), project_type: form.project_type, description: form.description.trim() || null, start_date: form.start_date || null, end_date: form.end_date || null }) })
      await onCreated(project)
    } catch (err) { setError('创建失败，请确认后端已启动，并检查项目名称和负责人信息。') } finally { setBusy(false) }
  }
  return <div className="create-project-screen"><div className="create-project-card"><div className="brand create-brand"><div className="brand-mark">▦</div><div><div className="brand-name">协作账本</div><div className="brand-sub">COLLAB LEDGER</div></div></div><div className="eyebrow">FIRST PROJECT</div><h1>创建你的第一个项目</h1><p className="create-project-desc">项目数据会写入 SQLite，之后任务、贡献和 Agent 记忆都会归属于这个真实项目。</p><form onSubmit={submit}><div className="form-row"><label>项目名称<input autoFocus value={form.name} onChange={e => update('name', e.target.value)} placeholder="例如：软件工程课程大作业" /></label><label>项目类型<select value={form.project_type} onChange={e => update('project_type', e.target.value)}><option>课程项目</option><option>竞赛项目</option><option>科研项目</option><option>其他</option></select></label></div><label>项目简介<textarea value={form.description} onChange={e => update('description', e.target.value)} placeholder="说明项目目标和协作范围…" /></label><div className="form-row"><label>开始日期<input type="date" value={form.start_date} onChange={e => update('start_date', e.target.value)} /></label><label>结束日期<input type="date" value={form.end_date} onChange={e => update('end_date', e.target.value)} /></label></div><div className="form-section-title">创建者</div><p className="modal-sub">当前登录用户 {currentUser?.name} 将自动成为项目 owner。</p>{error && <div className="form-error">{error}</div>}<div className="create-form-actions">{onCancel && <button type="button" className="ghost-button" onClick={onCancel}>返回当前项目</button>}<button className="primary-button create-submit" disabled={busy}>{busy ? '正在创建…' : '创建项目并进入工作台 →'}</button></div></form><div className="create-privacy">◉ 只记录项目协作成果，不采集私人聊天、桌面、摄像头或键鼠数据。</div></div></div>
}

class AppErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) { console.error('协作账本前端运行时错误', error, info) }
  render() {
    if (this.state.error) return <div className="app-error-screen"><div><div className="brand-mark">!</div><h1>页面加载遇到问题</h1><p>{this.state.error.message || '未知前端错误'}</p><button className="primary-button" onClick={() => window.location.reload()}>重新加载</button></div></div>
    return this.props.children
  }
}

const root = createRoot(document.getElementById('root')); root.render(<AppErrorBoundary><App /></AppErrorBoundary>)
