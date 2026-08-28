"""D6 长期画像：接口鉴权 / 聚合正确性 / 推荐兜底 / 字段契约。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi.testclient import TestClient

import backend.main as api
from backend.core.context import active_db_path


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def _setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "profile.db")
    monkeypatch.setenv("RECOMMEND_SKILL_MODE", "rule")
    monkeypatch.setenv("RECOMMEND_USE_LLM_SKILL", "false")
    monkeypatch.setenv("RECOMMEND_USE_LLM_REASON", "false")
    api.init_db()


def _member(client: TestClient, owner: TestClient, pid: int, email: str, name: str) -> dict:
    user = _account(client, name, email)
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert client.post(f"/api/invitations/{code}/accept").status_code == 200
    return user


def test_profile_empty_user_fields_and_self_access(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    alice_dev = _client()
    alice = _account(alice_dev, "爱丽丝", "alice-profile@example.com")
    resp = alice_dev.get(f"/api/users/{alice['id']}/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == alice["id"]
    assert body["name"] == "爱丽丝"
    assert body["skill_families"] == []
    assert body["skill_strength"] == {}
    assert body["average_quality"] is None
    assert body["quality_samples"] == 0
    assert body["average_efficiency"] is None
    assert body["efficiency_samples"] == 0
    assert body["contributions_total"] == 0
    assert body["projects_count"] == 0
    assert body["active_months"] == 0
    assert body["updated_at"].endswith("Z")


def test_profile_aggregation_quality_efficiency_and_skill(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); backend = _client(); docs = _client()
    _account(owner, "组长", "own-agg@example.com")
    backend_user = _account(backend, "后端同学", "be-agg@example.com")
    _account(docs, "文档同学", "docs-agg@example.com")

    pid = owner.post("/api/projects", json={"name": "聚合项目"}).json()["id"]
    backend_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend.post(f"/api/invitations/{backend_code}/accept").status_code == 200
    backend.patch("/api/users/me", json={"skills": ["后端", "Python"]}).status_code == 200

    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "实现推荐接口REST端点", "assignee_id": backend_user["id"], "task_type": "后端", "estimated_hours": 8}).json()
    backend.post(f"/api/tasks/{task['id']}/start", json={})
    backend.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 6})
    owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 4, "comment": "接口清晰"})

    body = owner.get(f"/api/users/{backend_user['id']}/profile").json()
    assert body["quality_samples"] == 1
    assert abs(body["average_quality"] - 4.0) < 1e-6
    assert body["efficiency_samples"] == 1
    assert abs(body["average_efficiency"] - (6 / 8)) < 1e-6
    assert body["contributions_total"] == 0
    assert body["active_months"] >= 1
    fams = {f["id"]: f for f in body["skill_families"]}
    assert "backend" in fams
    assert body["skill_strength"]["backend"] > 0


def test_profile_access_control_same_project_and_403(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); member = _client(); outsider = _client()
    _account(owner, "组长", "own-acl@example.com")
    member_user = _account(member, "组员甲", "mem-acl@example.com")
    _account(outsider, "无关用户", "out-acl@example.com")

    pid = owner.post("/api/projects", json={"name": "ACL项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200

    # 组员只能看本人；同项目成员可互看
    assert owner.get(f"/api/users/{member_user['id']}/profile").status_code == 200
    assert member.get(f"/api/users/{member_user['id']}/profile").status_code == 200  # 本人可看
    # 无关用户 -> 403
    resp = outsider.get(f"/api/users/{member_user['id']}/profile")
    assert resp.status_code == 403


def _old_ts(days: int) -> str:
    d = date.today() - timedelta(days=days)
    return f"{d.isoformat()}T08:00:00Z"


def test_profile_old_data_decay_weight(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); backend = _client()
    _account(owner, "组长", "own-decay@example.com")
    backend_user = _account(backend, "老后端", "be-decay@example.com")

    pid = owner.post("/api/projects", json={"name": "衰减项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend.post(f"/api/invitations/{code}/accept").status_code == 200

    # 造一条老任务（150 天前）与一条新任务（10 天前），直接 SQL 写入
    conn = sqlite3.connect(active_db_path())
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO tasks (project_id,title,assignee_id,status,estimated_hours,actual_hours,quality,task_type,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, "老后端任务", backend_user["id"], "completed", 10, 5, 2, "后端", _old_ts(150), _old_ts(150)),
    )
    conn.execute(
        "INSERT INTO tasks (project_id,title,assignee_id,status,estimated_hours,actual_hours,quality,task_type,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, "新后端任务", backend_user["id"], "completed", 10, 5, 2, "后端", _old_ts(10), _old_ts(10)),
    )
    conn.commit(); conn.close()

    # 老任务砍半（0.5）+ 新任务全额（1.0）：quality 加权均值 = (2*0.5 + 2*1.0)/(0.5+1.0) = 2.0
    body = owner.get(f"/api/users/{backend_user['id']}/profile").json()
    assert body["quality_samples"] == 2
    assert abs(body["average_quality"] - 2.0) < 1e-6


def test_recommendation_fallback_historical_profile(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); backend = _client()
    owner_user = _account(owner, "组长", "own-recfb@example.com")
    backend_user = _account(backend, "老后端", "be-recfb@example.com")

    # 老项目：有历史质量/效率样本
    old_pid = owner.post("/api/projects", json={"name": "历史项目"}).json()["id"]
    old_code = owner.post(f"/api/projects/{old_pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend.post(f"/api/invitations/{old_code}/accept").status_code == 200
    old_task = owner.post(f"/api/projects/{old_pid}/tasks", json={"title": "历史后端模块A", "assignee_id": backend_user["id"], "task_type": "后端", "estimated_hours": 8}).json()
    backend.post(f"/api/tasks/{old_task['id']}/start", json={})
    backend.post(f"/api/tasks/{old_task['id']}/complete", json={"actual_hours": 6})
    owner.post(f"/api/tasks/{old_task['id']}/review", json={"quality": 5, "comment": "很稳"})

    # 新项目：只加入、无任务样本
    new_pid = owner.post("/api/projects", json={"name": "当前项目"}).json()["id"]
    new_code = owner.post(f"/api/projects/{new_pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend.post(f"/api/invitations/{new_code}/accept").status_code == 200

    rec = owner.get(f"/api/projects/{new_pid}/recommendations", params={"task_name": "新增后端接口B", "task_type": "后端"}).json()
    items = {item["user_id"]: item for item in rec["recommendations"]}
    node = items.get(backend_user["id"])
    assert node is not None, f"后端同学应出现在推荐中, 实际: {list(items)}"
    assert node.get("profile_source") == "historical"
    assert node["dimensions"]["quality"]["samples"] == 0       # 当前项目仍无样本
    assert node["dimensions"]["quality"]["missing"] is False   # 兜底已清除缺失标记
    assert node["reasons"]["average_quality"] == 5.0           # reasons 取历史质量
    assert "参考历史画像" in node["dimensions"]["quality"]["note"]
    assert any("跨项目历史画像" in line for line in node["reasons"]["evidence"])


def test_recommendation_current_no_fallback(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); backend = _client()
    _account(owner, "组长", "own-reccur@example.com")
    backend_user = _account(backend, "新后端", "be-reccur@example.com")

    pid = owner.post("/api/projects", json={"name": "当前项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend.post(f"/api/invitations/{code}/accept").status_code == 200
    backend.patch("/api/users/me", json={"skills": ["后端"]}).status_code == 200

    for i in range(2):
        task = owner.post(f"/api/projects/{pid}/tasks", json={"title": f"当前后端接口X-{i}", "assignee_id": backend_user["id"], "task_type": "后端", "estimated_hours": 4}).json()
        backend.post(f"/api/tasks/{task['id']}/start", json={})
        backend.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 3})
        owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 4, "comment": "尚可"})

    rec = owner.get(f"/api/projects/{pid}/recommendations", params={"task_name": "补齐新后端接口Y", "task_type": "后端"}).json()
    node = next(item for item in rec["recommendations"] if item["user_id"] == backend_user["id"])
    # 当前项目已有 >=2 条样本，不进入历史兜底
    assert node.get("profile_source") != "historical"
    assert node["dimensions"]["quality"]["samples"] == 2
    assert node["dimensions"]["efficiency"]["samples"] == 2

def test_profile_404_and_401(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    anon = _client()
    assert anon.get("/api/users/1/profile").status_code == 401
    owner = _client(); _account(owner, "组长", "own-404@example.com")
    assert owner.get("/api/users/999999/profile").status_code == 404


def test_profile_efficiency_none_without_completed_tasks(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); backend = _client()
    _account(owner, "组长", "own-eff@example.com")
    backend_user = _account(backend, "新后端", "be-eff@example.com")
    pid = owner.post("/api/projects", json={"name": "效率项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend.post(f"/api/invitations/{code}/accept").status_code == 200
    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "只开始不完成", "assignee_id": backend_user["id"], "estimated_hours": 4}).json()
    backend.post(f"/api/tasks/{task['id']}/start", json={})

    body = owner.get(f"/api/users/{backend_user['id']}/profile").json()
    assert body["efficiency_samples"] == 0
    assert body["average_efficiency"] is None
    assert body["quality_samples"] == 0
    assert body["average_quality"] is None
    assert body["projects_count"] == 1
    assert body["active_months"] >= 1


def test_recommendation_requires_exactly_one_task_identifier(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); _account(owner, "组长", "own-recid@example.com")
    pid = owner.post("/api/projects", json={"name": "推荐参数项目"}).json()["id"]
    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "参数校验任务"}).json()

    both = owner.get(f"/api/projects/{pid}/recommendations", params={"task_id": task["id"], "task_name": "同时给两个"})
    assert both.status_code == 422
    neither = owner.get(f"/api/projects/{pid}/recommendations")
    assert neither.status_code == 422
    missing = owner.get(f"/api/projects/{pid}/recommendations", params={"task_id": 999999})
    assert missing.status_code == 404
    by_name = owner.get(f"/api/projects/{pid}/recommendations", params={"task_name": "参数校验任务"})
    assert by_name.status_code == 200
