import { expect, test } from "@playwright/test";
import { register, setupOwner, uniqueEmail } from "./helpers";

test("生成邀请链接并由新成员接受加入 @mobile", async ({ page }) => {
  await setupOwner(page, "邀请组长", "邀请链路项目");

  await page.getByRole("button", { name: "成员管理", exact: true }).click();
  await expect(page.getByText("添加成员")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "生成邀请链接", exact: true }).click();
  await expect(page.getByText("邀请已生成")).toBeVisible({ timeout: 10_000 });
  const inviteUrl = await page.locator(".invite-result span").first().getAttribute("title");
  expect(inviteUrl).toContain("/invite/");
  const code = inviteUrl!.split("/invite/")[1]!;

  // 关闭成员管理弹窗后退出登录
  await page.getByRole("button", { name: "×", exact: true }).click();
  await expect(page.getByText("添加成员")).toBeHidden({ timeout: 10_000 });
  await page.getByText("退出登录").click();
  await expect(page.getByRole("button", { name: "登录并进入工作台 →" })).toBeVisible({ timeout: 10_000 });

  await register(page, "受邀成员", uniqueEmail("invitee"));

  // 打开邀请链接并接受
  await page.goto(`/#/invite/${encodeURIComponent(code)}`);
  await expect(page.getByText("接受后即可进入项目空间")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /接受邀请/ }).click();

  // 受邀成员进入项目工作区
  await expect(page.getByRole("heading", { name: "邀请链路项目" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "任务看板", exact: true })).toBeVisible({ timeout: 20_000 });
});