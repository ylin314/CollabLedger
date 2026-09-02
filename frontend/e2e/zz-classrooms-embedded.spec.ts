import { expect, test } from "@playwright/test";

test("班级成员保留主布局侧栏", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("http://127.0.0.1:8000/");
  await page.getByLabel("邮箱").fill("owner-demo@example.com");
  await page.getByLabel("密码").fill("password-123");
  await page.getByRole("button", { name: "登录并进入工作台 →" }).click();
  await expect(page.getByRole("button", { name: "任务看板", exact: true })).toBeVisible({ timeout: 20000 });
  await page.getByRole("button", { name: "班级成员" }).first().click();
  await expect(page).toHaveURL(/\/classrooms/);
  await expect(page.locator(".sidebar nav .nav-item.selected")).toContainText("班级成员");
  await expect(page.getByRole("heading", { name: "班级与成员" })).toBeVisible();
  await expect(page.getByRole("button", { name: "任务看板", exact: true })).toBeVisible();
});
