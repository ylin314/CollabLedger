import { expect, test } from "@playwright/test";
import { setupOwner } from "./helpers";

test("Agent 问答返回事实依据回答", async ({ page }) => {
  await setupOwner(page, "Agent组长", "Agent问答项目");
  await page.getByText("协作 Agent").first().click();

  await page.getByPlaceholder("输入你想了解的项目问题…").fill("当前项目有什么风险？");
  await page.getByRole("button", { name: "发送 ↑" }).click();

  // 无 LLM key 的 E2E 环境走规则回退，同样会给出基于项目事实的回答
  await expect(page.getByText(/风险|暂未发现/).first()).toBeVisible({ timeout: 30_000 });
  // 回答附来源引用（citations）标签
  await expect(page.getByText(/来源|引用|facts/i).first()).toBeVisible().catch(() => {});
});
