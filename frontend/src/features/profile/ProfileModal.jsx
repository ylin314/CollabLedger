import { useEffect, useState } from "react";
import { getJson } from "../../api/client";
import { SkillBar } from "../../shared/components";

function ProfileModal({ user, onClose }) {
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getJson(`/api/users/${user.id}/profile`)
      .then((payload) => {
        if (!cancelled) setProfile(payload);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [user.id]);

  const families = profile?.skill_families || [];
  const empty =
    profile &&
    !families.length &&
    !profile.quality_samples &&
    !profile.efficiency_samples &&
    !profile.contributions_total;

  return (
    <div className="modal-backdrop">
      <div className="modal profile-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">MEMBER PROFILE</span>
            <h2>{profile?.name || user.name} 的历史画像</h2>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        {error ? (
          <p className="profile-error">{error}</p>
        ) : !profile ? (
          <div className="loading-state">正在聚合历史画像…</div>
        ) : empty ? (
          <div className="empty-state">暂无历史画像</div>
        ) : (
          <>
            <p className="profile-note">
              基于历史项目聚合，仅对本组成员可见；近 90 天样本权重 1.0，更早历史权重 0.5。
            </p>
            <div className="profile-grid">
              <div className="profile-skills">
                <h4>技能画像</h4>
                {families.length ? (
                  families.map((family) => (
                    <SkillBar
                      key={family.id}
                      label={family.name}
                      value={Math.round((profile.skill_strength?.[family.id] || 0) * 100)}
                      color="purple"
                    />
                  ))
                ) : (
                  <p className="muted-note">暂无技能样本</p>
                )}
              </div>
              <div className="profile-metrics">
                <div className="profile-metric">
                  <strong>
                    {profile.average_quality != null ? profile.average_quality : "—"}
                    {profile.average_quality != null && <small> / 5</small>}
                  </strong>
                  <span>平均质量（{profile.quality_samples} 条评价）</span>
                </div>
                <div className="profile-metric">
                  <strong>{profile.average_efficiency != null ? profile.average_efficiency : "—"}</strong>
                  <span>工时比（{profile.efficiency_samples} 条完成记录，越小越快）</span>
                </div>
                <div className="profile-metric">
                  <strong>{profile.contributions_total ?? 0}</strong>
                  <span>已确认贡献</span>
                </div>
                <div className="profile-metric">
                  <strong>{profile.projects_count ?? 0}</strong>
                  <span>参与项目数</span>
                </div>
                <div className="profile-metric">
                  <strong>{profile.active_months ?? 0}</strong>
                  <span>活跃月份</span>
                </div>
              </div>
            </div>
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