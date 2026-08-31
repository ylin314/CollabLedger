import { expect, type Page } from "@playwright/test";

let counter = 0;

export function uniqueEmail(prefix = "e2e") {
  counter += 1;
  return `${prefix}-${Date.now()}-${counter}@example.com`;
}

/** 注册新账号；新用户无项目时会自动进入新建项目页。 */
export async function register(page: Page, name: string, email: string, password = "password-123") {
  await page.goto("/");
  await page.getByText("还没有账号？立即注册").click();
  await page.getByLabel("姓名").fill(name);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "注册账号 →" }).click();
  await expect(
    page.getByRole("button", { name: "创建项目并进入工作台" }),
  ).toBeVisible({ timeout: 20_000 });
}

/** 在新建项目页创建项目并进入工作台。 */
export async function createProject(page: Page, projectName: string) {
  await page.getByLabel("项目名称", { exact: true }).fill(projectName);
  await page.getByRole("button", { name: "创建项目并进入工作台" }).click();
  await expect(page.getByText("任务看板").first()).toBeVisible({ timeout: 20_000 });
}

/** 注册 + 建项目的完整入口，返回账号邮箱。 */
export async function setupOwner(page: Page, name = "组长", projectName = "E2E 验收项目") {
  const email = uniqueEmail();
  await register(page, name, email);
  await createProject(page, projectName);
  return email;
}

/** 创建一个任务并分配给指定成员（分配后任务状态为待开始）。 */
export async function createTask(page: Page, title: string, assigneeLabel?: string) {
  await page.getByRole("button", { name: "＋ 新建任务" }).first().click();
  await page.getByPlaceholder("例如：完成项目 PPT").fill(title);
  if (assigneeLabel) {
    await page.getByLabel("负责人（可稍后分配）").selectOption(assigneeLabel);
  }
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible({ timeout: 10_000 });
}
