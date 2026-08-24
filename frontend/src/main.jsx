import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const demoProject = {
  id: 1, name: '软件工程课程大作业', project_type: '课程项目', description: '面向小组作业的智能协作与贡献管理', start_date: '2026-08-18', end_date: '2026-09-20',
  members: [
    { id: 1, name: '张三', role: 'owner', status: 'online', skills: ['后端', 'Python', '数据分析'], max_concurrent_tasks: 3 },
    { id: 2, name: '李四', role: 'member', status: 'busy', skills: ['前端', '交互设计'], max_concurrent_tasks: 3 },
    { id: 3, name: '王五', role: 'member', status: 'online', skills: ['数据库', '测试'], max_concurrent_tasks: 3 },
    { id: 4, name: '赵六', role: 'member', status: 'away', skills: ['产品', '文档'], max_concurrent_tasks: 2 },
  ],
  tasks: [
    { id: 101, title: '完成数据库设计', description: '确定实体关系、索引和初始化脚本', assignee_id: 3, assignee_name: '王五', status: 'in_progress', due_date: '2026-08-28', estimated_hours: 4, actual_hours: null, quality: null, task_type: '数据库' },
    { id: 102, title: '搭建前端页面骨架', description: '完成路由、布局与核心组件', assignee_id: 2, assignee_name: '李四', status: 'in_progress', due_date: '2026-08-29', estimated_hours: 8, actual_hours: 5.5, quality: null, task_type: '前端' },
    { id: 103, title: '撰写项目报告第三章', description: '整理系统设计和关键实现', assignee_id: 4, assignee_name: '赵六', status: 'assigned', due_date: '2026-08-31', estimated_hours: 5, actual_hours: null, quality: null, task_type: '文档' },
    { id: 104, title: '完成项目 PPT', description: '汇报结构、视觉规范和演示稿', assignee_id: null, assignee_name: null, status: 'unassigned', due_date: '2026-09-03', estimated_hours: 4, actual_hours: null, quality: null, task_type: '汇报' },
    { id: 105, title: '需求分析与用户访谈', description: '完成问卷、访谈并输出洞察', assignee_id: 1, assignee_name: '张三', status: 'completed', due_date: '2026-08-25', estimated_hours: 6, actual_hours: 5, quality: 4.5, task_type: '产品' },
    { id: 106, title: 'GitHub Actions 自动化', description: '配置测试与构建流水线', assignee_id: 1, assignee_name: '张三', status: 'overdue', due_date: '2026-08-22', estimated_hours: 3, actual_hours: null, quality: null, task_type: '后端' },
  ],
  contributions: [
    { id: 1, user_id: 1, user_name: '张三', kind: 'code', title: '提交 API 鉴权模块', description: '新增 6 个接口并补充单元测试', quantity: 8, created_at: '2026-08-23T09:20:00Z' },
    { id: 2, user_id: 2, user_name: '李四', kind: 'document', title: '更新交互流程图', description: '完成任务板和报告页的交互标注', quantity: 2, created_at: '2026-08-23T14:10:00Z' },
    { id: 3, user_id: 3, user_name: '王五', kind: 'code', title: '数据库索引优化', description: '添加 3 个复合索引，查询耗时下降 42%', quantity: 3, created_at: '2026-08-22T17:40:00Z' },
    { id: 4, user_id: 4, user_name: '赵六', kind: 'meeting', title: '主持周会并同步决策', description: '沉淀 4 条会议决策和行动项', quantity: 1, created_at: '2026-08-21T10:00:00Z' },
  ],
}

const statusMeta = {
  unassigned: { label: '未分配', tone: 'slate' },
  assigned: { label: '待开始', tone: 'blue' },
  in_progress: { label: '进行中', tone: 'amber' },
  paused: { label: '已暂停', tone: 'purple' },
  completed: { label: '已完成', tone: 'green' },
  overdue: { label: '延期', tone: 'red' },
  unfinished: { label: '未完成', tone: 'red' },
}
const nav = [
  { id: 'overview', label: '项目总览' },
  { id: 'tasks', label: '任务看板' },
  { id: 'contributions', label: '贡献账本' },
  { id: 'report', label: '贡献报告' },
  { id: 'agent', label: '协作 Agent' },
]
const kindMeta = { code: '代码', document: '文档', meeting: '会议', task: '任务', other: '其他' }

async function getJson(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error('network')
  return r.json()
}
async function sendJson(url, options = {}) {
  if (window.__collabDemoMode && url.includes('/agent')) throw new Error('demo-mode')
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!r.ok) throw new Error('network')
  return r.json()
}
function initials(name = '') { return name.slice(0, 1) }
function formatDate(value) {
  if (!value) return '未设置'
  const d = new Date(value)
  return d.getMonth() + 1 + '月' + d.getDate() + '日'
}

