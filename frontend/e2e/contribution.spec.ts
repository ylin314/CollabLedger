import { expect, test } from "@playwright/test";
import { setupOwner } from "./helpers";

test("记录贡献并由组长确认", async ({ page }) => {
  await setupOwner(page, "贡献组长", "贡献链路项目");
  await page.getByRole("button", { name: "贡献账本", exact: true }).click();

  await page.getByRole("button", { name: "＋ 记录贡献" }).click();
  await page.getByLabel("贡献标题").fill("完成周报导出模块");
  await page.getByRole("button", { name: "保存记录" }).click();
  await expect(page.getByText("完成周报导出模块").first()).toBeVisible({ timeout: 10_000 });

  // 待确认 → 组长确认 → 状态变为已确认
  await page.getByRole("button", { name: "确认", exact: true }).first().click();
  await expect(page.getByRole("button", { name: "确认这条贡献" })).toBeVisible();
  await page.getByRole("button", { name: "确认这条贡献" }).click();
  await expect(page.locator(".contribution-status").filter({ hasText: "已确认" })).toBeVisible({ timeout: 10_000 });
});
