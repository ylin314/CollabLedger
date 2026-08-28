import { useState } from "react";
import { ReceiptText } from "lucide-react";
import { formatApiError, sendJson } from "../../api/client";
import { emailLooksValid } from "../../shared/core";

function AuthView({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function fillDemo() {
    setMode("login");
    setError("");
    setForm({
      name: "",
      email: "owner-demo@example.com",
      password: "password-123",
    });
  }
  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (!emailLooksValid(form.email)) {
        throw new Error(
          "邮箱格式不正确，请使用类似 rxc@example.com 的地址（需要包含域名，不能是 1@1）",
        );
      }
      const credentials = { email: form.email.trim(), password: form.password };
      if (mode === "register")
        await sendJson("/api/auth/register", {
          method: "POST",
          body: JSON.stringify({ name: form.name.trim(), ...credentials }),
        });
      const result = await sendJson("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(credentials),
      });
      if (!result?.user?.id) throw new Error("接口未返回有效用户信息");
      onAuthenticated(result.user);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="brand create-brand">
          <div className="brand-mark"><ReceiptText aria-hidden="true" /></div>
          <div>
            <div className="brand-name">协作账本</div>
            <div className="brand-sub">COLLAB LEDGER</div>
          </div>
        </div>
        <h1>{mode === "login" ? "登录你的协作空间" : "创建一个协作账号"}</h1>
        <form onSubmit={submit}>
          {mode === "register" && (
            <label>
              姓名
              <input
                autoFocus
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
                required
              />
            </label>
          )}
          <label>
            邮箱
            <input
              type="email"
              placeholder="例如 rxc@example.com"
              value={form.email}
              onChange={(e) =>
                setForm((f) => ({ ...f, email: e.target.value }))
              }
              required
            />
          </label>
          <label>
            密码
            <input
              type="password"
              minLength="8"
              value={form.password}
              onChange={(e) =>
                setForm((f) => ({ ...f, password: e.target.value }))
              }
              required
            />
          </label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-button create-submit" disabled={busy}>
            {busy
              ? "请稍候…"
              : mode === "login"
                ? "登录并进入工作台 →"
                : "注册账号 →"}
          </button>
        </form>
        <div className="auth-demo">
          <span>演示项目账号：owner-demo@example.com / password-123</span>
          <button type="button" className="text-button" onClick={fillDemo}>
            一键填入演示账号
          </button>
        </div>
        <button
          className="auth-switch"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError("");
          }}
        >
          {mode === "login" ? "还没有账号？立即注册" : "已有账号？返回登录"}
        </button>
      </div>
    </div>
  );
}

export { AuthView };
