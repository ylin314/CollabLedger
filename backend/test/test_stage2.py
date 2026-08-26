from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api


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

    weekly = owner.get(f"/api/projects/{pid}/weekly-report").json()
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

    first = owner.get(f"/api/projects/{pid}/weekly-report").json()
    assert first["stored"] is True and first["summary"]["tasks_completed"] == 1
    assert first["source"] == "rule" and first["insight"] and first["members"][0]["summary"]
    second = owner.get(f"/api/projects/{pid}/weekly-report").json()
    assert second["stored"] is True and second["generated_at"] == first["generated_at"]

    history = owner.get(f"/api/projects/{pid}/weekly-report/history").json()
    assert history["count"] == 1 and history["items"][0]["period_start"] == first["period"]["week_start"]
    assert history["items"][0]["tasks_completed"] == 1 and history["items"][0]["source"] == "rule"

    refreshed = owner.get(f"/api/projects/{pid}/weekly-report", params={"refresh": "true"}).json()
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
    assert backend_dev.patch("/api/users/me", json={"max_concurrent_tasks": 2}).status_code == 200

    # 分配给后端同学,再标记为延期(overdue 权重 1.3)
    overdue = owner.post(f"/api/projects/{pid}/tasks", json={"title": "延期后端任务", "assignee_id": backend_user["id"], "task_type": "后端"}).json()
    assert owner.post(f"/api/tasks/{overdue['id']}/overdue", json={}).status_code == 200
    # 高优先级未分配 → critical_unassigned(severity 95)
    owner.post(f"/api/projects/{pid}/tasks", json={"title": "关键未分配任务", "task_type": "后端", "priority": "high"})

    load = owner.get(f"/api/projects/{pid}/members/load").json()
    backend = next(item for item in load["members"] if item["user_id"] == backend_user["id"])
    assert backend["weighted_overdue_tasks"] == 1
    assert backend["weighted_load"] == 0.65  # (1.3) / 2
    assert backend["weighted_level"] == "normal"
    assert backend["weighted_label"] == "正常"
    # 向后兼容字段保留
    assert backend["load_ratio"] is not None and backend["load_level"] in ("low", "normal", "high")

    risks = owner.get(f"/api/projects/{pid}/risks").json()
    severities = [item["severity"] for item in risks["risks"]]
    assert severities == sorted(severities, reverse=True)
    types = {item["type"] for item in risks["risks"]}
    assert "critical_unassigned" in types and "unassigned_task" in types and "overdue_task" in types
    assert risks["summary_source"] == "rule"  # LLM 未配置走规则回退
    assert risks["summary"]
    # summarize=0 跳过 LLM 总结
    no_summary = owner.get(f"/api/projects/{pid}/risks", params={"summarize": 0}).json()
    assert "summary" not in no_summary

