import React, { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  BarChart3,
  BookOpenCheck,
  Bot,
  Columns3,
  LayoutDashboard,
  LogOut,
  Plus,
  ReceiptText,
  Search,
  Settings,
  Sparkles,
  TimerReset,
  Users,
  History,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import {
  formatApiError,
  getJson,
  sendJson,
  setUnauthorizedHandler,
} from "./api/client";
import "./styles.css";
import "./design-system.css";
import { initials, nav, readRoute, routePath, statusMeta } from "./shared/core";
import { Overview } from "./features/overview/Overview";
import {
  TasksView,
  TaskModal,
  WorklogModal,
  QualityReviewModal,
} from "./features/tasks/TasksView";
import { ContributionsView } from "./features/contributions/ContributionsView";
import { ReportView } from "./features/reports/ReportView";
import { AgentView } from "./features/agent/AgentView";
import {
  RecommendModal,
  RecommendationsView,
} from "./features/recommendations/RecommendationsView";
import { AuthView } from "./features/auth/AuthView";
import { MembersModal } from "./features/members/MembersModal";
import { CreateProjectView } from "./features/projects/CreateProjectView";
import { ProjectSettingsView } from "./features/projects/ProjectSettingsView";
import { InviteAcceptView } from "./features/invitations/InviteAcceptView";
import { HistoryProjectsView } from "./features/projects/HistoryProjectsView";
import { ClassroomsView } from "./features/classrooms/ClassroomsView";
import { projectListQuery, workspaceQuery } from "./app/queries";
import { taskPermissions } from "./app/permissions";

const navIcons = {
  overview: LayoutDashboard,
  tasks: Columns3,
  recommendations: Sparkles,
  contributions: BookOpenCheck,
  report: BarChart3,
  agent: Bot,
};

function NavGlyph({ id }) {
  const Icon = navIcons[id];
  return <Icon aria-hidden="true" />;
}

function App() {
  const location = useLocation();
  const routerNavigate = useNavigate();
  const queryClient = useQueryClient();
  const [auth, setAuth] = useState(undefined);
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState(null);
  const [report, setReport] = useState(null);
  const [risks, setRisks] = useState(null);
  const [memberLoad, setMemberLoad] = useState(null);
  const [weekly, setWeekly] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [online, setOnline] = useState(Boolean(auth));
  const [showTask, setShowTask] = useState(false);
  const [recommendTask, setRecommendTask] = useState(null);

  const [reviewTask, setReviewTask] = useState(null);
  const [toast, setToast] = useState("");
  const [query, setQuery] = useState("");

  const route = useMemo(() => readRoute(location.pathname), [location.pathname]);
  const active = route.page;
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAuth(null);
      setProject(null);
      setProjects([]);
      setReport(null);
      setRisks(null);
      setMemberLoad(null);
      setWeekly(null);
      setLoadError("");
      queryClient.clear();
      routerNavigate("/login", { replace: true });
    });
    let cancelled = false;
    getJson("/api/auth/me")
      .then((user) => {
        if (!cancelled) setAuth(user);
      })
      .catch((error) => {
        if (!cancelled && error.status !== 401) {
          setOnline(false);
          setAuth(null);
        }
      });
    return () => {
      cancelled = true;
      setUnauthorizedHandler(null);
    };
  }, []);
  useEffect(() => {
    if (auth && route.page !== "invite")
      loadProject(route.projectId || undefined);
  }, [auth]);
  useEffect(() => {
    if (auth && route.projectId && project?.id !== route.projectId)
      loadProject(route.projectId);
  }, [auth, route.projectId]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 2600);
      return () => clearTimeout(t);
    }
  }, [toast]);

  async function loadProject(id) {
    setLoading(true);
    setLoadError("");
    try {
      const list = await queryClient.fetchQuery(projectListQuery);
      setProjects(list);
      const rememberedId = Number(localStorage.getItem("collab_project_id"));
      const selectedId =
        id ||
        (list.some((item) => item.id === rememberedId) ? rememberedId : null) ||
        list[0]?.id;
      if (!selectedId) {
        setProject(null);
        setReport(null);
        setRisks(null);
        setMemberLoad(null);
        setWeekly(null);
        setOnline(true);
        if (route.page !== "classrooms" && window.location.hash !== "#/projects/new")
          routerNavigate(routePath(null, "new"), { replace: true });
        return;
      }
      const workspace = await queryClient.fetchQuery(workspaceQuery(selectedId));
      setProject(workspace.project);
      setReport(workspace.report);
      setRisks(workspace.risks);
      setMemberLoad(workspace.memberLoad);
      setWeekly(workspace.weekly);
      setOnline(true);
      localStorage.setItem("collab_project_id", String(selectedId));
      if (route.projectId !== selectedId) {
        const page = route.page === "new" ? "overview" : route.page;
        routerNavigate(routePath(selectedId, page), { replace: true });
      }
    } catch (error) {
      if (error.status === 401) return;
      setOnline(false);
      setLoadError(error.message);
      setToast(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    try {
      await sendJson("/api/auth/logout", { method: "POST" });
    } catch (error) {
      if (error.status !== 401) setToast(error.message);
    } finally {
      setAuth(null);
      setProject(null);
      setProjects([]);
      setReport(null);
      setRisks(null);
      setMemberLoad(null);
      setWeekly(null);
      queryClient.clear();
      routerNavigate("/login", { replace: true });
    }
  }
  function navigate(page, projectId = project?.id, replace = false) {
    routerNavigate(routePath(projectId, page), { replace });
  }
  async function afterProject(created) {
    navigate("overview", created.id);
    await loadProject(created.id);
  }
  const tasks = project?.tasks || [];
  const members = project?.members || [];
  const role =
    project?.current_user_role ||
    projects.find((item) => item.id === project?.id)?.role;
  const isOwner = role === "owner";
  const basePermissions = taskPermissions({
    role,
    projectStatus: project?.status,
    currentUserId: auth?.id,
  });
  const canWrite = basePermissions.writable;
  const canAssign = canWrite;
  const permissionsForTask = (task) =>
    taskPermissions({
      role,
      projectStatus: project?.status,
      currentUserId: auth?.id,
      assigneeId: task.assignee_id,
      participantIds: task.participant_ids || [],
      creatorId: task.created_by,
      reviewerId: task.reviewer_id,
      taskStatus: task.status,
    });
  const canManageTask = (task) => permissionsForTask(task).canManage;
  const canEditTask = (task) => permissionsForTask(task).canEdit;
  const canDeleteTask = (task) => permissionsForTask(task).canDelete;
  const canReviewTask = (task) => permissionsForTask(task).canReview;
  useEffect(() => {
    if (!project) return;
    const ownerOnly = active === "members" || active === "settings";
    if ((ownerOnly && !isOwner) || (active === "worklog" && !canWrite)) {
      navigate("overview", project.id, true);
      setToast("当前账号没有这个页面的操作权限");
    }
  }, [active, canWrite, isOwner, project?.id]);
  const activeTasks = tasks.filter((t) =>
    ["assigned", "in_progress", "paused"].includes(t.status),
  );
  const completed =
    project?.statistics?.completed_task_count ??
    tasks.filter((t) => t.status === "completed").length;
  const overdue =
    project?.statistics?.overdue_task_count ??
    tasks.filter((t) => ["overdue", "unfinished"].includes(t.status)).length;
  const progress =
    project?.statistics?.progress ??
    (tasks.length ? Math.round((completed / tasks.length) * 100) : 0);
  const memberStats = useMemo(
    () =>
      members.map((m) => ({
        ...m,
        current:
          m.current_task_count ??
          tasks.filter(
            (t) =>
              t.assignee_id === m.user_id &&
              ["assigned", "in_progress", "paused"].includes(t.status),
          ).length,
        done: tasks.filter(
          (t) => t.assignee_id === m.user_id && t.status === "completed",
        ).length,
        quality: (() => {
          const q = tasks
            .filter((t) => t.assignee_id === m.user_id && t.quality != null)
            .map((t) => t.quality);
          return q.length
            ? (q.reduce((a, b) => a + b, 0) / q.length).toFixed(1)
            : "—";
        })(),
      })),
    [members, tasks],
  );

  if (auth === undefined)
    return (
      <div className="auth-screen">
        <div className="loading-state">正在恢复登录状态…</div>
      </div>
    );
  if (!auth) return <AuthView onAuthenticated={(user) => setAuth(user)} />;
  if (route.page === "invite")
    return (
      <InviteAcceptView
        code={route.inviteCode}
        currentUser={auth}
        onCancel={() => {
          routerNavigate(routePath(project?.id, "overview"), { replace: true });
          loadProject(project?.id || undefined);
        }}
        onAccepted={async (projectId) => {
          routerNavigate(routePath(projectId, "overview"), { replace: true });
          await loadProject(projectId);
          setToast("已加入项目");
        }}
      />
    );
  if (route.page === "classrooms") return <ClassroomsView currentUser={auth} onToast={setToast} onBack={() => routerNavigate(project?.id ? routePath(project.id, "overview") : "/projects/new")} />;
  if (!loading && loadError && !project)
    return (
      <div className="app-error-screen">
        <div>
          <div className="brand-mark">!</div>
          <h1>项目加载失败</h1>
          <p>{loadError}</p>
          <button
            className="primary-button"
            onClick={() => loadProject(route.projectId || undefined)}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  if (!loading && (!project || route.page === "new"))
    return (
      <CreateProjectView
        currentUser={auth}
        onCreated={afterProject}
        onCancel={
          project ? () => navigate("overview", project.id, true) : undefined
        }
      />
    );

  async function taskAction(task, action) {
    try {
      const updated = await sendJson(`/api/tasks/${task.id}/${action}`, {
        method: "POST",
      });
      setProject((p) => ({
        ...p,
        tasks: p.tasks.map((t) =>
          t.id === task.id ? { ...t, ...updated } : t,
        ),
      }));
      setToast(
        `已将「${task.title}」标记为${statusMeta[updated.status]?.label || "已更新"}`,
      );
      if (action === "complete" && isOwner)
        setReviewTask({ ...task, ...updated });
      await loadProject(project.id);
    } catch (error) {
      setToast(error.message);
    }
  }
  async function createTask(data) {
    try {
      const t = await sendJson(`/api/projects/${project.id}/tasks`, {
        method: "POST",
        body: JSON.stringify(data),
      });
      setProject((p) => ({ ...p, tasks: [t, ...p.tasks] }));
      setToast("任务已创建");
    } catch (error) {
      setToast(error.message);
    }
    setShowTask(false);
  }
  async function updateTask(task, data) {
    try {
      const updated = await sendJson(`/api/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      setProject((current) => ({
        ...current,
        tasks: current.tasks.map((item) =>
          item.id === task.id ? { ...item, ...updated } : item,
        ),
      }));
      setToast("任务已更新");
      await loadProject(project.id);
      return true;
    } catch (error) {
      setToast(formatApiError(error));
      return false;
    }
  }
  async function deleteTask(task) {
    try {
      await sendJson(`/api/tasks/${task.id}`, { method: "DELETE" });
      setProject((current) => ({
        ...current,
        tasks: current.tasks.filter((item) => item.id !== task.id),
      }));
      setToast("任务已删除");
      await loadProject(project.id);
      return true;
    } catch (error) {
      setToast(formatApiError(error));
      return false;
    }
  }
  async function changeProject(id) {
    navigate("overview", id);
    await loadProject(id);
  }
  async function afterProjectDeleted(projectId) {
    const remaining = projects.filter((item) => item.id !== projectId);
    localStorage.removeItem("collab_project_id");
    setProjects(remaining);
    setToast("项目已删除");
    if (remaining.length) {
      const next = remaining.find((item) => item.status === "active") || remaining[0];
      navigate("overview", next.id, true);
      await loadProject(next.id);
      return;
    }
    setProject(null);
    setReport(null);
    setRisks(null);
    setMemberLoad(null);
    setWeekly(null);
    routerNavigate(routePath(null, "new"), { replace: true });
  }

  return (
    <div className="app-shell" data-page={active}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><ReceiptText aria-hidden="true" /></div>
          <div>
            <div className="brand-name">协作账本</div>
            <div className="brand-sub">COLLAB LEDGER</div>
          </div>
        </div>
        <div className="workspace-label">我的工作区</div>
        <div className="project-switch">
          <select
            value={project?.id || ""}
            onChange={(e) => changeProject(Number(e.target.value))}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}{p.status === "archived" ? "（已归档）" : ""}
              </option>
            ))}
          </select>
        </div>
        <nav>
          {nav.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${active === item.id ? "selected" : ""}`}
              onClick={() => navigate(item.id)}
            >
              <span className="nav-icon"><NavGlyph id={item.id} /></span>
              <span>{item.label}</span>
              {item.id === "tasks" && overdue > 0 && (
                <span className="nav-badge">{overdue}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-tools">
          {isOwner && (
            <button className="side-tool" onClick={() => navigate("members")}>
              <Users aria-hidden="true" /> 成员管理
            </button>
          )}
          {isOwner && (
            <button className="side-tool" onClick={() => navigate("settings")}>
              <Settings aria-hidden="true" /> 项目设置
            </button>
          )}
          <button className="side-tool" onClick={() => navigate("history")}>
            <History aria-hidden="true" /> 历史项目
          </button>
          <button className="side-tool" onClick={() => routerNavigate("/classrooms")}><Users aria-hidden="true" /> 班级成员</button>
          {canWrite && (
            <button className="side-tool" onClick={() => navigate("worklog")}>
              <TimerReset aria-hidden="true" /> 今日打卡
            </button>
          )}
          <button className="side-tool" onClick={() => navigate("new")}>
            <Plus aria-hidden="true" /> 新建项目
          </button>
        </div>
        <div className="sidebar-bottom">
          <button className="profile-chip" onClick={logout}>
            <div className="avatar avatar-me">{initials(auth.name)}</div>
            <div>
              <strong>{auth.name}</strong>
              <span>退出登录</span>
            </div>
            <LogOut className="more" aria-hidden="true" />
          </button>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            {active !== "overview" && (
              <button
                className="back-button"
                onClick={() => routerNavigate(-1)}
              >
                <ArrowLeft aria-hidden="true" /> 返回
              </button>
            )}
            <span>项目空间</span>
            <b>/</b>
            <strong>
              {nav.find((n) => n.id === active)?.label ||
                {
                  members: "成员管理",
                  worklog: "今日打卡",
                  settings: "项目设置",
                  history: "历史项目",
                  new: "新建项目",
                  recommendations: "任务推荐",
                }[active] ||
                "项目总览"}
            </strong>
          </div>
          <div className="top-actions">
            <div className="search">
              <Search aria-hidden="true" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索任务、成员…"
              />
            </div>
          </div>
        </header>
        <div className="page-wrap">
          {loading ? (
            <div className="loading-state">正在加载项目空间…</div>
          ) : (
            <>
              {active === "overview" && (
                <Overview
                  auth={auth}
                  project={project}
                  memberStats={memberStats}
                  tasks={tasks}
                  progress={progress}
                  completed={completed}
                  overdue={overdue}
                  activeTasks={activeTasks}
                  online={online}
                  risks={risks}
                  weekly={weekly}
                  memberLoad={memberLoad}
                  onNavigate={navigate}
                  onAction={taskAction}
                  onRecommend={setRecommendTask}
                  canWrite={canWrite}
                  canManageTask={canManageTask}
                />
              )}{" "}
              {active === "tasks" && (
                <TasksView
                  tasks={tasks.filter(
                    (t) =>
                      !query ||
                      `${t.title}${t.assignee_name || ""}`.includes(query),
                  )}
                  members={members}
                  onAction={taskAction}
                  onCreate={() => setShowTask(true)}
                  onRecommend={setRecommendTask}
                  onBatch={() => navigate("recommendations")}
                  canWrite={canWrite}
                  canManageTask={canManageTask}
                  canEditTask={canEditTask}
                  canDeleteTask={canDeleteTask}
                  onUpdate={updateTask}
                  onDelete={deleteTask}
                  canReviewTask={canReviewTask}
                  onReview={setReviewTask}
                  onToast={setToast}
                  project={project}
                  onReload={() => loadProject(project.id)}
                />
              )}{" "}
              {active === "recommendations" && (
                <RecommendationsView
                  project={project}
                  members={members}
                  canWrite={canWrite}
                  currentUserId={auth?.id}
                  onRecommend={setRecommendTask}
                  onToast={setToast}
                  setProject={setProject}
                  onReload={() => loadProject(project.id)}
                />
              )}{" "}
              {active === "contributions" && (
                <ContributionsView
                  project={project}
                  members={members}
                  currentUser={auth}
                  role={role}
                  canWrite={canWrite}
                  online={online}
                  setProject={setProject}
                  onToast={setToast}
                  onReload={() => loadProject(project.id)}
                />
              )}{" "}
              {active === "report" && (
                <ReportView
                  project={project}
                  report={report}
                  memberStats={memberStats}
                  tasks={tasks}
                  weekly={weekly}
                  risks={risks}
                />
              )}{" "}
              {active === "agent" && (
                <AgentView
                  project={project}
                  tasks={tasks}
                  online={online}
                  onRecommend={setRecommendTask}
                  role={role}
                />
              )}
              {active === "members" && isOwner && (
                <MembersModal
                  project={project}
                  currentUser={auth}
                  onClose={() => navigate("overview", project.id, true)}
                  onUpdated={(detail) =>
                    setProject((p) => ({
                      ...p,
                      members: detail.members || p.members,
                    }))
                  }
                  onToast={setToast}
                />
              )}{" "}
              {active === "settings" && isOwner && (
                <ProjectSettingsView
                  project={project}
                  onSaved={(updated) => {
                    setProject((current) => ({ ...current, ...updated }));
                    setProjects((items) =>
                      items.map((item) =>
                        item.id === updated.id ? { ...item, ...updated } : item,
                      ),
                    );
                  }}
                  onReload={() => loadProject(project.id)}
                  onDeleted={afterProjectDeleted}
                  onToast={setToast}
                />
              )}{" "}
              {active === "history" && (
                <HistoryProjectsView
                  projects={projects.filter((item) => item.status === "archived")}
                  onOpen={(projectId) => navigate("overview", projectId)}
                />
              )}{" "}
              {active === "worklog" && canWrite && (
                <WorklogModal
                  project={project}
                  tasks={tasks}
                  role={role}
                  user={auth}
                  onClose={() => navigate("overview", project.id, true)}
                  onToast={setToast}
                  onSaved={() => loadProject(project.id)}
                />
              )}
            </>
          )}
        </div>
      </main>
      {showTask && canWrite && (
        <TaskModal
          members={members}
          project={project}
          onClose={() => setShowTask(false)}
          onSave={createTask}
          onToast={setToast}
        />
      )}{" "}
      {recommendTask && canWrite && (
        <RecommendModal
          task={recommendTask}
          project={project}
          members={members}
          onClose={() => setRecommendTask(null)}
          onToast={setToast}
          setProject={setProject}
          currentUserId={auth?.id}
        />
      )}{" "}
      {reviewTask && (
        <QualityReviewModal
          project={project}
          task={reviewTask}
          currentUser={auth}
          members={members}
          onClose={() => setReviewTask(null)}
          onSaved={(quality) => {
            setProject((p) => ({
              ...p,
              tasks: p.tasks.map((t) =>
                t.id === reviewTask.id ? { ...t, quality } : t,
              ),
            }));
            setToast("质量评价已记录");
          }}
        />
      )}{" "}
      {toast && <div className="toast"><BookOpenCheck aria-hidden="true" /> {toast}</div>}
    </div>
  );
}

export class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("协作账本前端运行时错误", error, info);
  }
  render() {
    if (this.state.error)
      return (
        <div className="app-error-screen">
          <div>
            <div className="brand-mark">!</div>
            <h1>页面加载遇到问题</h1>
            <p>{this.state.error.message || "未知前端错误"}</p>
            <button
              className="primary-button"
              onClick={() => window.location.reload()}
            >
              重新加载
            </button>
          </div>
        </div>
      );
    return this.props.children;
  }
}

export default App;
