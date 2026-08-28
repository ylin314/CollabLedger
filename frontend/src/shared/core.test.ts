import { describe, expect, it } from "vitest";
import { absoluteInviteUrl, emailLooksValid, readRoute, routePath } from "./core";

describe("routing helpers", () => {
  it("parses project and invitation routes", () => {
    expect(readRoute("/projects/12/tasks")).toEqual({ projectId: 12, page: "tasks" });
    expect(readRoute("/invite/ABC123")).toEqual({
      projectId: null,
      page: "invite",
      inviteCode: "ABC123",
    });
  });

  it("builds routes and normalizes backend invitation links", () => {
    expect(routePath(3, "history")).toBe("/projects/3/history");
    expect(absoluteInviteUrl({ invite_url: "/invite/CODE" })).toBe(
      `${window.location.origin}/#/invite/CODE`,
    );
  });
});

describe("auth validation", () => {
  it("rejects malformed email addresses", () => {
    expect(emailLooksValid("member@example.com")).toBe(true);
    expect(emailLooksValid("member.example.com")).toBe(false);
  });
});
