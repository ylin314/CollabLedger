import { useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, Link2, XCircle } from "lucide-react";
import { formatApiError, getJson, sendJson } from "../../api/client";

function InviteAcceptView({ code, currentUser, onAccepted, onCancel }) {
  const [invite, setInvite] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError("");
    getJson(`/api/invitations/${encodeURIComponent(code)}`)
      .then((payload) => {
        if (!cancelled) setInvite(payload);
      })
      .catch((reason) => {
        if (!cancelled) setError(formatApiError(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  async function accept() {
    setBusy(true);
    setError("");
    try {
      const result = await sendJson(
        `/api/invitations/${encodeURIComponent(code)}/accept`,
        { method: "POST" },
      );
      await onAccepted(result.project_id);
    } catch (reason) {
      setError(formatApiError(reason));
    } finally {
      setBusy(false);
    }
  }

  const roleLabel = invite?.role === "viewer" ? "只读成员" : "项目成员";
  return (
    <div className="invite-screen">
      <section className="invite-card">
        <div className="invite-icon">
          {error || invite?.valid === false ? <XCircle /> : <Link2 />}
        </div>
        <h1>{error ? "邀请暂时无法使用" : "加入项目"}</h1>
        {error ? (
          <div className="form-error">{error}</div>
        ) : !invite ? (
          <div className="loading-state">正在读取邀请…</div>
        ) : (
          <>
            <div className="invite-project-name">{invite.project_name}</div>
            <dl className="invite-facts">
              <div>
                <dt>加入身份</dt>
                <dd>{roleLabel}</dd>
              </div>
              <div>
                <dt>当前账号</dt>
                <dd>{currentUser.name}</dd>
              </div>
              <div>
                <dt>有效期至</dt>
                <dd>{invite.expires_at?.slice(0, 16).replace("T", " ")}</dd>
              </div>
            </dl>
            {!invite.valid && (
              <div className="form-error">邀请已过期、撤销或达到使用上限。</div>
            )}
            {invite.valid && (
              <div className="invite-ready">
                <CheckCircle2 aria-hidden="true" />
                接受后即可进入项目空间
              </div>
            )}
          </>
        )}
        <div className="invite-actions">
          <button className="ghost-button" onClick={onCancel}>
            返回我的项目
          </button>
          <button
            className="primary-button"
            disabled={busy || !invite?.valid}
            onClick={accept}
          >
            {busy ? "正在加入…" : "接受邀请"}
            {!busy && <ArrowRight aria-hidden="true" />}
          </button>
        </div>
      </section>
    </div>
  );
}

export { InviteAcceptView };
