import { useEffect, useState } from "react";
import { getJson, sendJson } from "../../api/client";

function GitHubIntegration({ project, onToast, onReload }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [repo, setRepo] = useState("");

  useEffect(() => {
    let cancelled = false;
    getJson("/api/integrations/github/status")
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {
        if (!cancelled) setStatus({ configured: false, connected: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status || !status.configured) return null;

  async function connect() {
    try {
      const payload = await getJson("/api/integrations/github/auth-url");
      if (!payload.configured) return onToast(payload.message);
      window.location.href = payload.url;
    } catch (error) {
      onToast(error.message);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await sendJson("/api/integrations/github/disconnect", { method: "POST" });
      onToast("已断开 GitHub 连接");
      const s = await getJson("/api/integrations/github/status");
      setStatus(s);
    } catch (error) {
      onToast(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function sync() {
    if (!repo.trim()) return onToast("请填写仓库（owner/name）");
    setBusy(true);
    try {
      const existing = (status.projects || []).find(
        (item) => item.project_id === project.id,
      );
      const config = existing?.config?.repos?.length
        ? existing.config
        : {
            repos: repo
              .split(",")
              .map((r) => r.trim())
              .filter(Boolean),
            logins: { [status.account]: project.owner_id || 1 },
          };
      const result = await sendJson(
        `/api/projects/${project.id}/integrations/github/sync`,
        { method: "POST", body: JSON.stringify({ config }) },
      );
      onToast(`已导入 ${result.created} 条新提交/PR（重复 ${result.skipped} 条）`);
      await onReload();
    } catch (error) {
      onToast(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-header">
        <div>
          <h2>GitHub 接入</h2>
          <p>把真实提交与 PR 自动导入贡献账本（来源标记 GitHub，需组长确认）。</p>
        </div>
        <span className="eyebrow">
          {status.connected ? `已连接 ${status.account}` : "未连接"}
        </span>
      </div>
      <div className="modal-actions" style={{ justifyContent: "flex-start", gap: 8 }}>
        {!status.connected && (
          <button className="primary-button" disabled={busy} onClick={connect}>
            连接 GitHub
          </button>
        )}
        {status.connected && (
          <>
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="仓库 owner/name，如 ylin314/CollabLedger（多个用英文逗号分隔）"
              style={{ flex: 1, minWidth: 260 }}
            />
            <button className="primary-button" disabled={busy} onClick={sync}>
              同步提交/PR
            </button>
            <button className="ghost-button" disabled={busy} onClick={disconnect}>
              断开
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export { GitHubIntegration };