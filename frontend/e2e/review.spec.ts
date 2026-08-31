import { expect, test } from "@playwright/test";
import { createTask, setupOwner } from "./helpers";

test("完成任务并在评价弹窗提交质量评价 @mobile", async ({ page }) => {
  await setupOwner(page, "评价组长", "评价链路项目");
  await page.getByRole("button", { name: "任务看板", exact: true }).click();
  await createTask(page, "输出数据报告", "评价组长");

  await page.getByRole("button", { name: "开始", exact: true }).first().click();
  await expect(page.getByText("进行中").first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "完成任务", exact: true }).first().click();
  await expect(page.getByText("已完成").first()).toBeVisible({ timeout: 10_000 });

  // owner 完成任务后自动弹出评价弹窗（App.jsx complete 分支）
  await expect(page.getByRole("heading", { name: "评价任务交付质量" })).toBeVisible({ timeout: 10_000 });
  await page.locator(".quality-range").fill("4.5");
  await page.getByLabel("评价说明").fill("报告结构完整，数据核对无误");
  await page.getByRole("button", { name: "提交评价" }).click();
  await expect(page.getByRole("heading", { name: "评价任务交付质量" })).toBeHidden({ timeout: 10_000 });

  // 重新打开详情核对持久化评价
  await page.getByRole("button", { name: "查看", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "质量评价" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("报告结构完整，数据核对无误").first()).toBeVisible({ timeout: 10_000 });
});