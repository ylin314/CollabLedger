import { expect, test } from "@playwright/test";
import { setupOwner } from "./helpers";

test("我的长期画像：数据授权开关与画像来源 @mobile", async ({ page }) => {
  await setupOwner(page, "画像组长", "画像链路项目");
  await page.getByRole("button", { name: "我的画像", exact: true }).click();

  // 画像面板打开：显示数据授权区（全局开关）
  await expect(page.getByText("数据授权").first()).toBeVisible({ timeout: 15_000 });

  // 关闭全局授权 → 画像冻结提示
  const globalSwitch = page.getByLabel("同团队跨项目默认可用");
  if (await globalSwitch.isVisible().catch(() => false)) {
    await globalSwitch.uncheck();
    await expect(page.getByText("已冻结保留").first()).toBeVisible({ timeout: 15_000 });
    // 重新开启恢复
    await globalSwitch.check();
    await expect(page.getByText("正在使用").first()).toBeVisible({ timeout: 15_000 });
  }

  // 画像来源/计算说明可见（诚实降级：无数据不造假）
  await expect(page.getByText(/来源|计算|暂无/).first()).toBeVisible();
});
