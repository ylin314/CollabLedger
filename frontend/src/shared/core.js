const statusMeta = {
  unassigned: { label: "未分配", tone: "slate" },
  assigned: { label: "待开始", tone: "blue" },
  in_progress: { label: "进行中", tone: "amber" },
  paused: { label: "已暂停", tone: "purple" },
  completed: { label: "已完成", tone: "green" },
  overdue: { label: "延期", tone: "red" },
  unfinished: { label: "未完成", tone: "red" },
};

const nav = [
  { id: "overview", label: "项目总览" },
  { id: "tasks", label: "任务看板" },
  { id: "recommendations", label: "任务推荐" },
  { id: "contributions", label: "贡献账本" },
  { id: "report", label: "贡献报告" },
  { id: "agent", label: "协作 Agent" },
  { id: "classrooms", label: "班级成员" },
];

const avatarColors = ["#f5d84a", "#bfd0ff", "#8be1c2", "#ffb5a3", "#d8c6ff"];

function emailLooksValid(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

function initials(name = "") {
  return name.slice(0, 1);
}

function greetingTitle(name) {
  return `你好，${name || "同学"}`;
}

function memberAvatar(member) {
  return member?.avatar_url || member?.assignee_avatar_url || "";
}

const routePages = new Set([
  "overview",
  "tasks",
  "recommendations",
  "contributions",
  "report",
  "agent",
  "members",
  "worklog",
  "settings",
  "history",
  "invite",
  "new",
  "classrooms",
]);

function readRoute(source = window.location.hash) {
  const raw = String(source).replace(/^#?\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "classrooms") return { projectId: null, page: "classrooms" };
  if (parts[0] === "invite" && parts[1])
    return {
      projectId: null,
      page: "invite",
      inviteCode: decodeURIComponent(parts[1]),
    };
  if (parts[0] === "projects" && parts[1] === "new")
    return { projectId: null, page: "new" };
  if (parts[0] === "projects" && parts[1]) {
    const projectId = Number(parts[1]);
    const page = routePages.has(parts[2]) ? parts[2] : "overview";
    return { projectId: Number.isFinite(projectId) ? projectId : null, page };
  }
  return { projectId: null, page: "overview" };
}

function routeHash(projectId, page = "overview") {
  return `#${routePath(projectId, page)}`;
}

function routePath(projectId, page = "overview") {
  if (page === "classrooms")
    return projectId ? `/projects/${projectId}/classrooms` : "/classrooms";
  return projectId ? `/projects/${projectId}/${page}` : "/projects/new";
}

function absoluteInviteUrl(invite) {
  const code = invite?.invite_code || invite?.code || invite?.token;
  const route =
    invite?.invite_url ||
    invite?.url ||
    (code ? `/#/invite/${encodeURIComponent(code)}` : "");
  if (!route) return "";
  const normalizedRoute = route.startsWith("/invite/") ? `/#${route}` : route;
  try {
    return new URL(normalizedRoute, window.location.origin).href;
  } catch {
    return normalizedRoute;
  }
}

async function copyText(value) {
  if (!value) return false;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  return copied;
}

function dimLabel(key) {
  return (
    { skill: "技能", quality: "质量", efficiency: "效率", load: "负载" }[key] ||
    key
  );
}

function sourceLabel(value) {
  return (
    {
      rule: "规则",
      llm: "AI 语义",
      embedding: "向量语义",
      hybrid: "规则 + AI",
    }[value] ||
    value ||
    "规则"
  );
}

export {
  statusMeta,
  nav,
  avatarColors,
  emailLooksValid,
  initials,
  greetingTitle,
  memberAvatar,
  routePages,
  readRoute,
  routeHash,
  routePath,
  absoluteInviteUrl,
  copyText,
  dimLabel,
  sourceLabel,
};

