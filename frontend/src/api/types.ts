export type ProjectRole = "owner" | "member" | "viewer";
export type ProjectStatus = "active" | "archived";
export type TaskStatus =
  | "unassigned"
  | "assigned"
  | "in_progress"
  | "paused"
  | "completed"
  | "overdue"
  | "unfinished";
export type ContributionStatus = "pending" | "confirmed" | "disputed";

export interface PageResult<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface CurrentUser {
  id: number;
  name: string;
  email: string;
  skills?: string[];
  status?: string;
}

export interface ProjectSummary {
  id: number;
  name: string;
  description?: string | null;
  project_type?: string | null;
  status: ProjectStatus;
  role: ProjectRole;
  member_count?: number;
  task_count?: number;
  completed_task_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface ProjectMember {
  user_id: number;
  name: string;
  email?: string | null;
  role: ProjectRole;
  skills?: string[];
  max_concurrent_tasks: number;
  status?: string;
}

export interface ProjectTask {
  id: number;
  project_id: number;
  title: string;
  description?: string | null;
  assignee_id?: number | null;
  assignee_name?: string | null;
  reviewer_id?: number | null;
  reviewer_name?: string | null;
  status: TaskStatus;
  priority?: "low" | "medium" | "high";
  task_type?: string | null;
  due_date?: string | null;
  estimated_hours?: number | null;
  actual_hours?: number | null;
  quality?: number | null;
}
