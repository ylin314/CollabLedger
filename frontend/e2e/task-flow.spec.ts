import { expect, test } from "@playwright/test";
import { createTask, setupOwner } from "./helpers";

test("创建任务并完成开始-完成状态流转", async ({ page }) => {
  await setupOwner(page, "流转组长", "任务流转项目");
  await page.getByText("任务看板").first().click();
  await createTask(page, "编写接口文档", "流转组长");

  await page.getByRole("button", { name: "开始", exact: true }).first().click();
  await expect(page.getByText("进行中").first()).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: "完成任务", exact: true }).first().click();
  await expect(page.getByText("已完成").first()).toBeVisible({ timeout: 10_000 });
});

test("任务详情展示打卡与状态日志 @mobile", async ({ page }) => {
  await setupOwner(page, "打卡组长", "打卡项目");
  await page.getByText("任务看板").first().click();
  await createTask(page, "整理演示脚本", "打卡组长");

  // 今日打卡：填写完成内容并保存
  await page.getByText("今日打卡").first().click();
  await page.getByLabel("关联任务").selectOption({ label: "整理演示脚本" });
  await page.getByPlaceholder("完成了什么、下一步是什么？").fill("完成第一版演示脚本并同步给小组");
  await page.getByRole("button", { name: "保存打卡" }).click();
  await expect(page.getByText(/打卡/).first()).toBeVisible({ timeout: 10_000 });

  // 打开任务详情核对打卡记录
  await page.getByRole("button", { name: "查看", exact: true }).first().click();
  await expect(page.getByText("主动打卡").first()).toBeVisible();
  await expect(page.getByText("完成第一版演示脚本并同步给小组").first()).toBeVisible({ timeout: 10_000 });
});
