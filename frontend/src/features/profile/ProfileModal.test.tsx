import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getJson, sendJson } from "../../api/client";
import { ProfileModal } from "./ProfileModal";

vi.mock("../../api/client", () => ({
  getJson: vi.fn(),
  sendJson: vi.fn(),
}));

const mockedGetJson = vi.mocked(getJson);
const mockedSendJson = vi.mocked(sendJson);

const profile = {
  user_id: 2,
  name: "后端同学",
  project_count: 1,
  completed_task_count: 1,
  average_quality: 4.5,
  quality_samples: 1,
  efficiency: 0.75,
  efficiency_samples: 1,
  on_time_rate: 1,
  on_time_samples: 1,
  contributions_total: 1,
  skill_families: [],
  skill_strength: {},
  declared_skills: ["后端"],
  data_sources: [{ source: "confirmed_contributions", count: 1 }],
  source_projects: [{ project_id: 4 }],
  calculation_notes: { contributions: "只统计 confirmed" },
  generated_at: "2026-08-30T10:00:00Z",
};

const authorization = {
  global_enabled: true,
  data_status: "retained",
  projects: [
    {
      project_id: 4,
      project_name: "真实项目",
      project_status: "active",
      membership_status: "active",
      override: null,
      enabled: true,
    },
  ],
};

describe("ProfileModal authorization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGetJson
      .mockResolvedValueOnce(profile)
      .mockResolvedValueOnce(authorization)
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ recommendations: [] })
      .mockResolvedValueOnce(profile)
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ recommendations: [] });
    mockedSendJson.mockResolvedValue({ ...authorization, global_enabled: false, data_status: "frozen" });
  });

  it("uses an explicit switch to freeze cross-project analysis", async () => {
    render(<ProfileModal user={{ id: 2, name: "后端同学" }} isSelf onClose={vi.fn()} />);

    const toggle = await screen.findByRole("checkbox", { name: "同团队跨项目默认可用" });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(mockedSendJson).toHaveBeenCalledWith("/api/users/me/authorizations", {
        method: "PATCH",
        body: JSON.stringify({ global_enabled: false }),
      }),
    );
    expect(await screen.findByText(/已冻结保留/)).toBeInTheDocument();
  });
});