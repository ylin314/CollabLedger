from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api
import backend.services.analytics as analytics


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def test_stage2_recommendation_load_risks_and_weekly(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "stage2.db")
    monkeypatch.setenv("RECOMMEND_SKILL_MODE", "rule")
    monkeypatch.setenv("RECOMMEND_USE_LLM_SKILL", "false")
    monkeypatch.setenv("RECOMMEND_USE_LLM_REASON", "false")
    api.init_db()
    owner, backend_dev, frontend_dev = _client(), _client(), _client()
    _account(owner, "组长", "owner-s2@example.com")
    backend_user = _account(backend_dev, "后端同学", "backend-s2@example.com")
    frontend_user = _account(frontend_dev, "前端同学", "frontend-s2@example.com")
    docs_dev = _client()
    docs_user = _account(docs_dev, "文档同学", "docs-s2@example.com")
    pid = owner.post("/api/projects", json={"name": "阶段二项目"}).json()["id"]
    backend_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    frontend_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    docs_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend_dev.post(f"/api/invitations/{backend_code}/accept").status_code == 200
    assert frontend_dev.post(f"/api/invitations/{frontend_code}/accept").status_code == 200
    assert docs_dev.post(f"/api/invitations/{docs_code}/accept").status_code == 200
    assert backend_dev.patch("/api/users/me", json={"skills": ["后端", "Python"], "max_concurrent_tasks": 3}).status_code == 200
    assert frontend_dev.patch("/api/users/me", json={"skills": ["前端"], "max_concurrent_tasks": 1}).status_code == 200
    assert docs_dev.patch("/api/users/me", json={"skills": ["文档", "答辩"], "max_concurrent_tasks": 3}).status_code == 200

    busy = owner.post(f"/api/projects/{pid}/tasks", json={"title": "占满前端容量", "assignee_id": frontend_user["id"], "task_type": "前端"}).json()
    assert frontend_dev.post(f"/api/tasks/{busy['id']}/start", json={}).status_code == 200
    owner.post(f"/api/projects/{pid}/tasks", json={"title": "未分配后端接口", "task_type": "后端", "due_date": "2020-01-01"})
    done = owner.post(f"/api/projects/{pid}/tasks", json={"title": "已完成后端模块", "assignee_id": backend_user["id"], "task_type": "后端", "estimated_hours": 4}).json()
    backend_dev.post(f"/api/tasks/{done['id']}/start", json={})
    backend_dev.post(f"/api/tasks/{done['id']}/complete", json={"actual_hours": 3})
    owner.post(f"/api/tasks/{done['id']}/review", json={"quality": 5, "comment": "质量高"})

    rec = owner.get(f"/api/projects/{pid}/recommendations", params={"task_name": "补齐任务推荐相关后端接口", "task_type": "后端"}).json()
    names = [item["name"] for item in rec["recommendations"]]
    assert "前端同学" not in names
    assert "组长" not in names
    assert rec["recommendations"][0]["name"] == "后端同学"
    assert rec["weights"] == {"skill": 0.4, "quality": 0.3, "efficiency": 0.2, "load": 0.1}
    assert rec["disclaimer"]
    assert rec["recommendations"][0]["reasons"]["evidence"]
    assert rec["recommendations"][0]["dimensions"]["quality"]["missing"] is False
    assert rec["recommendations"][0]["dimensions"]["skill"]["score"] > 0.5
    assert rec["comparison"]["leader_name"] == "后端同学"
    assert rec["excluded_overloaded"]
    assert any(item["reason_code"] == "overloaded" for item in rec["excluded"])
    assert rec["recommendation_id"]

    load = owner.get(f"/api/projects/{pid}/members/load").json()
    frontend_load = next(item for item in load["members"] if item["user_id"] == frontend_user["id"])
    assert frontend_load["overloaded"] is True
    assert frontend_load["load_level"] == "high"

    risks = owner.get(f"/api/projects/{pid}/risks").json()
    types = {item["type"] for item in risks["risks"]}
    assert {"overdue_task", "unassigned_task", "high_member_load"} <= types
    assert all(item.get("rule") for item in risks["risks"])

    weekly = owner.post(f"/api/projects/{pid}/weekly-report").json()
    assert weekly["summary"]["tasks_total"] >= 3
    assert weekly["disclaimer"]
    assert weekly["source"]

    unassigned = owner.get(f"/api/projects/{pid}/tasks", params={"status": "unassigned"}).json()["items"][0]
    rec_task = owner.get(f"/api/projects/{pid}/recommendations", params={"task_id": unassigned["id"]}).json()
    decided = owner.post(f"/api/projects/{pid}/recommendations/{rec_task['recommendation_id']}/decide", json={"user_id": backend_user["id"], "note": "采纳推荐"})
    assert decided.status_code == 200
    assigned = decided.json()["task"]
    assert assigned["assignee_id"] == backend_user["id"]
    history = owner.get(f"/api/projects/{pid}/recommendations/history").json()
    assert history["items"]
    assert any(item["status"] in {"accept", "manual"} for item in history["items"])

