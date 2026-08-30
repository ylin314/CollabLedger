import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getJson, sendJson } from "../../api/client";
import { RecommendModal } from "./RecommendationsView";

vi.mock("../../api/client", () => ({
  getJson: vi.fn(),
  sendJson: vi.fn(),
}));

const mockedGetJson = vi.mocked(getJson);
const mockedSendJson = vi.mocked(sendJson);

describe("RecommendModal reviewer confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates only the assignee until the user explicitly confirms a reviewer", async () => {
    mockedGetJson.mockResolvedValue({
      recommendation_id: 31,
      recommendations: [
        {
          user_id: 2,
          name: "后端同学",
          score: 91,
          source: "rule",
          dimensions: {},
          reasons: { summary: "技能与负载匹配" },
        },
      ],
      disclaimer: "推荐仅供参考",
    });
    mockedSendJson
      .mockResolvedValueOnce({
        changed: false,
        task: { id: 7, assignee_id: 2, reviewer_id: null },
      })
      .mockResolvedValueOnce({
        id: 7,
        assignee_id: 2,
        reviewer_id: 1,
      });

    const onClose = vi.fn();
    render(
      <RecommendModal
        task={{ id: 7, title: "实现推荐接口", created_by: 1 }}
        project={{ id: 4, owner_id: 1, tasks: [] }}
        members={[
          { user_id: 1, name: "组长", role: "owner" },
          { user_id: 2, name: "后端同学", role: "member" },
        ]}
        currentUserId={1}
        onClose={onClose}
        onToast={vi.fn()}
        setProject={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "采纳" }));

    expect(
      await screen.findByRole("heading", { name: "负责人已更新" }),
    ).toBeInTheDocument();
    expect(mockedSendJson).toHaveBeenCalledTimes(1);
    expect(mockedSendJson).toHaveBeenNthCalledWith(
      1,
      "/api/projects/4/recommendations/31/decide",
      expect.objectContaining({ method: "POST" }),
    );
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("选择评审人（可跳过）"), {
      target: { value: "1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认设置评审人" }));

    await waitFor(() => expect(mockedSendJson).toHaveBeenCalledTimes(2));
    expect(mockedSendJson).toHaveBeenNthCalledWith(2, "/api/tasks/7", {
      method: "PATCH",
      body: JSON.stringify({ reviewer_id: 1 }),
    });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
