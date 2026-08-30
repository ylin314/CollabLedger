import { useEffect, useMemo, useState } from "react";
import { getJson, sendJson } from "../../api/client";

const platformNames = { github: "GitHub", feishu: "飞书", tencent_doc: "腾讯文档" };

function GitHubIntegration({ project, role, currentUserId, onToast, onReload }) {
  const [status, setStatus] = useState(null);
  const [platforms, setPlatforms] = useState([]);
  const [connections, setConnections] = useState([]);
  const [integrations, setIntegrations] = useState([]);
  const [busy, setBusy] = useState("");
  const [repo, setRepo] = useState("");
  const [resource, setResource] = useState({ platform: "feishu", resource_type: "document", resource_id: "", resource_url: "", api_path: "" });
  const [tencentCredentials, setTencentCredentials] = useState({ access_token: "", external_account_id: "", external_username: "" });
  const [issue, setIssue] = useState({ repository: "", title: "", body: "" });
  const [pull, setPull] = useState({ repository: "", title: "", head: "", base: "main", body: "" });

  async function reloadIntegrations() {
    const [github, available, userConnections, projectIntegrations] = await Promise.all([
      getJson("/api/integrations/github/status"),
      getJson("/api/integrations/platforms"),
      getJson("/api/integrations/connections"),
      getJson(`/api/projects/${project.id}/integrations`),
    ]);
    setStatus(github);
    setPlatforms(available.items || []);
    setConnections(userConnections.items || []);
    setIntegrations(projectIntegrations.items || []);
  }

  useEffect(() => {
    let cancelled = false;
    const exchange = async () => {
      const hash = window.location.hash;
      const hashMatch = hash.match(/^#\/github\?(.*)$/);
      if (hashMatch) {
        const params = new URLSearchParams(hashMatch[1]);
        if (params.get("connected")) onToast(`GitHub 已连接：${params.get("connected")}`);
        if (params.get("error")) onToast(`GitHub 连接未完成：${params.get("error")}`);
        window.history.replaceState(null, "", window.location.pathname);
      }
      const params = new URLSearchParams(window.location.search);
      const callbackPlatform = sessionStorage.getItem("collab_oauth_platform");
      if (callbackPlatform && params.get("code") && params.get("state")) {
        try {
          await sendJson(`/api/integrations/${callbackPlatform}/connections`, {
            method: "POST",
            body: JSON.stringify({ code: params.get("code"), state: params.get("state") }),
          });
          onToast(`${platformNames[callbackPlatform] || callbackPlatform} 已连接`);
        } catch (error) {
          onToast(error.message);
        } finally {
          sessionStorage.removeItem("collab_oauth_platform");
          window.history.replaceState(null, "", window.location.pathname);
        }
      }
      try {
        const data = await Promise.all([
          getJson("/api/integrations/github/status"),
          getJson("/api/integrations/platforms"),
          getJson("/api/integrations/connections"),
          getJson(`/api/projects/${project.id}/integrations`),
        ]);
        if (!cancelled) {
          setStatus(data[0]);
          setPlatforms(data[1].items || []);
          setConnections(data[2].items || []);
          setIntegrations(data[3].items || []);
        }
      } catch (error) {
        if (!cancelled) onToast(error.message);
      }
    };
    exchange();
    return () => { cancelled = true; };
  }, [project.id]);

  const activeConnections = useMemo(
    () => Object.fromEntries(connections.filter((item) => item.status === "active").map((item) => [item.platform, item])),
    [connections],
  );
  const owner = role === "owner";

  async function run(key, action) {
    setBusy(key);
    try { await action(); } catch (error) { onToast(error.message); } finally { setBusy(""); }
  }

  async function connect(platform) {
    if (platform === "github") {
      const payload = await getJson("/api/integrations/github/auth-url");
      if (!payload.configured) return onToast(payload.message);
      window.location.href = payload.url;
      return;
    }
    const redirectUri = `${window.location.origin}${window.location.pathname}`;
    const payload = await sendJson(`/api/integrations/${platform}/oauth/start`, {
      method: "POST", body: JSON.stringify({ redirect_uri: redirectUri }),
    });
    sessionStorage.setItem("collab_oauth_platform", platform);
    window.location.href = payload.authorize_url;
  }

  async function connectTencent() {
    if (!tencentCredentials.access_token.trim() || !tencentCredentials.external_account_id.trim()) {
      return onToast("请填写腾讯文档访问凭据和账号标识");
    }
    await sendJson("/api/integrations/tencent_doc/connections", {
      method: "POST", body: JSON.stringify(tencentCredentials),
    });
    setTencentCredentials({ access_token: "", external_account_id: "", external_username: "" });
    onToast("腾讯文档连接已保存（令牌仅加密存于后端）");
    await reloadIntegrations();
  }

  async function disconnect(connection) {
    await sendJson(`/api/integrations/connections/${connection.id}`, { method: "DELETE" });
    onToast(`${platformNames[connection.platform] || connection.platform} 已停止使用，历史数据保留`);
    await reloadIntegrations();
  }

  async function syncGitHub() {
    if (!owner) return onToast("只有项目 owner 可以配置或同步外部平台");
    if (!repo.trim()) return onToast("请填写仓库（owner/name）");
    const result = await sendJson(`/api/projects/${project.id}/integrations/github/sync`, {
      method: "POST",
      body: JSON.stringify({ config: {
        repos: repo.split(",").map((item) => item.trim()).filter(Boolean),
        logins: currentUserId ? { [status.account]: currentUserId } : {},
      } }),
    });
    onToast(`已导入 ${result.created} 条新记录（重复 ${result.skipped} 条）${result.status === "partial" ? "，部分仓库失败" : ""}`);
    await Promise.all([reloadIntegrations(), onReload()]);
  }

  async function bindDocumentPlatform() {
    if (!owner) return onToast("只有项目 owner 可以绑定外部资源");
    if (!resource.resource_id.trim()) return onToast("请填写资源 ID");
    const integration = await sendJson(`/api/projects/${project.id}/integrations`, {
      method: "POST", body: JSON.stringify({ ...resource, actor_user_id: currentUserId }),
    });
    onToast(`${platformNames[resource.platform]} 资源已绑定`);
    setResource((current) => ({ ...current, resource_id: "", resource_url: "", api_path: "" }));
    await reloadIntegrations();
    return integration;
  }

  async function syncIntegration(integration) {
    const result = await sendJson(`/api/projects/${project.id}/integrations/${integration.id}/sync`, {
      method: "POST", body: JSON.stringify({}),
    });
    onToast(`${platformNames[integration.platform]} 同步完成：新增 ${result.created || 0}，重复 ${result.skipped || 0}`);
    await Promise.all([reloadIntegrations(), onReload()]);
  }

  async function registerWebhook(integration) {
    const result = await sendJson(`/api/projects/${project.id}/integrations/${integration.id}/github/webhook`, {
      method: "POST", body: JSON.stringify({}),
    });
    onToast(`Webhook 已启用：${result.hooks.length} 个仓库`);
  }

  async function createIssue() {
    const result = await sendJson(`/api/projects/${project.id}/github/issues`, {
      method: "POST", body: JSON.stringify(issue),
    });
    onToast(`GitHub Issue #${result.number} 已创建`);
    setIssue((current) => ({ ...current, title: "", body: "" }));
  }

  async function createPull() {
    const result = await sendJson(`/api/projects/${project.id}/github/pulls`, {
      method: "POST", body: JSON.stringify(pull),
    });
    onToast(`GitHub PR #${result.number} 已创建`);
    setPull((current) => ({ ...current, title: "", body: "" }));
  }

  if (!status) return null;
  const githubConnection = activeConnections.github;
  const githubIntegration = integrations.find((item) => item.platform === "github" && item.enabled);

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-header">
        <div>
          <h2>外部平台接入</h2>
          <p>连接、绑定、同步均为显式操作；外部事件先落库，再生成待确认贡献。</p>
        </div>
        <span className="eyebrow">{activeConnections.github ? `GitHub ${activeConnections.github.external_username || "已连接"}` : "按需连接"}</span>
      </div>

      <div className="integration-grid">
        {platforms.map((platform) => {
          const connection = activeConnections[platform.platform];
          return (
            <section className="integration-card" key={platform.platform}>
              <strong>{platform.name}</strong>
              <span className={`contribution-status ${connection ? "confirmed" : "pending"}`}>{connection ? "已连接" : platform.enabled ? "可连接" : "需要外部配置"}</span>
              <p>{platform.category === "code" ? "提交、PR、Issue、Review、Webhook 与反向写入" : "文档版本与更新事件同步"}</p>
              {!connection && platform.platform !== "tencent_doc" && (
                <button className="ghost-button" disabled={!platform.enabled || busy} onClick={() => run(`connect-${platform.platform}`, () => connect(platform.platform))}>连接{platform.name}</button>
              )}
              {connection && (
                <button className="ghost-button" disabled={busy} onClick={() => run(`disconnect-${platform.platform}`, () => disconnect(connection))}>停止使用并保留数据</button>
              )}
            </section>
          );
        })}
      </div>

      {!activeConnections.tencent_doc && platforms.find((item) => item.platform === "tencent_doc")?.enabled && (
        <details className="integration-details">
          <summary>连接腾讯文档访问凭据</summary>
          <div className="integration-form-row">
            <input type="password" autoComplete="off" value={tencentCredentials.access_token} onChange={(event) => setTencentCredentials((current) => ({ ...current, access_token: event.target.value }))} placeholder="访问令牌（不会回显）" />
            <input value={tencentCredentials.external_account_id} onChange={(event) => setTencentCredentials((current) => ({ ...current, external_account_id: event.target.value }))} placeholder="账号标识" />
            <input value={tencentCredentials.external_username} onChange={(event) => setTencentCredentials((current) => ({ ...current, external_username: event.target.value }))} placeholder="显示名称（可选）" />
            <button className="primary-button" disabled={busy} onClick={() => run("connect-tencent", connectTencent)}>保存连接</button>
          </div>
        </details>
      )}

      {githubConnection && (
        <details className="integration-details" open>
          <summary>GitHub 仓库同步</summary>
          <div className="integration-form-row">
            <input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="owner/repo，多个用英文逗号分隔" />
            <button className="primary-button" disabled={busy || !owner} onClick={() => run("sync-github", syncGitHub)}>同步提交 / PR / Issue / Review</button>
            {githubIntegration && <button className="ghost-button" disabled={busy || !owner} onClick={() => run("webhook", () => registerWebhook(githubIntegration))}>启用 Webhook</button>}
          </div>
        </details>
      )}

      {(activeConnections.feishu || activeConnections.tencent_doc) && owner && (
        <details className="integration-details">
          <summary>绑定飞书 / 腾讯文档资源</summary>
          <div className="integration-form-row">
            <select value={resource.platform} onChange={(event) => setResource((current) => ({ ...current, platform: event.target.value }))}>
              {activeConnections.feishu && <option value="feishu">飞书</option>}
              {activeConnections.tencent_doc && <option value="tencent_doc">腾讯文档</option>}
            </select>
            <select value={resource.resource_type} onChange={(event) => setResource((current) => ({ ...current, resource_type: event.target.value }))}>
              <option value="document">单文档</option><option value="wiki_space">知识空间</option>
            </select>
            <input value={resource.resource_id} onChange={(event) => setResource((current) => ({ ...current, resource_id: event.target.value }))} placeholder="资源 ID" />
            <input value={resource.resource_url} onChange={(event) => setResource((current) => ({ ...current, resource_url: event.target.value }))} placeholder="资源链接（可选）" />
            {resource.platform === "tencent_doc" && <input value={resource.api_path} onChange={(event) => setResource((current) => ({ ...current, api_path: event.target.value }))} placeholder="开放 API 路径" />}
            <button className="primary-button" disabled={busy} onClick={() => run("bind-document", bindDocumentPlatform)}>绑定资源</button>
          </div>
        </details>
      )}

      {integrations.filter((item) => item.platform !== "github" && item.enabled).map((integration) => (
        <div className="integration-row" key={integration.id}>
          <span>{platformNames[integration.platform]} · {integration.resource_id}</span>
          <button className="ghost-button" disabled={busy || !owner} onClick={() => run(`sync-${integration.id}`, () => syncIntegration(integration))}>立即同步</button>
        </div>
      ))}

      {githubConnection && owner && (
        <details className="integration-details">
          <summary>可选：从 CollabLedger 显式创建 GitHub Issue / PR</summary>
          <div className="integration-write-grid">
            <div>
              <strong>创建 Issue</strong>
              <input value={issue.repository} onChange={(event) => setIssue((current) => ({ ...current, repository: event.target.value }))} placeholder="owner/repo" />
              <input value={issue.title} onChange={(event) => setIssue((current) => ({ ...current, title: event.target.value }))} placeholder="Issue 标题" />
              <textarea value={issue.body} onChange={(event) => setIssue((current) => ({ ...current, body: event.target.value }))} placeholder="Issue 内容" />
              <button className="primary-button" disabled={busy || !issue.repository.trim() || !issue.title.trim()} onClick={() => run("create-issue", createIssue)}>确认创建 Issue</button>
            </div>
            <div>
              <strong>创建 Pull Request</strong>
              <input value={pull.repository} onChange={(event) => setPull((current) => ({ ...current, repository: event.target.value }))} placeholder="owner/repo" />
              <input value={pull.title} onChange={(event) => setPull((current) => ({ ...current, title: event.target.value }))} placeholder="PR 标题" />
              <div className="integration-form-row"><input value={pull.head} onChange={(event) => setPull((current) => ({ ...current, head: event.target.value }))} placeholder="来源分支" /><input value={pull.base} onChange={(event) => setPull((current) => ({ ...current, base: event.target.value }))} placeholder="目标分支" /></div>
              <textarea value={pull.body} onChange={(event) => setPull((current) => ({ ...current, body: event.target.value }))} placeholder="PR 内容" />
              <button className="primary-button" disabled={busy || !pull.repository.trim() || !pull.title.trim() || !pull.head.trim() || !pull.base.trim()} onClick={() => run("create-pull", createPull)}>确认创建 PR</button>
            </div>
          </div>
        </details>
      )}
    </div>
  );
}

export { GitHubIntegration };