def test_weekly_report_persist_lookup_refresh_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "weekly-d3.db")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("RECOMMEND_SKILL_MODE", "rule")
    api.init_db()
    owner, member = _client(), _client()
    _account(owner, "周报组长", "weekly-owner@example.com")
    member_user = _account(member, "周报成员", "weekly-member@example.com")
    pid = owner.post("/api/projects", json={"name": "周报深化项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200
    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "周报任务", "assignee_id": member_user["id"], "estimated_hours": 2}).json()
    member.post(f"/api/tasks/{task['id']}/start", json={})
    member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 1})

    first = owner.post(f"/api/projects/{pid}/weekly-report").json()
    assert first["stored"] is True and first["summary"]["tasks_completed"] == 1
    assert first["source"] == "rule" and first["insight"] and first["members"][0]["summary"]
    second = owner.get(f"/api/projects/{pid}/weekly-report").json()
    assert second["stored"] is True and second["generated_at"] == first["generated_at"]

    history = owner.get(f"/api/projects/{pid}/weekly-report/history").json()
    assert history["count"] == 1 and history["items"][0]["period_start"] == first["period"]["week_start"]
    assert history["items"][0]["tasks_completed"] == 1 and history["items"][0]["source"] == "rule"

    refreshed = owner.post(f"/api/projects/{pid}/weekly-report").json()
    assert refreshed["stored"] is True and refreshed["generated_at"] >= first["generated_at"]
    history2 = owner.get(f"/api/projects/{pid}/weekly-report/history").json()
    assert history2["count"] == 1
    md = owner.get(f"/api/projects/{pid}/weekly-report", params={"format": "markdown"})
    assert md.status_code == 200 and "## 成员产出" in md.text and "## 整体洞察" in md.text


