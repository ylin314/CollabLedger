import { queryOptions } from "@tanstack/react-query";
import { getJson } from "../api/client";
import type { PageResult, ProjectMember, ProjectSummary, ProjectTask } from "../api/types";

export interface WorkspaceProject extends ProjectSummary {
  owner_id?: number;
  current_user_role?: string;
  statistics?: Record<string, number>;
  members: Array<ProjectMember & Record<string, unknown>>;
  tasks: Array<ProjectTask & Record<string, unknown>>;
  contributions: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface WorkspacePayload {
  project: WorkspaceProject;
  report: Record<string, unknown> | null;
  risks: Record<string, unknown> | null;
  memberLoad: Record<string, unknown> | null;
  weekly: Record<string, unknown> | null;
  diagnostics: {
    risksError: string | null;
    memberLoadError: string | null;
  };
}

async function optionalJson(url: string) {
  try {
    return await getJson<Record<string, unknown>>(url);
  } catch (error) {
    if ((error as { status?: number }).status === 401) throw error;
    return null;
  }
}

async function diagnosticJson(url: string) {
  try {
    return {
      data: await getJson<Record<string, unknown>>(url),
      error: null,
    };
  } catch (error) {
    if ((error as { status?: number }).status === 401) throw error;
    return {
      data: null,
      error: (error as { message?: string }).message || "接口加载失败",
    };
  }
}
export const projectListQuery = queryOptions({
  queryKey: ["projects"],
  staleTime: 0,
  queryFn: async () => {
    const [active, archived] = await Promise.all([
      getJson<PageResult<ProjectSummary>>("/api/projects?page_size=100"),
      getJson<PageResult<ProjectSummary>>(
        "/api/projects?archived=true&page_size=100",
      ),
    ]);
    return [...(active.items || []), ...(archived.items || [])];
  },
});

export function workspaceQuery(projectId: number) {
  return queryOptions({
    queryKey: ["workspace", projectId],
    staleTime: 0,
    queryFn: async (): Promise<WorkspacePayload> => {
      const [detail, membersPayload, tasksPayload, contributionsPayload] =
        await Promise.all([
          getJson<Record<string, unknown>>(`/api/projects/${projectId}`),
          getJson<PageResult<ProjectMember>>(`/api/projects/${projectId}/members`),
          getJson<PageResult<ProjectTask>>(
            `/api/projects/${projectId}/tasks?page_size=100`,
          ),
          getJson<PageResult<Record<string, unknown>>>(
            `/api/projects/${projectId}/contributions?page_size=100`,
          ),
        ]);
      const [report, riskResult, loadResult, weekly] = await Promise.all([
        optionalJson(`/api/projects/${projectId}/report`),
        diagnosticJson(`/api/projects/${projectId}/risks`),
        diagnosticJson(`/api/projects/${projectId}/members/load`),
        optionalJson(`/api/projects/${projectId}/weekly-report`),
      ]);
      const risks = riskResult.data;
      const memberLoad = loadResult.data;
      const loadMembers = Array.isArray(memberLoad?.members)
        ? memberLoad.members
        : [];
      const loadByUser = Object.fromEntries(
        loadMembers.map((item) => {
          const row = item as Record<string, unknown>;
          return [Number(row.user_id), row];
        }),
      );
      const members = (membersPayload.items || []).map((member) => ({
        ...member,
        id: member.user_id,
        ...(loadByUser[member.user_id] || {}),
      }));
      const names = Object.fromEntries(
        members.map((member) => [member.user_id, member.name]),
      );
      const tasks = (tasksPayload.items || []).map((task) => ({
        ...task,
        assignee_name: task.assignee_name || names[Number(task.assignee_id)] || null,
      }));

      return {
        project: {
          ...detail,
          members,
          tasks,
          contributions: contributionsPayload.items || [],
        } as unknown as WorkspaceProject,
        report,
        risks,
        memberLoad,
        weekly,
        diagnostics: {
          risksError: riskResult.error,
          memberLoadError: loadResult.error,
        },
      };
    },
  });
}
