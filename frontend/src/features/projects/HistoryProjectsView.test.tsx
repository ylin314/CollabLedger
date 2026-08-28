import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HistoryProjectsView } from "./HistoryProjectsView";

describe("HistoryProjectsView", () => {
  it("renders archived project facts from API data", () => {
    render(
      <HistoryProjectsView
        projects={[
          {
            id: 9,
            name: "课程设计",
            role: "owner",
            status: "archived",
            description: "已结束的协作项目",
            member_count: 4,
            task_count: 10,
            completed_task_count: 8,
            updated_at: "2026-08-20T10:00:00Z",
          },
        ]}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "历史项目" })).toBeInTheDocument();
    expect(screen.getByText("课程设计")).toBeInTheDocument();
    expect(screen.getByText("8 / 10 项完成")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
  });
});