def test_d2_weighted_load_and_risk_severity(tmp_path, monkeypatch):
    """D2 聚焦：加权负载字段、风险按 severity 降序、critical_unassigned、LLM 总结规则回退。"""
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "d2.db")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("RECOMMEND_USE_LLM_REASON", "false")
    api.init_db()
    owner, backend_dev = _client(), _client()
    _account(owner, "组长", "owner-d2@example.com")
    backend_user = _account(backend_dev, "后端同学", "backend-d2@example.com")
    pid = owner.post("/api/projects", json={"name": "D2 项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend_dev.post(f"/api/invitations/{code}/accept").status_code == 200
    assert backend_dev.patch("/api/users/me", json={"max_concurrent_tasks": 3}).status_code == 200

    # 两个延期任务：原始负载 2/3 为 normal，加权负载 2.6/3 为 high。
    for index in range(2):
        overdue = owner.post(f"/api/projects/{pid}/tasks", json={"title": f"延期后端任务{index}", "assignee_id": backend_user["id"], "task_type": "后端"}).json()
        assert owner.post(f"/api/tasks/{overdue['id']}/overdue", json={}).status_code == 200
    critical = owner.post(f"/api/projects/{pid}/tasks", json={"title": "关键未分配任务", "task_type": "后端", "priority": "high"}).json()

    load = owner.get(f"/api/projects/{pid}/members/load").json()
    assert load["active_statuses"] == ["assigned", "in_progress", "paused", "overdue"]
    backend = next(item for item in load["members"] if item["user_id"] == backend_user["id"])
    assert backend["weighted_overdue_tasks"] == 2
    assert backend["weighted_load"] == 0.87
    assert backend["weighted_level"] == "high"
    assert backend["weighted_label"] == "高负载"
    assert backend["load_level"] == "normal"  # 证明风险采用加权口径，而非旧任务数口径。

    risks = owner.get(f"/api/projects/{pid}/risks").json()
    severities = [item["severity"] for item in risks["risks"]]
    assert severities == sorted(severities, reverse=True)
    critical_rows = [item for item in risks["risks"] if item.get("task_id") == critical["id"]]
    assert len(critical_rows) == 1
    assert critical_rows[0]["type"] == "critical_unassigned"
    assert critical_rows[0]["source_types"] == ["critical_unassigned", "unassigned_task"]
    types = {item["type"] for item in risks["risks"]}
    assert "critical_unassigned" in types and "unassigned_task" not in types and "overdue_task" in types
    high_load = next(item for item in risks["risks"] if item["type"] == "high_member_load")
    assert high_load["weighted_load"] == 0.87 and high_load["load_level"] == "normal"
    assert risks["summary_source"] == "rule"
    assert risks["llm_status"] == "not_configured" and risks["llm_error"] is None
    assert risks["summary"]


def test_d2_risk_llm_failure_is_observable_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "d2-llm.db")
    secret = "sk-d2-secret-must-not-leak"
    monkeypatch.setenv("LLM_API_KEY", secret)
    api.init_db()
    owner = _client()
    _account(owner, "风险组长", "risk-owner@example.com")
    pid = owner.post("/api/projects", json={"name": "D2 风险诊断项目"}).json()["id"]
    owner.post(f"/api/projects/{pid}/tasks", json={"title": "普通未分配任务"})
    calls = []

    def failing_llm(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError(f"Authorization: Bearer {secret}; provider timeout")

    monkeypatch.setattr(analytics, "llm_json", failing_llm)
    no_summary = owner.get(f"/api/projects/{pid}/risks", params={"summarize": 0}).json()
    assert "summary" not in no_summary and calls == []

    payload = owner.get(f"/api/projects/{pid}/risks").json()
    assert payload["summary_source"] == "rule" and payload["llm_status"] == "failed"
    assert calls and payload["llm_error"]
    assert secret not in payload["llm_error"] and "[REDACTED]" in payload["llm_error"]



def test_d3_report_is_read_only_until_explicit_generation_and_separates_contributions_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "d3-meaning.db")
    monkeypatch.setenv("LLM_API_KEY", "")
    api.init_db()
    owner, member = _client(), _client()
    _account(owner, "周报组长", "d3-owner@example.com")
    member_user = _account(member, "周报成员", "d3-member@example.com")
    pid = owner.post("/api/projects", json={"name": "D3 口径项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200
    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "有双来源工时的任务", "assignee_id": member_user["id"], "estimated_hours": 8}).json()
    assert member.post(f"/api/tasks/{task['id']}/start", json={}).status_code == 200
    assert member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 7}).status_code == 200
    assert member.post(f"/api/tasks/{task['id']}/checkins", json={"content": "真实打卡", "hours": 2.5}).status_code == 201

    confirmed = member.post(f"/api/projects/{pid}/contributions", json={"kind": "code", "title": "已确认贡献"}).json()
    pending = member.post(f"/api/projects/{pid}/contributions", json={"kind": "document", "title": "待确认贡献"}).json()
    disputed = member.post(f"/api/projects/{pid}/contributions", json={"kind": "research", "title": "争议贡献"}).json()
    assert owner.post(f"/api/contributions/{confirmed['id']}/confirm", json={"note": "核验通过"}).status_code == 200
    assert owner.post(f"/api/contributions/{disputed['id']}/dispute", json={"note": "证据不足"}).status_code == 200

    # GET 只读：工作区/Agent/查看周报不会偷偷创建 weekly_reports。
    preview = owner.get(f"/api/projects/{pid}/weekly-report").json()
    assert preview["exists"] is False and preview["stored"] is False
    conn = api.db()
    assert conn.execute("SELECT COUNT(*) n FROM weekly_reports WHERE project_id=?", (pid,)).fetchone()["n"] == 0
    conn.close()

    report = owner.post(f"/api/projects/{pid}/weekly-report").json()
    assert report["exists"] is True and report["stored"] is True
    summary = report["summary"]
    assert summary["contribution_count"] == 1
    assert summary["pending_contribution_count"] == 2
    assert summary["pending_count"] == 1 and summary["disputed_count"] == 1
    assert summary["pending_label"] == "待确认 2 项"
    assert summary["checkin_hours"] == 2.5
    assert summary["task_hours"] == 7.0
    assert summary["actual_hours"] == 2.5
    member_row = next(row for row in report["members"] if row["user_id"] == member_user["id"])
    assert member_row["contribution_count"] == 1 and member_row["pending_contribution_count"] == 2
    assert member_row["checkin_hours"] == 2.5 and member_row["task_hours"] == 7.0
    assert member_row["actual_hours"] == 2.5 and member_row["hours_source"] == "checkin"

    markdown = owner.get(f"/api/projects/{pid}/weekly-report", params={"format": "markdown"})
    assert "确认贡献：1 项" in markdown.text
    assert "待确认 2 项" in markdown.text
    assert "打卡工时：2.5" in markdown.text and "任务工时：7.0" in markdown.text


