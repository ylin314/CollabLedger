import { describe, expect, it } from "vitest";
import { taskPermissions } from "./permissions";

describe("taskPermissions", () => {
  it("allows the owner to edit and delete active-project tasks", () => {
    expect(
      taskPermissions({ role: "owner", projectStatus: "active", currentUserId: 1 }),
    ).toEqual({
      writable: true,
      canEdit: true,
      canManage: true,
      canDelete: true,
      canReview: false,
    });
  });

  it("limits members to assigned or self-created tasks", () => {
    expect(
      taskPermissions({
        role: "member",
        projectStatus: "active",
        currentUserId: 7,
        assigneeId: 7,
        creatorId: 8,
      }),
    ).toEqual({
      writable: true,
      canEdit: false,
      canManage: true,
      canDelete: false,
      canReview: false,
    });
  });

  it("makes archived projects read-only", () => {
    expect(
      taskPermissions({ role: "owner", projectStatus: "archived", currentUserId: 1 }),
    ).toEqual({
      writable: false,
      canEdit: false,
      canManage: false,
      canDelete: false,
      canReview: false,
    });
  });

  it("allows a designated reviewer to review a completed task", () => {
    expect(
      taskPermissions({
        role: "member",
        projectStatus: "active",
        currentUserId: 7,
        assigneeId: 8,
        reviewerId: 7,
        taskStatus: "completed",
      }).canReview,
    ).toBe(true);
  });
});
