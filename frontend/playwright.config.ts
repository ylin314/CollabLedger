import { defineConfig, devices } from "@playwright/test";

// E2E 通过后端托管的 frontend/dist 访问应用（同时覆盖“后端静态托管”验收项）。
// 运行前必须先 npm run build。
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8310",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "zh-CN",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    // 仅标题带 @mobile 的核心链路补跑 390px 视口
    { name: "mobile", grep: /@mobile/, use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: "node e2e/server.mjs",
    url: "http://127.0.0.1:8310/api/health",
    reuseExistingServer: false,
    timeout: 90_000,
  },
});