function useEscape(onClose) {
  useEffect(() => {
    const onKey = event => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
}

function App() {
  const [project, setProject] = useState(demoProject)
  const [report, setReport] = useState(null)
  const [active, setActive] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [online, setOnline] = useState(false)
  const [showTask, setShowTask] = useState(false)
  const [recommendTask, setRecommendTask] = useState(null)
  const [toast, setToast] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => { loadProject() }, [])
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(''), 2600)
    return () => clearTimeout(timer)
  }, [toast])

  async function loadProject() {
    setLoading(true)
    try {
      const projects = await getJson('/api/projects')
      const id = projects[0]?.id
      if (!id) {
        window.__collabDemoMode = false
        setProject(null)
        setReport(null)
        setOnline(true)
        return
      }
      const [detail, rep, contributions] = await Promise.all([
        getJson('/api/projects/' + id),
        getJson('/api/projects/' + id + '/report'),
        getJson('/api/projects/' + id + '/contributions'),
      ])
      const names = Object.fromEntries((detail.members || []).map(member => [member.id, member.name]))
      detail.tasks = (detail.tasks || []).map(task => ({ ...task, assignee_name: names[task.assignee_id] || null }))
      window.__collabDemoMode = false
      setProject({ ...detail, contributions })
      setReport(rep)
      setOnline(true)
    } catch {
      window.__collabDemoMode = true
      setOnline(false)
      setProject(demoProject)
      setReport(null)
    } finally {
      setLoading(false)
    }
  }

  const tasks = project?.tasks || []
  const members = project?.members || []
  const activeTasks = tasks.filter(task => ['assigned', 'in_progress', 'paused'].includes(task.status))
  const completed = tasks.filter(task => task.status === 'completed').length
  const overdue = tasks.filter(task => ['overdue', 'unfinished'].includes(task.status)).length
  const progress = tasks.length ? Math.round(completed / tasks.length * 100) : 0
  const memberStats = useMemo(() => members.map(member => ({
    ...member,
    current: tasks.filter(task => task.assignee_id === member.id && ['assigned', 'in_progress', 'paused'].includes(task.status)).length,
    done: tasks.filter(task => task.assignee_id === member.id && task.status === 'completed').length,
    quality: (() => {
      const values = tasks.filter(task => task.assignee_id === member.id && task.quality != null).map(task => task.quality)
      return values.length ? (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1) : '—'
    })(),
  })), [members, tasks])

  if (!loading && !project) {
    return (
      <CreateProjectView
        onCreated={async created => {
          const [detail, rep, contributions] = await Promise.all([
            getJson('/api/projects/' + created.id),
            getJson('/api/projects/' + created.id + '/report'),
            getJson('/api/projects/' + created.id + '/contributions'),
          ])
          const names = Object.fromEntries((detail.members || []).map(member => [member.id, member.name]))
          detail.tasks = (detail.tasks || []).map(task => ({ ...task, assignee_name: names[task.assignee_id] || null }))
          window.__collabDemoMode = false
          setProject({ ...detail, contributions })
          setReport(rep)
          setOnline(true)
        }}
      />
    )
  }

  async function taskAction(task, action) {
    try {
      const updated = await sendJson('/api/tasks/' + task.id + '/' + action, { method: 'POST' })
      setProject(current => ({ ...current, tasks: current.tasks.map(item => item.id === task.id ? { ...item, ...updated } : item) }))
      setToast('已将「' + task.title + '」标记为' + (statusMeta[updated.status]?.label || '已更新'))
    } catch {
      const next = { start: 'in_progress', pause: 'paused', resume: 'in_progress', complete: 'completed', overdue: 'overdue', unfinished: 'unfinished' }[action]
      setProject(current => ({ ...current, tasks: current.tasks.map(item => item.id === task.id ? { ...item, status: next } : item) }))
      setToast('演示模式：状态已更新')
    }
  }

  async function createTask(data) {
    try {
      const task = await sendJson('/api/projects/' + project.id + '/tasks', { method: 'POST', body: JSON.stringify(data) })
      setProject(current => ({ ...current, tasks: [task, ...current.tasks] }))
      setToast('任务已创建')
    } catch {
      const task = {
        ...data,
        id: Date.now(),
        status: data.assignee_id ? 'assigned' : 'unassigned',
        assignee_name: members.find(member => member.id === Number(data.assignee_id))?.name || null,
      }
      setProject(current => ({ ...current, tasks: [task, ...current.tasks] }))
      setToast('演示模式：任务已创建')
    }
    setShowTask(false)
  }

  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="wordmark"><b>协作账本</b><span>Ledger</span></div>
        <div className="project-chip">
          <strong>{project.name}</strong>
          <em>{project.project_type || '项目空间'}</em>
        </div>
        <nav>
          {nav.map(item => (
            <button
              key={item.id}
              className={'nav-item ' + (active === item.id ? 'selected' : '')}
              aria-current={active === item.id ? 'page' : undefined}
              onClick={() => setActive(item.id)}
            >
              {item.label}
              {item.id === 'tasks' && overdue > 0 && <span className="nav-badge">{overdue}</span>}
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          <strong>张三</strong>
          <p>只记录项目产出，不采集私人聊天、桌面或键鼠数据。</p>
        </div>
      </aside>
      <main className="stage">
        <header className="mast">
          <div className="mast-title">
            <span>{nav.find(item => item.id === active)?.label}</span>
            <strong>{project.name}</strong>
          </div>
          <div className="search">
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder="按任务或成员筛选" />
          </div>
          <div className={'live ' + (online ? '' : 'offline')}>
            <b>{online ? '已同步' : '演示数据'}</b>
          </div>
        </header>
        <div className="page">
          {loading ? <div className="loading-state">正在读取项目记录…</div> : (
            <>
              {active === 'overview' && (
                <Overview
                  project={project}
                  memberStats={memberStats}
                  tasks={tasks}
                  progress={progress}
                  completed={completed}
                  overdue={overdue}
                  activeTasks={activeTasks}
                  online={online}
                  onNavigate={setActive}
                  onAction={taskAction}
                  onRecommend={setRecommendTask}
                />
              )}
              {active === 'tasks' && (
                <TasksView
                  tasks={tasks.filter(task => !query || (task.title + (task.assignee_name || '')).includes(query))}
                  members={members}
                  onAction={taskAction}
                  onCreate={() => setShowTask(true)}
                  onRecommend={setRecommendTask}
                />
              )}
              {active === 'contributions' && (
                <ContributionsView project={project} members={members} online={online} setProject={setProject} onToast={setToast} />
              )}
              {active === 'report' && (
                <ReportView project={project} report={report} memberStats={memberStats} tasks={tasks} />
              )}
              {active === 'agent' && (
                <AgentView project={project} online={online} onRecommend={setRecommendTask} />
              )}
            </>
          )}
        </div>
      </main>
      {showTask && <TaskModal members={members} onClose={() => setShowTask(false)} onSave={createTask} />}
      {recommendTask && (
        <RecommendModal
          task={recommendTask}
          project={project}
          members={members}
          onClose={() => setRecommendTask(null)}
          onToast={setToast}
          setProject={setProject}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

function PageTitle({ title, desc, action }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {desc && <p>{desc}</p>}
      </div>
      {action}
    </div>
  )
}

function Overview({ project, memberStats, tasks, progress, completed, overdue, activeTasks, online, onNavigate, onAction, onRecommend }) {
  return (
    <>
      <PageTitle
        title={project.name}
        desc={project.description || '按任务、贡献和风险看这一周的协作。'}
        action={<button className="primary-button" onClick={() => onNavigate('tasks')}>新建任务</button>}
      />
      <div className="stat-strip">
        <Stat label="周期" value={formatDate(project.start_date) + ' – ' + formatDate(project.end_date)} hint={memberStats.length + ' 位成员'} />
        <Stat label="进行中" value={activeTasks.length} hint={'共 ' + tasks.length + ' 项'} />
        <Stat label="已完成" value={completed} hint={progress + '%'} />
        <Stat label="需处理" value={overdue} hint={overdue ? '建议今天处理延期项' : '暂无延期'} />
      </div>
      <div className="work-grid">
        <section className="sheet">
          <div className="sheet-head">
            <h2>成员</h2>
            <button className="text-button" onClick={() => onNavigate('contributions')}>贡献账本</button>
          </div>
          {memberStats.map(member => <MemberRow key={member.id} member={member} />)}
        </section>
        <section className="sheet">
          <div className="sheet-head">
            <h2>需要关注</h2>
            <button className="text-button" onClick={() => onNavigate('tasks')}>全部任务</button>
          </div>
          {tasks.filter(task => task.status !== 'completed').slice(0, 4).map(task => (
            <TaskRow key={task.id} task={task} onAction={onAction} onRecommend={onRecommend} />
          ))}
        </section>
      </div>
      <p className="footnote">{online ? '数据已与接口同步。' : '当前展示演示数据。'} 推荐只提供建议，不生成排名。</p>
    </>
  )
}

function Stat({ label, value, hint }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  )
}

function MemberRow({ member }) {
  const status = { online: ['协作中', 'online'], busy: ['专注中', 'busy'], away: ['暂离', 'away'], offline: ['离线', 'offline'] }[member.status] || ['协作中', 'online']
  return (
    <div className="member-row">
      <span className="initial">{initials(member.name)}</span>
      <div>
        <strong>{member.name}</strong>
        <small>
          <span className={'status ' + status[1]}>{status[0]}</span>
          {' ' + (member.skills || []).slice(0, 3).join(' · ')}
        </small>
      </div>
      <span className="member-load">{member.current}/{member.max_concurrent_tasks}</span>
    </div>
  )
}

function TaskRow({ task, onAction, onRecommend }) {
  const meta = statusMeta[task.status] || statusMeta.unassigned
  const action = task.status === 'in_progress' ? 'complete' : task.status === 'paused' ? 'resume' : task.status === 'assigned' ? 'start' : null
  return (
    <div className="task-row">
      <div>
        <strong>{task.title}</strong>
        <div className="task-row-meta">
          <span className={'tag ' + meta.tone}>{meta.label}</span>
          <span>截止 {formatDate(task.due_date)}</span>
          {task.assignee_name ? <span className="assignee-inline">{task.assignee_name}</span> : (
            <button className="assign-link" onClick={() => onRecommend(task)}>分配负责人</button>
          )}
        </div>
      </div>
      {action && (
        <button className="row-action" onClick={() => onAction(task, action)}>
          {action === 'complete' ? '完成' : action === 'resume' ? '继续' : '开始'}
        </button>
      )}
    </div>
  )
}

function TasksView({ tasks, members, onAction, onCreate, onRecommend }) {
  const [filter, setFilter] = useState('all')
  const filtered = filter === 'all' ? tasks : tasks.filter(task => task.status === filter)
  const columns = ['unassigned', 'assigned', 'in_progress', 'paused', 'completed', 'overdue', 'unfinished']
  return (
    <>
      <PageTitle title="任务看板" desc="从开始到完成，记下每一段真实的协作过程。" action={<button className="primary-button" onClick={onCreate}>新建任务</button>} />
      <div className="board-toolbar">
        <div className="filter-tabs">
          <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部</button>
          {columns.map(id => (
            <button key={id} className={filter === id ? 'active' : ''} onClick={() => setFilter(id)}>{statusMeta[id].label}</button>
          ))}
        </div>
      </div>
      <div className="board-grid">
        {columns.map(id => {
          const list = filtered.filter(task => task.status === id)
          return (
            <div className="board-column" key={id}>
              <div className="column-header">
                <strong>{statusMeta[id].label}</strong>
                <span className="column-count">{list.length}</span>
              </div>
              {list.map(task => <TaskCard key={task.id} task={task} members={members} onAction={onAction} onRecommend={onRecommend} />)}
              {!list.length && <div className="empty-column">暂无任务</div>}
            </div>
          )
        })}
      </div>
    </>
  )
}

function TaskCard({ task, onAction, onRecommend }) {
  const meta = statusMeta[task.status]
  const action = task.status === 'in_progress' ? 'complete' : task.status === 'assigned' ? 'start' : task.status === 'paused' ? 'resume' : null
  return (
    <article className="task-card">
      <span className={'tag ' + meta.tone}>{meta.label}</span>
      <h3>{task.title}</h3>
      {task.description && <p>{task.description}</p>}
      <div className="task-card-info">
        <span>{formatDate(task.due_date)}</span>
        <span>{task.estimated_hours || '—'}h</span>
      </div>
      <div className="task-card-bottom">
        {task.assignee_name ? <span className="assignee-inline">{task.assignee_name}</span> : (
          <button className="assign-link" onClick={() => onRecommend(task)}>分配负责人</button>
        )}
        {action && (
          <button className="mini-action" onClick={() => onAction(task, action)}>
            {action === 'complete' ? '完成' : action === 'resume' ? '继续' : '开始'}
          </button>
        )}
      </div>
    </article>
  )
}

function ContributionsView({ project, members, online, setProject, onToast }) {
  const [kind, setKind] = useState('all')
  const [open, setOpen] = useState(false)
  const contributions = project.contributions || []
  const list = kind === 'all' ? contributions : contributions.filter(item => item.kind === kind)
  async function save(data) {
    try {
      const item = await sendJson('/api/projects/' + project.id + '/contributions', { method: 'POST', body: JSON.stringify(data) })
      setProject(current => ({ ...current, contributions: [item, ...(current.contributions || [])] }))
      onToast('贡献记录已保存')
    } catch {
      const member = members.find(item => item.id === Number(data.user_id))
      setProject(current => ({
        ...current,
        contributions: [{ ...data, id: Date.now(), user_name: member?.name || '我', created_at: new Date().toISOString() }, ...(current.contributions || [])],
      }))
      onToast('演示模式：贡献记录已保存')
    }
    setOpen(false)
  }
  return (
    <>
      <PageTitle title="贡献账本" desc="记录做了什么，而不是监控正在做什么。" action={<button className="primary-button" onClick={() => setOpen(true)}>记录贡献</button>} />
      <div className="privacy-banner">
        <div>
          <strong>这是公平秤，不是监控器</strong>
          <p>只记录项目相关的产出：任务、代码、文档和会议。数据默认仅对项目组可见。</p>
        </div>
        <span className="live">{online ? '已同步' : '演示数据'}</span>
      </div>
      <div className="stat-strip">
        <Stat label="本周贡献" value={contributions.length} hint="条可追溯记录" />
        <Stat label="代码提交" value={contributions.filter(item => item.kind === 'code').reduce((sum, item) => sum + (item.quantity || 1), 0)} hint="次变更" />
        <Stat label="活跃成员" value={new Set(contributions.map(item => item.user_id)).size} hint={'共 ' + members.length + ' 位'} />
      </div>
      <div className="ledger-header">
        <div className="filter-tabs">
          {[['all', '全部'], ['code', '代码'], ['document', '文档'], ['meeting', '会议'], ['task', '任务']].map(([id, label]) => (
            <button key={id} className={kind === id ? 'active' : ''} onClick={() => setKind(id)}>{label}</button>
          ))}
        </div>
        <span className="ledger-note">按时间倒序</span>
      </div>
      <div className="ledger-list">
        {list.map(item => <ContributionItem key={item.id} item={item} />)}
        {!list.length && <div className="empty-state">还没有这类贡献记录</div>}
      </div>
      {open && <ContributionModal members={members} onClose={() => setOpen(false)} onSave={save} />}
    </>
  )
}

function ContributionItem({ item }) {
  return (
    <div className="contribution-item">
      <span className="contribution-kind">{kindMeta[item.kind] || kindMeta.other}</span>
      <div className="contribution-main">
        <strong>{item.title || '未命名贡献'}</strong>
        <p>{item.description || '成员提交了一条项目产出记录'}</p>
        <span>{item.user_name} · {formatDate(item.created_at)}</span>
      </div>
      <div className="contribution-qty">
        <strong>{item.quantity || 1}</strong>
        <span>{item.kind === 'code' ? '次' : '项'}</span>
      </div>
    </div>
  )
}

function ReportView({ project, report, memberStats, tasks }) {
  const rows = report?.members || memberStats.map(member => ({
    user_id: member.id,
    name: member.name,
    tasks_total: tasks.filter(task => task.assignee_id === member.id).length,
    tasks_completed: member.done,
    tasks_overdue: tasks.filter(task => task.assignee_id === member.id && ['overdue', 'unfinished'].includes(task.status)).length,
    average_quality: member.quality === '—' ? null : Number(member.quality),
    actual_hours: tasks.filter(task => task.assignee_id === member.id).reduce((sum, task) => sum + (task.actual_hours || 0), 0),
  }))
  const total = report?.overall?.tasks || tasks.length
  const done = report?.overall?.completed || tasks.filter(task => task.status === 'completed').length
  return (
    <>
      <PageTitle title="项目贡献报告" desc="用事实回顾协作过程，为组内互评提供依据。" action={<button className="ghost-button">导出报告</button>} />
      <div className="report-hero">
        <div>
          <h2>{project.name}</h2>
          <p>截至今天，项目已完成 {done} / {total} 项任务。</p>
        </div>
        <div className="big-progress">
          <strong>{total ? Math.round(done / total * 100) : 0}%</strong>
          <div className="progress-track"><i style={{ width: (total ? done / total * 100 : 0) + '%' }} /></div>
          <span>整体完成度</span>
        </div>
      </div>
      <div className="report-grid">
        <section className="sheet">
          <div className="sheet-head"><h2>成员贡献概览</h2></div>
          <div className="report-row report-head">
            <span>成员</span><span>完成任务</span><span>延期</span><span>平均质量</span><span>实际耗时</span>
          </div>
          {rows.map(row => (
            <div className="report-row" key={row.user_id}>
              <span className="assignee-inline"><strong>{row.name}</strong></span>
              <span>{row.tasks_completed} / {row.tasks_total}</span>
              <span className={row.tasks_overdue ? 'danger-text' : ''}>{row.tasks_overdue}</span>
              <span>{row.average_quality ? row.average_quality + ' / 5' : '—'}</span>
              <span>{row.actual_hours || 0}h</span>
            </div>
          ))}
        </section>
        <section className="sheet">
          <div className="sheet-head"><h2>协作摘要</h2></div>
          <div className="profile-insight">
            <span className="initial">张</span>
            <div>
              <strong>张三</strong>
              <p>主要承担后端开发与数据处理，代码任务完成较快，同时承担了较多核心任务。</p>
            </div>
          </div>
          <SkillBar label="后端 / Python" value={88} />
          <SkillBar label="数据分析" value={74} />
          <SkillBar label="产品协作" value={52} />
          <p className="footnote">不形成公开排名，仅用于项目复盘。</p>
        </section>
      </div>
    </>
  )
}

function SkillBar({ label, value }) {
  return (
    <div className="skill-bar">
      <div><span>{label}</span><b>{value}%</b></div>
      <div className="progress-track"><i style={{ width: value + '%' }} /></div>
    </div>
  )
}

function AgentView({ project, online, onRecommend }) {
  const [messages, setMessages] = useState([{ role: 'agent', text: '你好，我是协作 Agent。可以帮你分析项目风险、生成周报，或推荐任务负责人。' }])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  async function ask(text = input) {
    if (!text.trim() || busy) return
    setMessages(current => [...current, { role: 'user', text }])
    setInput('')
    setBusy(true)
    try {
      const result = await sendJson('/api/projects/' + project.id + '/agent', { method: 'POST', body: JSON.stringify({ message: text }) })
      setMessages(current => [...current, { role: 'agent', text: result.answer }])
    } catch {
      const fallback = text.includes('风险') || text.includes('延期')
        ? '目前最大的风险是「GitHub Actions 自动化」已经延期，建议今天由张三优先处理，或拆分给其他成员。'
        : text.includes('周报') || text.includes('总结')
          ? '本周共完成 1 项任务，4 位成员产生了 4 条贡献记录。整体进度正常，建议关注 1 项延期任务。'
          : '我可以帮助分析项目风险、生成周报或推荐任务负责人。试试问我“目前项目最大的风险是什么？”'
      setMessages(current => [...current, { role: 'agent', text: fallback }])
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageTitle
        title="协作 Agent"
        desc="把项目事实变成可执行的下一步建议。"
        action={<span className={'live ' + (online ? '' : 'offline')}><b>{online ? '接口已连接' : '演示模式'}</b></span>}
      />
      <div className="agent-layout">
        <section>
          <div className="letters">
            {messages.map((message, index) => (
              <div key={index} className={'letter ' + message.role}>
                <span className="who">{message.role === 'agent' ? 'Agent' : '你'}</span>
                <p>{message.text}</p>
              </div>
            ))}
            {busy && (
              <div className="letter agent">
                <span className="who">Agent</span>
                <p>正在查阅项目记录…</p>
              </div>
            )}
          </div>
          <div className="agent-input">
            <input
              value={input}
              onChange={event => setInput(event.target.value)}
              onKeyDown={event => event.key === 'Enter' && ask()}
              placeholder="输入你想了解的项目问题"
            />
            <button className="primary-button" onClick={() => ask()} disabled={busy}>发送</button>
          </div>
        </section>
        <aside>
          <div className="sheet-head"><h2>可询问</h2></div>
          <div className="question-list">
            <button onClick={() => ask('目前项目最大的风险是什么？')}>目前项目最大的风险是什么？<span>→</span></button>
            <button onClick={() => ask('帮我总结一下这周我们组的工作')}>帮我总结一下这周我们组的工作<span>→</span></button>
            <button onClick={() => onRecommend({ id: 0, title: '完成项目 PPT', task_type: '汇报', estimated_hours: 4 })}>这个任务应该给谁？<span>→</span></button>
          </div>
          <div className="agent-note">
            <div>
              <strong>只提供建议</strong>
              <p>推荐基于成员主动提供的技能、任务历史和当前负载。最终决定权始终在组长和成员手中。</p>
            </div>
          </div>
        </aside>
      </div>
    </>
  )
}

function ModalFrame({ title, kicker, wide, onClose, children }) {
  useEscape(onClose)
  return (
    <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <div className={'modal ' + (wide ? 'recommend-modal' : '')} role="dialog" aria-modal="true">
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            {kicker && <p className="modal-sub">{kicker}</p>}
          </div>
          <button className="icon-x" onClick={onClose} aria-label="关闭">关闭</button>
        </div>
        {children}
      </div>
    </div>
  )
}

function TaskModal({ members, onClose, onSave }) {
  const [form, setForm] = useState({ title: '', description: '', task_type: '其他', estimated_hours: 4, due_date: '', assignee_id: '' })
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  return (
    <ModalFrame title="创建新任务" onClose={onClose}>
      <label>任务名称<input autoFocus value={form.title} onChange={event => update('title', event.target.value)} placeholder="例如：完成项目 PPT" /></label>
      <label>任务描述<textarea value={form.description} onChange={event => update('description', event.target.value)} placeholder="补充任务背景和交付标准" /></label>
      <div className="form-row">
        <label>任务类型
          <select value={form.task_type} onChange={event => update('task_type', event.target.value)}>
            <option>其他</option><option>前端</option><option>后端</option><option>数据库</option><option>文档</option><option>汇报</option>
          </select>
        </label>
        <label>预计耗时（小时）<input type="number" min="0" value={form.estimated_hours} onChange={event => update('estimated_hours', Number(event.target.value))} /></label>
      </div>
      <div className="form-row">
        <label>截止日期<input type="date" value={form.due_date} onChange={event => update('due_date', event.target.value)} /></label>
        <label>负责人（可稍后分配）
          <select value={form.assignee_id} onChange={event => update('assignee_id', event.target.value)}>
            <option value="">暂不分配</option>
            {members.map(member => <option value={member.id} key={member.id}>{member.name}</option>)}
          </select>
        </label>
      </div>
      <div className="modal-actions">
        <button className="ghost-button" onClick={onClose}>取消</button>
        <button className="primary-button" disabled={!form.title.trim()} onClick={() => onSave({ ...form, assignee_id: form.assignee_id ? Number(form.assignee_id) : null, estimated_hours: Number(form.estimated_hours) })}>创建任务</button>
      </div>
    </ModalFrame>
  )
}

function ContributionModal({ members, onClose, onSave }) {
  const [form, setForm] = useState({ user_id: members[0]?.id || '', kind: 'code', title: '', description: '', quantity: 1 })
  return (
    <ModalFrame title="记录一条贡献" onClose={onClose}>
      <div className="form-row">
        <label>成员
          <select value={form.user_id} onChange={event => setForm(current => ({ ...current, user_id: Number(event.target.value) }))}>
            {members.map(member => <option value={member.id} key={member.id}>{member.name}</option>)}
          </select>
        </label>
        <label>贡献类型
          <select value={form.kind} onChange={event => setForm(current => ({ ...current, kind: event.target.value }))}>
            <option value="code">代码 / Commit</option>
            <option value="document">文档</option>
            <option value="meeting">会议</option>
            <option value="task">任务</option>
            <option value="other">其他</option>
          </select>
        </label>
      </div>
      <label>贡献标题<input autoFocus value={form.title} onChange={event => setForm(current => ({ ...current, title: event.target.value }))} placeholder="例如：完成 API 鉴权模块" /></label>
      <label>补充说明<textarea value={form.description} onChange={event => setForm(current => ({ ...current, description: event.target.value }))} placeholder="写下可被复盘的事实" /></label>
      <label>数量 / 次数<input type="number" min="1" value={form.quantity} onChange={event => setForm(current => ({ ...current, quantity: Number(event.target.value) }))} /></label>
      <div className="modal-actions">
        <button className="ghost-button" onClick={onClose}>取消</button>
        <button className="primary-button" disabled={!form.title.trim()} onClick={() => onSave(form)}>保存记录</button>
      </div>
    </ModalFrame>
  )
}

function RecommendModal({ task, project, members, onClose, onToast, setProject }) {
  const [results, setResults] = useState([])
  const [busy, setBusy] = useState(true)
  useEffect(() => {
    (async () => {
      try {
        const query = new URLSearchParams({ task_name: task.title, task_type: task.task_type || '', estimated_hours: task.estimated_hours || 1 })
        const result = await getJson('/api/projects/' + project.id + '/recommendations?' + query)
        setResults(result.recommendations || [])
      } catch {
        const scores = members.map((member, index) => ({
          user_id: member.id,
          name: member.name,
          score: Math.max(62, 92 - index * 8),
          reasons: { skills: member.skills, average_quality: index === 0 ? 4.7 : 4.2, efficiency: index === 0 ? 1.12 : 1.02, current_load: (index % 3) + '/3' },
        })).sort((a, b) => b.score - a.score)
        setResults(scores)
      } finally {
        setBusy(false)
      }
    })()
  }, [])
  async function choose(result) {
    if (task.id) {
      try {
        const updated = await sendJson('/api/tasks/' + task.id, { method: 'PATCH', body: JSON.stringify({ assignee_id: result.user_id, status: 'assigned' }) })
        setProject(current => ({ ...current, tasks: current.tasks.map(item => item.id === task.id ? { ...item, ...updated, assignee_name: result.name } : item) }))
      } catch {
        setProject(current => ({ ...current, tasks: current.tasks.map(item => item.id === task.id ? { ...item, assignee_id: result.user_id, assignee_name: result.name, status: 'assigned' } : item) }))
      }
    }
    onToast('已将「' + task.title + '」分配给' + result.name)
    onClose()
  }
  return (
    <ModalFrame title="谁适合负责这个任务？" kicker={'「' + task.title + '」 · 基于技能、质量、效率和当前负载'} wide onClose={onClose}>
      {busy ? (
        <div className="recommend-loading"><span className="loader" />正在计算匹配度…</div>
      ) : (
        <div className="recommend-list">
          {results.map((result, index) => (
            <div className={'recommend-item ' + (index === 0 ? 'best' : '')} key={result.user_id}>
              <div className="rank">{index + 1}</div>
              <div className="recommend-main">
                <div className="recommend-name">
                  <strong>{result.name}</strong>
                  {index === 0 && <span className="best-label">最匹配</span>}
                  <span className="score">{Math.round(result.score)}<small>%</small></span>
                </div>
                <div className="reason-chips">
                  {(result.reasons?.skills || []).slice(0, 2).map(skill => <span key={skill}>{skill}</span>)}
                  <span>质量 {result.reasons?.average_quality || '—'}/5</span>
                  <span>负载 {result.reasons?.current_load || '—'}</span>
                </div>
                <div className="match-track"><i style={{ width: result.score + '%' }} /></div>
              </div>
              <button className="choose-button" onClick={() => choose(result)}>采纳</button>
            </div>
          ))}
        </div>
      )}
      <div className="recommend-foot">
        <span>推荐仅供参考，不会自动替你做决定。</span>
        <button className="ghost-button" onClick={onClose}>手动指定</button>
      </div>
    </ModalFrame>
  )
}

function CreateProjectView({ onCreated }) {
  const [form, setForm] = useState({ name: '', project_type: '课程项目', description: '', start_date: '', end_date: '', owner_name: '', owner_email: '', skills: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  async function submit(event) {
    event.preventDefault()
    setError('')
    if (!form.name.trim() || !form.owner_name.trim()) {
      setError('请填写项目名称和负责人姓名')
      return
    }
    setBusy(true)
    try {
      const owner = await sendJson('/api/users', {
        method: 'POST',
        body: JSON.stringify({
          name: form.owner_name.trim(),
          email: form.owner_email.trim() || null,
          skills: form.skills.split(/[,，]/).map(item => item.trim()).filter(Boolean),
          status: 'online',
        }),
      })
      const project = await sendJson('/api/projects', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name.trim(),
          project_type: form.project_type,
          description: form.description.trim() || null,
          start_date: form.start_date || null,
          end_date: form.end_date || null,
          owner_id: owner.id,
        }),
      })
      await onCreated(project)
    } catch {
      setError('创建失败，请确认后端已启动，并检查项目名称和负责人信息。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="create-project-screen">
      <section className="create-copy">
        <div>
          <div className="wordmark"><b>协作账本</b><span>Ledger</span></div>
          <h1>先立一册，再开始记。</h1>
          <p>项目数据会写入本地 SQLite。之后任务、贡献和 Agent 记忆都会归到这个真实项目。</p>
        </div>
        <p>只记录项目协作成果，不采集私人聊天、桌面、摄像头或键鼠数据。</p>
      </section>
      <form className="create-project-card" onSubmit={submit}>
        <div className="form-row">
          <label>项目名称<input autoFocus value={form.name} onChange={event => update('name', event.target.value)} placeholder="例如：软件工程课程大作业" /></label>
          <label>项目类型
            <select value={form.project_type} onChange={event => update('project_type', event.target.value)}>
              <option>课程项目</option><option>竞赛项目</option><option>科研项目</option><option>其他</option>
            </select>
          </label>
        </div>
        <label>项目简介<textarea value={form.description} onChange={event => update('description', event.target.value)} placeholder="说明项目目标和协作范围" /></label>
        <div className="form-row">
          <label>开始日期<input type="date" value={form.start_date} onChange={event => update('start_date', event.target.value)} /></label>
          <label>结束日期<input type="date" value={form.end_date} onChange={event => update('end_date', event.target.value)} /></label>
        </div>
        <div className="form-section-title">创建者 / 组长</div>
        <div className="form-row">
          <label>姓名<input value={form.owner_name} onChange={event => update('owner_name', event.target.value)} placeholder="例如：张三" /></label>
          <label>邮箱（可选）<input value={form.owner_email} onChange={event => update('owner_email', event.target.value)} placeholder="name@example.com" /></label>
        </div>
        <label>擅长领域（逗号分隔）<input value={form.skills} onChange={event => update('skills', event.target.value)} placeholder="Python，后端，数据分析" /></label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button create-submit" disabled={busy}>{busy ? '正在创建…' : '创建项目并进入工作台'}</button>
      </form>
    </div>
  )
}

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) { console.error('协作账本前端运行时错误', error, info) }
  render() {
    if (this.state.error) {
      return (
        <div className="app-error-screen">
          <div>
            <h1>页面加载遇到问题</h1>
            <p>{this.state.error.message || '未知前端错误'}</p>
            <button className="primary-button" onClick={() => window.location.reload()}>重新加载</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

const root = createRoot(document.getElementById('root'))
root.render(<AppErrorBoundary><App /></AppErrorBoundary>)
