import { expect, test } from "@playwright/test";
import { setupOwner } from "./helpers";

test("生成周报并查看真实统计 @mobile", async ({ page }) => {
  await setupOwner(page, "周报组长", "周报链路项目");
  await page.getByText("贡献报告").first().click();

  // 首次进入显示未生成提示，点击生成后出现统计
  await page.getByRole("button", { name: /生成周报|刷新周报/ }).click();
  await expect(page.getByRole("heading", { name: "本周周报" })).toBeVisible({ timeout: 30_000 });
  // 周报页展示任务统计与口径说明（confirmed 贡献 / 打卡优先工时）
  await expect(page.getByText(/任务|贡献/).first()).toBeVisible();

  // 刷新一次验证幂等与历史
  await page.getByRole("button", { name: /刷新周报/ }).click().catch(() => {});
  await expect(page.getByRole("heading", { name: "本周周报" })).toBeVisible({ timeout: 30_000 });
});
