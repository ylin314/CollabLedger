import { useCallback, useEffect, useState } from "react";
import { getJson, sendJson } from "../../api/client";
import { SkillBar } from "../../shared/components";

function ProfileModal({ user, onClose, isSelf = false }) {
  const [profile, setProfile] = useState(null);
  const [authorization, setAuthorization] = useState(null);
  const [collaborations, setCollaborations] = useState([]);
  const [recommendations, setRecommendations] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      if (isSelf) {
        const [profilePayload, authorizationPayload, collaborationPayload, recommendationPayload] =
          await Promise.all([
            getJson("/api/users/me/profile"),
            getJson("/api/users/me/authorizations"),
            getJson("/api/users/me/collaborations"),
            getJson("/api/users/me/recommendations"),
          ]);
        setProfile(profilePayload);
        setAuthorization(authorizationPayload);
        setCollaborations(collaborationPayload.items || []);
        setRecommendations(recommendationPayload.recommendations || []);
      } else {
        setProfile(await getJson(`/api/users/${user.id}/profile`));
      }
    } catch (reason) {
      setError(reason.message);
    }
  }, [isSelf, user.id]);

  useEffect(() => {
    let cancelled = false;
    load().catch((reason) => {
      if (!cancelled) setError(reason.message);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function saveAuthorization(body) {
    setBusy(true);
    setError("");
    try {
      const payload = await sendJson("/api/users/me/authorizations", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setAuthorization(payload);
      const [profilePayload, collaborationPayload, recommendationPayload] = await Promise.all([
        getJson("/api/users/me/profile"),
        getJson("/api/users/me/collaborations"),
        getJson("/api/users/me/recommendations"),
      ]);
      setProfile(profilePayload);
      setCollaborations(collaborationPayload.items || []);
      setRecommendations(recommendationPayload.recommendations || []);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteProfileData() {
    if (!window.confirm("彻底删除画像授权与派生数据？团队任务、贡献和工时原始记录仍会按项目权限保留。")) return;
    setBusy(true);
    setError("");
    try {
      const payload = await sendJson("/api/users/me/profile-data", { method: "DELETE" });
      setAuthorization(payload);
      setCollaborations([]);
      setRecommendations([]);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  const families = profile?.skill_families || [];
  const empty =
    profile &&
    !families.length &&
    !profile.quality_samples &&
    !profile.efficiency_samples &&
    !profile.contributions_total &&
    !profile.completed_task_count;
  const sourceCounts = Object.fromEntries(
    (profile?.data_sources || []).map((item) => [item.source, item.count]),
  );

  return (
    <div className="modal-backdrop">
      <div className="modal profile-modal profile-modal-wide">
        <div className="modal-head">
          <div>
            <span className="eyebrow">{isSelf ? "MY COLLABORATION PROFILE" : "MEMBER PROFILE"}</span>
            <h2>{profile?.name || user.name} 的长期协作画像</h2>
          </div>
          <button aria-label="关闭画像" onClick={onClose}>×</button>
        </div>
        {error && <p className="profile-error">{error}</p>}
        {!profile ? (
          <div className="loading-state">正在聚合真实项目记录…</div>
        ) : (
          <>
            <p className="profile-note">
              画像按读取时实时聚合；只使用未删除项目中的任务、评价、工时和 confirmed 贡献。
              自报技能只用于冷启动，不冒充历史完成证据。
            </p>
            {isSelf && authorization && (
              <section className="profile-section authorization-panel">
                <div className="profile-section-head">
                  <div>
                    <h3>数据授权</h3>
                    <p>
                      当前状态：{authorization.data_status === "retained" ? "正在使用" : authorization.data_status === "frozen" ? "已冻结保留" : "派生数据已删除"}
                    </p>
                  </div>
                  <label className="profile-switch">
                    <input
                      type="checkbox"
                      checked={Boolean(authorization.global_enabled)}
                      disabled={busy}
                      onChange={(event) => saveAuthorization({ global_enabled: event.target.checked })}
                    />
                    <span>同团队跨项目默认可用</span>
                  </label>
                </div>
                <p className="profile-note">关闭后停止跨项目画像、合作分析和长期推荐，但保留团队原始记录；可逐项目覆盖。</p>
                <div className="authorization-projects">
                  {(authorization.projects || []).map((item) => (
                    <label key={item.project_id}>
                      <span>
                        <strong>{item.project_name}</strong>
                        <small>{item.project_status} · {item.membership_status}</small>
                      </span>
                      <select
                        value={item.override == null ? "inherit" : item.override ? "on" : "off"}
                        disabled={busy}
                        onChange={(event) => {
                          const raw = event.target.value;
                          saveAuthorization({
                            project_overrides: {
                              [item.project_id]: raw === "inherit" ? null : raw === "on",
                            },
                          });
                        }}
                      >
                        <option value="inherit">跟随全局</option>
                        <option value="on">此项目允许</option>
                        <option value="off">此项目关闭</option>
                      </select>
                    </label>
                  ))}
                </div>
                <button className="danger-button compact-danger" disabled={busy} onClick={deleteProfileData}>
                  彻底删除画像派生数据
                </button>
              </section>
            )}

            {empty ? (
              <div className="empty-state">暂无可验证的历史画像；不会用假数据填充。</div>
            ) : (
              <section className="profile-section">
                <div className="profile-grid">
                  <div className="profile-skills">
                    <h4>技能画像</h4>
                    {families.length ? (
                      families.map((family) => (
                        <div key={family.id} className="profile-skill-row">
                          <SkillBar
                            label={family.name}
                            value={Math.round((profile.skill_strength?.[family.id] || 0) * 100)}
                            color="purple"
                          />
                          <small>{family.task_count} 个任务证据 · {family.quality_samples} 条质量样本</small>
                        </div>
                      ))
                    ) : (
                      <p className="muted-note">暂无达到阈值的真实任务技能样本</p>
                    )}
                    {profile.declared_skills?.length > 0 && (
                      <p className="profile-note">自报技能：{profile.declared_skills.join("、")}（仅冷启动）</p>
                    )}
                  </div>
                  <div className="profile-metrics">
                    <div className="profile-metric"><strong>{profile.project_count ?? 0}</strong><span>来源项目</span></div>
                    <div className="profile-metric"><strong>{profile.completed_task_count ?? 0}</strong><span>已完成任务</span></div>
                    <div className="profile-metric"><strong>{profile.average_quality ?? "—"}{profile.average_quality != null && <small> / 5</small>}</strong><span>平均质量（{profile.quality_samples} 条）</span></div>
                    <div className="profile-metric"><strong>{profile.efficiency ?? "—"}</strong><span>实际/预估工时比（{profile.efficiency_samples} 条）</span></div>
                    <div className="profile-metric"><strong>{profile.on_time_rate == null ? "—" : `${Math.round(profile.on_time_rate * 100)}%`}</strong><span>准时率（{profile.on_time_samples} 条有截止日期任务）</span></div>
                    <div className="profile-metric"><strong>{profile.contributions_total ?? 0}</strong><span>已确认贡献；待确认/争议不计入</span></div>
                  </div>
                </div>
              </section>
            )}

            <section className="profile-section">
              <h3>数据来源与计算口径</h3>
              <div className="source-chip-list">
                {(profile.data_sources || []).map((item) => (
                  <span key={item.source}>{item.source}: {item.count}</span>
                ))}
              </div>
              <ul className="profile-notes-list">
                {Object.values(profile.calculation_notes || {}).map((note) => <li key={note}>{note}</li>)}
              </ul>
              <p className="profile-note">
                来源项目 {profile.source_projects?.length || 0} 个；任务 {sourceCounts.assigned_tasks || 0} 条；
                confirmed 贡献 {sourceCounts.confirmed_contributions || 0} 条。生成于 {profile.generated_at || profile.updated_at}。
              </p>
            </section>

            {isSelf && (
              <div className="profile-two-columns">
                <section className="profile-section">
                  <h3>跨项目合作关系</h3>
                  {collaborations.length ? collaborations.map((item) => (
                    <article className="profile-list-item" key={item.user_id}>
                      <strong>{item.name}</strong>
                      <span>{item.shared_project_count} 个共同项目 · {item.shared_task_count} 个共同任务</span>
                      <small>事实分 {item.cooperation_score}；不代表人格评价</small>
                    </article>
                  )) : <p className="muted-note">暂无双方均授权的共同项目记录</p>}
                </section>
                <section className="profile-section">
                  <h3>长期任务方向</h3>
                  {recommendations.length ? recommendations.map((item) => (
                    <article className="profile-list-item" key={`${item.skill}-${item.cold_start}`}>
                      <strong>{item.skill} · {item.score}</strong>
                      <span>{item.reason}</span>
                      <small>{item.cold_start ? "冷启动：仅自报技能" : `${item.sample_count} 个真实任务样本`}</small>
                    </article>
                  )) : <p className="muted-note">当前授权范围内没有可生成的长期方向</p>}
                </section>
              </div>
            )}
          </>
        )}
        <div className="modal-actions">
          <button className="ghost-button" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

export { ProfileModal };