def test_viewer_recommendation_preview_is_read_only_and_archived_weekly_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "permission-boundaries.db")
    monkeypatch.setenv("RECOMMEND_SKILL_MODE", "rule")
    monkeypatch.setenv("RECOMMEND_USE_LLM_SKILL", "false")
    monkeypatch.setenv("RECOMMEND_USE_LLM_REASON", "false")
    api.init_db()
    owner, viewer = _client(), _client()
    _account(owner, "边界组长", "boundary-owner@example.com")
    _account(viewer, "只读成员", "boundary-viewer@example.com")
    pid = owner.post("/api/projects", json={"name": "边界项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "viewer"}).json()["code"]
    assert viewer.post(f"/api/invitations/{code}/accept").status_code == 200

    conn = api.db()
    before = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM recommendations),(SELECT COUNT(*) FROM recommendation_events)").fetchone())
    conn.close()
    preview = viewer.get(f"/api/projects/{pid}/recommendations", params={"task_name": "只读预览任务", "task_type": "后端"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["recommendation_id"] is None
    conn = api.db()
    assert tuple(conn.execute("SELECT (SELECT COUNT(*) FROM recommendations),(SELECT COUNT(*) FROM recommendation_events)").fetchone()) == before
    conn.close()

    assert owner.post(f"/api/projects/{pid}/archive").status_code == 200
    archived_preview = owner.get(f"/api/projects/{pid}/recommendations", params={"task_name": "归档只读预览", "task_type": "后端"})
    assert archived_preview.status_code == 200
    assert archived_preview.json()["recommendation_id"] is None
    conn = api.db()
    assert tuple(conn.execute("SELECT (SELECT COUNT(*) FROM recommendations),(SELECT COUNT(*) FROM recommendation_events)").fetchone()) == before
    conn.close()
    archived = owner.post(f"/api/projects/{pid}/weekly-report")
    assert archived.status_code == 409
    assert archived.json()["error"]["code"] == "CONFLICT"
