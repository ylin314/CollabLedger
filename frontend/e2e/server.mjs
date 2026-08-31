// E2E 专用后端启动器：每次运行前重置临时 SQLite 库，并禁用真实 LLM（走规则回退，保证用例稳定）。
import { spawn } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const root = resolve(here, "..", "..");
const dataDir = join(root, ".e2e");
mkdirSync(dataDir, { recursive: true });
const dbPath = join(dataDir, "e2e.db");
for (const suffix of ["", "-wal", "-shm"]) {
  try {
    rmSync(dbPath + suffix);
  } catch {
    // 首次运行时文件不存在，忽略
  }
}

const python = process.platform === "win32"
  ? join(root, ".venv", "Scripts", "python.exe")
  : join(root, ".venv", "bin", "python");

const child = spawn(
  python,
  ["-m", "uvicorn", "backend.main:app", "--app-dir", root, "--port", "8310"],
  {
    cwd: root,
    env: {
      ...process.env,
      COLLAB_DB: dbPath,
      COLLAB_RATE_LIMIT_DISABLED: "1",
      LLM_API_KEY: "",
      RECOMMEND_SKILL_MODE: "rule",
      RECOMMEND_USE_LLM_SKILL: "false",
      RECOMMEND_USE_LLM_REASON: "false",
    },
    stdio: "inherit",
  },
);

process.on("SIGTERM", () => child.kill());
process.on("SIGINT", () => child.kill());
child.on("exit", (code) => process.exit(code ?? 0));
