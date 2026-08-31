import { expect, test } from "@playwright/test";
import { createTask, setupOwner } from "./helpers";

test("生成未分配任务推荐并采纳指派负责人", async ({ page }) => {
  await setupOwner(page, "推荐组长", "推荐链路项目");
  const projectId = await page.evaluate(async () => {
    const response = await fetch("/api/projects?page_size=100");
    const payload = await response.json();
    return payload.items[0].id;
  });
  const memberResponse = await page.evaluate(async (id) => {
    const response = await fetch(`/api/projects/${id}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "推荐成员", email: "recommendation-member@example.com", skills: ["前端开发"], role: "member" }),
    });
    return { status: response.status, body: await response.json() };
  }, projectId);
  expect(memberResponse.status, JSON.stringify(memberResponse.body)).toBe(201);
  await page.getByRole("button", { name: "任务看板", exact: true }).click();
  // 未分配任务才会进入推荐池
  await createTask(page, "调研竞品功能");

  await page.getByText("任务推荐").first().click();
  await page.getByRole("button", { name: "一键给所有未分配任务出建议" }).click();

  // 推荐卡片出现后采纳（规则模式也会给出建议与理由）
  const acceptButton = page.getByRole("button", { name: "采纳", exact: true }).first();
  await expect(acceptButton).toBeVisible({ timeout: 30_000 });
  await acceptButton.click();

  // 采纳只改负责人，评审人提示可显式跳过
  const skipReviewer = page.getByRole("button", { name: "跳过", exact: true });
  if (await skipReviewer.isVisible().catch(() => false)) {
    await skipReviewer.click();
  }

  await page.getByRole("button", { name: "任务看板", exact: true }).click();
  const taskCard = page.locator(".task-card").filter({ hasText: "调研竞品功能" });
  await expect(taskCard).toBeVisible();
  // 任务已被指派给组长（卡片显示负责人而非“尚未分配”）
  await expect(taskCard.getByText("推荐成员")).toBeVisible({ timeout: 10_000 });
});
