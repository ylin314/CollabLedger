import { expect, test } from "@playwright/test";
import { createProject, register, setupOwner, uniqueEmail } from "./helpers";

test("注册新账号并创建项目进入工作台", async ({ page }) => {
  await register(page, "E2E组长", uniqueEmail());
  await createProject(page, "认证链路项目");
  await expect(page.getByText("项目总览").first()).toBeVisible();
  await expect(page.getByText("贡献账本").first()).toBeVisible();
});

test("退出登录后可重新登录同一账号 @mobile", async ({ page }) => {
  const email = await setupOwner(page, "回登组长", "重新登录项目");
  await page.getByText("退出登录").click();
  await expect(page.getByRole("button", { name: "登录并进入工作台 →" })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill("password-123");
  await page.getByRole("button", { name: "登录并进入工作台 →" }).click();
  await expect(page.getByText("任务看板").first()).toBeVisible({ timeout: 20_000 });
});
