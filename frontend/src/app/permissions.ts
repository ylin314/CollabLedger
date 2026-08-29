import type { ProjectRole, ProjectStatus, TaskStatus } from "../api/types";

interface TaskPermissionInput {
  role?: ProjectRole | null;
  projectStatus?: ProjectStatus | null;
  currentUserId?: number | null;
  assigneeId?: number | null;
  participantIds?: number[];
  creatorId?: number | null;
  reviewerId?: number | null;
  taskStatus?: TaskStatus | null;
}

export function taskPermissions({
  role,
  projectStatus,
  currentUserId,
  assigneeId,
  participantIds = [],
  creatorId,
  reviewerId,
  taskStatus,
}: TaskPermissionInput) {
  const writable =
    projectStatus !== "archived" && (role === "owner" || role === "member");
  const owner = role === "owner";
  return {
    writable,
    canEdit: writable && owner,
    canManage: writable && (owner || assigneeId === currentUserId || participantIds.includes(currentUserId || -1)),
    canDelete: writable && (owner || creatorId === currentUserId),
    canReview:
      writable &&
      taskStatus === "completed" &&
      (owner || (reviewerId === currentUserId && assigneeId !== currentUserId)),
  };
}
