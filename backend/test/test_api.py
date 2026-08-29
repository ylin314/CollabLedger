from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api

import sqlite3
import time
from datetime import timedelta


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _setup(tmp_path, monkeypatch, filename: str = "api.db"):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / filename)
    api.init_db()


def _register_login(client: TestClient, name: str, email: str) -> dict:
    registered = client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"})
    assert registered.status_code == 201, registered.text
    assert registered.json()["status"] == "offline"
    logged = client.post("/api/auth/login", json={"email": email, "password": "password-123"})
    assert logged.status_code == 200, logged.text
    return logged.json()["user"]


def _project(client: TestClient, name: str = "扩展项目") -> int:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _member(client: TestClient, owner: TestClient, pid: int, name: str, email: str) -> dict:
    user = _register_login(client, name, email)
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert client.post(f"/api/invitations/{code}/accept").status_code == 200
    return user


def test_cookie_auth_errors_profile_and_project_lifecycle(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    anonymous = _client()
    unauthenticated = anonymous.get("/api/projects")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"error": {"code": "UNAUTHORIZED", "message": "请先登录"}}

    owner = _client()
    user = _register_login(owner, "张三", "zhangsan@example.com")
    cookie = owner.cookies.get("collab_session")
    assert cookie
    set_cookie = owner.post("/api/auth/login", json={"email": "zhangsan@example.com", "password": "password-123"}).headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=lax" in set_cookie

    me = owner.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["id"] == user["id"]
    patched = owner.patch("/api/users/me", json={"skills": ["Python", "后端"], "max_concurrent_tasks": 4, "status": "online"})
    assert patched.json()["skills"] == ["Python", "后端"]

    project = owner.post(
        "/api/projects",
        json={"name": "协作账本", "project_type": "课程项目", "start_date": "2026-09-01", "end_date": "2026-12-20"},
    )
    assert project.status_code == 201, project.text
    pid = project.json()["id"]
    assert project.json()["current_user_role"] == "owner"
    assert project.json()["created_at"].endswith("Z")

    listing = owner.get("/api/projects", params={"page": 1, "page_size": 1})
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["role"] == "owner"

    archived = owner.post(f"/api/projects/{pid}/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert owner.post(f"/api/projects/{pid}/tasks", json={"title": "只读项目不能新增"}).status_code == 409
    assert owner.get("/api/projects", params={"archived": True}).json()["total"] == 1
    restored = owner.post(f"/api/projects/{pid}/restore")
    assert restored.status_code == 200 and restored.json()["status"] == "active"

    duplicate = owner.post("/api/auth/register", json={"name": "重复", "email": "zhangsan@example.com", "password": "password-123"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "CONFLICT"
    invalid = owner.post("/api/auth/register", json={"name": "", "email": "bad", "password": "1"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    logged_out = owner.post("/api/auth/logout")
    assert logged_out.status_code == 204
    assert owner.get("/api/auth/me").status_code == 401


def test_register_login_validation_and_me(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client = _client()
    assert client.post("/api/auth/register", json={"name": "   ", "email": "ok@example.com", "password": "password-123"}).status_code == 422
    assert client.post("/api/auth/register", json={"name": "甲", "email": "not-an-email", "password": "password-123"}).status_code == 422
    assert client.post("/api/auth/register", json={"name": "甲", "email": "ok@example.com", "password": "1"}).status_code == 422
    user = _register_login(client, "甲", "ok@example.com")
    assert user["name"] == "甲" and user["email"] == "ok@example.com"
    wrong = client.post("/api/auth/login", json={"email": "ok@example.com", "password": "wrong-password"})
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "UNAUTHORIZED"
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["id"] == user["id"]
    assert client.patch("/api/users/me", json={"name": ""}).status_code == 422


def test_users_endpoints_404_422_and_profiles_access(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client = _client()
    _register_login(client, "乙", "users@example.com")
    assert client.get("/api/users/999999").status_code == 404
    # GET /api/users/me 不存在该 GET 路由，落入 /api/users/{user_id} 整数校验返回 422
    assert client.get("/api/users/me").status_code == 422
    listing = client.get("/api/users")
    assert listing.status_code == 200 and listing.json()["items"]
    # 非本人且非同项目：查看对方 profile 应 403
    other = _client()
    _register_login(other, "丙", "users-other@example.com")
    me = client.get("/api/auth/me").json()
    assert other.get(f"/api/users/{me['id']}/profile").status_code == 403
    # 未登录查看 profile -> 401
    anonymous = _client()
    assert anonymous.get(f"/api/users/{me['id']}/profile").status_code == 401


def test_project_patch_archive_restore_delete_and_acl(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, other = _client(), _client()
    _register_login(owner, "组长", "owner-extra@example.com")
    _register_login(other, "别人", "other-extra@example.com")
    pid = _project(owner, "生命周期项目")

    assert owner.patch(f"/api/projects/{pid}", json={"name": "改名项目"}).status_code == 200
    assert other.patch(f"/api/projects/{pid}", json={"name": "越权改名"}).status_code == 403
    assert other.post(f"/api/projects/{pid}/archive").status_code == 403
    assert owner.post(f"/api/projects/{pid}/archive").status_code == 200
    assert owner.post(f"/api/projects/{pid}/archive").status_code == 409
    assert owner.post(f"/api/projects/{pid}/restore").status_code == 200
    assert owner.post(f"/api/projects/{pid}/restore").status_code == 409
    assert other.delete(f"/api/projects/{pid}").status_code == 403
    assert owner.delete(f"/api/projects/{pid}").status_code == 204
    assert owner.get(f"/api/projects/{pid}").status_code == 404


def test_invitation_flow_revoke_and_accept_conflict(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, invitee, late = _client(), _client(), _client()
    _register_login(owner, "组长", "inv-owner@example.com")
    _register_login(invitee, "受邀人", "inv-invitee@example.com")
    _register_login(late, "迟到者", "inv-late@example.com")
    pid = _project(owner, "邀请项目")

    assert owner.get(f"/api/projects/{pid}/invitations").status_code == 200
    created = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member", "max_uses": 1})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "member" and body["code"]
    assert invitee.get(f"/api/invitations/{body['code']}").status_code == 200
    assert invitee.post(f"/api/invitations/{body['code']}/accept").status_code == 200
    assert invitee.post(f"/api/invitations/{body['code']}/accept").status_code == 409
    assert owner.post(f"/api/invitations/{body['id']}/revoke").status_code == 200

    second = owner.post(f"/api/projects/{pid}/invitations", json={"role": "viewer"}).json()
    assert owner.post(f"/api/invitations/{second['id']}/revoke").status_code == 200
    assert late.post(f"/api/invitations/{second['code']}/accept").status_code == 409


def test_member_management_permissions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, member, outsider = _client(), _client(), _client()
    _register_login(owner, "组长", "mem-owner@example.com")
    member_user = _register_login(member, "组员", "mem-member@example.com")
    _register_login(outsider, "外人", "mem-outsider@example.com")
    pid = _project(owner, "成员项目")

    assert outsider.post(f"/api/projects/{pid}/members", json={"user_id": member_user["id"], "role": "member"}).status_code == 403
    assert owner.post(f"/api/projects/{pid}/members", json={"user_id": 999999, "role": "member"}).status_code == 404
    assert owner.post(f"/api/projects/{pid}/members", json={"user_id": member_user["id"], "role": "member"}).status_code == 201
    assert owner.post(f"/api/projects/{pid}/members", json={"user_id": member_user["id"], "role": "member"}).status_code == 409
    assert outsider.patch(f"/api/projects/{pid}/members/{member_user['id']}", json={"role": "viewer"}).status_code == 403
    assert owner.patch(f"/api/projects/{pid}/members/{member_user['id']}", json={"role": "viewer"}).status_code == 200
    assert owner.delete(f"/api/projects/{pid}/members/{member_user['id']}").status_code == 204


def _create_assigned_task(owner: TestClient, pid: int, assignee_id: int, title: str = "实现登录") -> int:
    resp = owner.post(
        f"/api/projects/{pid}/tasks",
        json={"title": title, "assignee_id": assignee_id, "estimated_hours": 4, "priority": "high", "task_type": "dev"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_task_lifecycle_assign_start_complete_review(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, member = _client(), _client()
    _register_login(owner, "组长", "task-owner@example.com")
    pid = _project(owner, "任务项目")
    member_user = _member(member, owner, pid, "组员", "task-member@example.com")

    # 空标题 -> 422；指派给非成员 -> 400
    assert owner.post(f"/api/projects/{pid}/tasks", json={"title": ""}).status_code == 422
    assert owner.post(f"/api/projects/{pid}/tasks", json={"title": "任务", "assignee_id": 999999}).status_code == 400

    # 指派的生命周期：start -> pause -> resume -> complete -> review
    task = _create_assigned_task(owner, pid, member_user["id"])
    before = owner.get(f"/api/tasks/{task}").json()
    assert before["status"] == "assigned" and before["assignee_id"] == member_user["id"]
    # 非 owner 且非 assignee（第三方可越权前）——先用 reviewer 之外的 outsider 校验 403
    outsider = _client()
    _register_login(outsider, "局外人", "task-outsider@example.com")
    assert outsider.post(f"/api/tasks/{task}/start").status_code == 403

    started = member.post(f"/api/tasks/{task}/start", json={"note": "开始"})
    assert started.status_code == 200 and started.json()["status"] == "in_progress"
    assert member.post(f"/api/tasks/{task}/start").status_code == 409  # 只能从 assigned 开始
    assert member.post(f"/api/tasks/{task}/pause").status_code == 200 and member.post(f"/api/tasks/{task}/pause").status_code == 409
    member.post(f"/api/tasks/{task}/resume")
    completed = member.post(f"/api/tasks/{task}/complete", json={"actual_hours": 3.5, "note": "完成"})
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    assert member.post(f"/api/tasks/{task}/complete").status_code == 409

    # 评审：成员给 owner 作为 reviewer 的场景——先由 owner 自评（非 assignee 可评）
    review = owner.post(f"/api/tasks/{task}/review", json={"quality": 4.5, "comment": "不错"})
    assert review.status_code == 201 and review.json()["quality"] == 4.5
    update = owner.post(f"/api/tasks/{task}/review", json={"quality": 5.0, "comment": "更佳"})
    assert update.status_code == 200
    got = owner.get(f"/api/tasks/{task}/review")
    assert got.status_code == 200 and got.json()["quality"] == 5.0
    assert owner.get(f"/api/tasks/{task}/review/history").json()["items"]
    assert owner.get(f"/api/tasks/{task}/logs").json()["items"]

    # 不能评价非完成/未指派可防错：未完成任务在另一个任务上验证 review 409
    todo = owner.post(f"/api/projects/{pid}/tasks", json={"title": "待办", "assignee_id": member_user["id"]}).json()["id"]
    assert owner.post(f"/api/tasks/{todo}/review", json={"quality": 4.0}).status_code == 409
    # 质量越界 -> 422
    assert owner.post(f"/api/tasks/{task}/review", json={"quality": 4.55}).status_code == 422
    # PATCH 携带 status/quality -> 422 提示专用接口
    assert owner.patch(f"/api/tasks/{task}", json={"status": "unassigned"}).status_code == 422
    assert owner.patch(f"/api/tasks/{task}", json={"quality": 3.0}).status_code == 422
    # 删除：非创建者非 owner -> 403
    assert member.delete(f"/api/tasks/{task}").status_code == 403
    assert owner.delete(f"/api/tasks/{task}").status_code == 204
    assert owner.get(f"/api/tasks/{task}").status_code == 404


def test_task_checkins_and_project_level_filters(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, member = _client(), _client()
    _register_login(owner, "组长", "ck-owner@example.com")
    pid = _project(owner, "打卡项目")
    member_user = _member(member, owner, pid, "组员", "ck-member@example.com")
    task = _create_assigned_task(owner, pid, member_user["id"], "前端页面")

    # 非 owner 且非 assignee 打卡 -> 403
    outsider = _client()
    _register_login(outsider, "路人", "ck-outsider@example.com")
    assert outsider.post(f"/api/tasks/{task}/checkins", json={"content": "越权打卡", "hours": 1}).status_code == 403

    created = member.post(
        f"/api/tasks/{task}/checkins",
        json={"content": "完成登录页", "hours": 2.5, "blockers": "样式冲突"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["user_id"] == member_user["id"]
    assert owner.post(f"/api/tasks/{task}/checkins", json={"content": "owner 补充", "hours": 1}).status_code == 201

    listed = member.get(f"/api/tasks/{task}/checkins")
    assert listed.status_code == 200 and len(listed.json()["items"]) == 2

    project_ck = owner.get(f"/api/projects/{pid}/checkins", params={"user_id": member_user["id"], "page_size": 1})
    assert project_ck.status_code == 200 and project_ck.json()["total"] == 1
    assert owner.get(f"/api/projects/{pid}/checkins", params={"task_id": task}).json()["total"] == 2


def test_task_listing_filters_and_pagination(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, member = _client(), _client()
    _register_login(owner, "组长", "lt-owner@example.com")
    pid = _project(owner, "列表项目")
    member_user = _member(member, owner, pid, "组员", "lt-member@example.com")
    for i in range(3):
        owner.post(f"/api/projects/{pid}/tasks", json={"title": f"任务{i}", "assignee_id": member_user["id"], "task_type": "doc"})
    one = _create_assigned_task(owner, pid, member_user["id"], "高优开发")

    all_tasks = owner.get(f"/api/projects/{pid}/tasks", params={"page": 1, "page_size": 2})
    assert all_tasks.json()["total"] == 4 and len(all_tasks.json()["items"]) == 2
    by_status = owner.get(f"/api/projects/{pid}/tasks", params={"status": "in_progress"})
    assert all(t["status"] == "in_progress" for t in by_status.json()["items"])
    member.post(f"/api/tasks/{one}/start")
    assert owner.get(f"/api/projects/{pid}/tasks", params={"assignee_id": member_user["id"]}).json()["total"] == 4
    assert owner.get(f"/api/projects/{pid}/tasks", params={"task_type": "doc"}).json()["total"] == 3
    assert owner.get(f"/api/projects/{pid}/tasks", params={"keyword": "高优"}).json()["total"] == 1
    # 校验初次 start 之后状态为 in_progress 可被列表分页命中
    assert owner.get(f"/api/projects/{pid}/tasks", params={"status": "in_progress"}).json()["total"] == 1


def test_contribution_lifecycle_and_permissions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, member = _client(), _client()
    _register_login(owner, "组长", "cb-owner@example.com")
    pid = _project(owner, "贡献项目")
    member_user = _member(member, owner, pid, "组员", "cb-member@example.com")

    # 普通成员只能记录自己的贡献
    assert member.post(f"/api/projects/{pid}/contributions", json={"title": "越权", "quantity": 1}).status_code == 201
    assert member.post(f"/api/projects/{pid}/contributions", json={"title": "记录他人", "user_id": owner.get("/api/auth/me").json()["id"]}).status_code == 403
    created = member.post(f"/api/projects/{pid}/contributions", json={"title": "写了后端", "kind": "code", "quantity": 2, "evidence_url": "https://example.com/pr"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["user_id"] == member_user["id"] and body["status"] == "pending" and body["source"] == "manual"

    # 已确认贡献不能修改（先 owner 确认）
    confirmed = owner.post(f"/api/contributions/{body['id']}/confirm", json={"note": "确认"})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert owner.patch(f"/api/contributions/{body['id']}", json={"title": "改"}).status_code == 409

    second = member.post(f"/api/projects/{pid}/contributions", json={"title": "另一个贡献", "quantity": 1}).json()
    disputed = owner.post(f"/api/contributions/{second['id']}/dispute", json={"note": "存疑"})
    assert disputed.status_code == 200 and disputed.json()["status"] == "disputed"
    # 非 owner 不可确认/不可删除他人已确认；成员可删除自己 pending
    third = member.post(f"/api/projects/{pid}/contributions", json={"title": "待删", "quantity": 1}).json()
    assert member.delete(f"/api/contributions/{third['id']}").status_code == 204
    assert owner.get(f"/api/projects/{pid}/contributions", params={"status": "pending"}).json()["total"] == 1


def test_analytics_recommendations_risks_and_reports(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner, member = _client(), _client()
    _register_login(owner, "组长", "an-owner@example.com")
    pid = _project(owner, "分析项目")
    member_user = _member(member, owner, pid, "组员", "an-member@example.com")
    task = _create_assigned_task(owner, pid, member_user["id"], "智能推荐任务")

    # task_id 与 task_name 必须且只能提供一个
    assert owner.get(f"/api/projects/{pid}/recommendations").status_code == 422
    assert owner.get(f"/api/projects/{pid}/recommendations", params={"task_id": 999999}).status_code == 404
    rec = owner.get(f"/api/projects/{pid}/recommendations", params={"task_id": task})
    assert rec.status_code == 200
    # 无配置外部模型 / 样本不足时应有兜底结果而非抛错
    if rec.json().get("items"):
        first = rec.json()["items"][0]
        rec_id = first["id"]
        batch = owner.post(f"/api/projects/{pid}/recommendations/batch", json={})
        assert batch.status_code == 200
        assert owner.post(f"/api/projects/{pid}/recommendations/{rec_id}/decide", json={"user_id": member_user["id"], "note": "采纳"}).status_code == 200
        history = owner.get(f"/api/projects/{pid}/recommendations/history")
        assert history.status_code == 200

    assert owner.get(f"/api/projects/{pid}/risks").status_code == 200
    report = owner.get(f"/api/projects/{pid}/report")
    assert report.status_code == 200
    assert owner.get(f"/api/projects/{pid}/contribution-report").status_code == 200
    weekly = owner.get(f"/api/projects/{pid}/weekly-report")
    assert weekly.status_code == 200
    markdown = owner.get(f"/api/projects/{pid}/weekly-report", params={"format": "markdown"})
    assert markdown.status_code == 200 and markdown.text
    exported = owner.get(f"/api/projects/{pid}/report/export", params={"format": "markdown"})
    assert exported.status_code == 200 and "attachment" in exported.headers.get("content-disposition", "")

# ---------- D7 收尾：接口 / 安全 / 边界 补充测试 ----------
# 依赖上方已有的 _client / _setup / _register_login / _project / _member / _create_assigned_task

def test_health_and_agent_config_auth_and_sessions_empty(tmp_path, monkeypatch):
    """health 精确返回；agent/config 未登录 401；新项目无 agent_memory 表时 sessions 返回空 items。"""
    _setup(tmp_path, monkeypatch)
    health = _client().get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "collab-ledger"}

    anonymous = _client()
    assert anonymous.get("/api/agent/config").status_code == 401

    owner = _client()
    _register_login(owner, "组长", "d7a-owner@example.com")
    pid = _project(owner, "Agent空会话项目")
    assert owner.get("/api/projects/999999/agent/sessions").status_code == 404
    assert owner.get(f"/api/projects/{pid}/agent/sessions").json() == {"items": []}


def test_project_acl_get_and_keyword_filter(tmp_path, monkeypatch):
    """项目单查：member 200、outsider 403；列表 keyword 精确过滤。"""
    _setup(tmp_path, monkeypatch)
    owner, member_c, outsider = _client(), _client(), _client()
    _register_login(owner, "组长", "d7b-owner@example.com")
    pid = _project(owner, "唯一王牌项目")
    _member(member_c, owner, pid, "组员", "d7b-member@example.com")
    _register_login(outsider, "外人", "d7b-outsider@example.com")

    assert owner.get(f"/api/projects/{pid}").status_code == 200
    assert member_c.get(f"/api/projects/{pid}").status_code == 200
    assert outsider.get(f"/api/projects/{pid}").status_code == 403

    assert owner.get("/api/projects", params={"keyword": "王牌"}).json()["total"] == 1
    assert owner.get("/api/projects", params={"keyword": "不存在"}).json()["total"] == 0


def test_agent_config_public_field_set_and_no_secret(tmp_path, monkeypatch):
    """登录后 agent/config 返回脱敏字段且不含 api_key。"""
    _setup(tmp_path, monkeypatch)
    client = _client()
    _register_login(client, "组长", "d7c-owner@example.com")
    body = client.get("/api/agent/config")
    assert body.status_code == 200
    data = body.json()
    assert set(data) == {"base_url", "chat_completions_url", "model", "configured"}
    assert "api_key" not in data


def test_contribution_report_content_fields(tmp_path, monkeypatch):
    """贡献账本报告：overall 与 members 明细字段完整。"""
    _setup(tmp_path, monkeypatch)
    owner, member_c = _client(), _client()
    _register_login(owner, "组长", "d7d-owner@example.com")
    pid = _project(owner, "报告项目")
    member_user = _member(member_c, owner, pid, "组员", "d7d-member@example.com")
    task = _create_assigned_task(owner, pid, member_user["id"], "报告基准任务")
    member_c.post(f"/api/tasks/{task}/start")
    member_c.post(f"/api/tasks/{task}/complete", json={"actual_hours": 3.5})
    owner.post(f"/api/tasks/{task}/review", json={"quality": 4.5, "comment": "合格"})
    crea = member_c.post(f"/api/projects/{pid}/contributions",
        json={"title": "写了后端", "kind": "code", "quantity": 2, "evidence_url": "https://example.com/pr/9"})
    assert crea.status_code == 201
    owner.post(f"/api/contributions/{crea.json()['id']}/confirm", json={})

    report = owner.get(f"/api/projects/{pid}/contribution-report")
    assert report.status_code == 200
    data = report.json()
    assert data["project_id"] == pid and data["project_name"] == "报告项目"
    assert set(data["overall"]) == {"tasks_total", "tasks_completed", "tasks_in_progress", "tasks_overdue", "progress"}
    me = next(m for m in data["members"] if m["user_id"] == member_user["id"])
    assert me["name"] == "组员"
    assert me["tasks_total"] == 1 and me["tasks_completed"] == 1 and me["tasks_overdue"] == 0
    assert me["average_quality"] == 4.5 and me["actual_hours"] == 3.5
    assert me["contributions"] == [{"kind": "code", "quantity": 2.0}]


def test_checkin_hours_boundary_and_content(tmp_path, monkeypatch):
    """checkin hours 越界 422；合法打卡内容回读一致。"""
    _setup(tmp_path, monkeypatch)
    owner, member_c = _client(), _client()
    _register_login(owner, "组长", "d7e-owner@example.com")
    pid = _project(owner, "打卡边界项目")
    member_user = _member(member_c, owner, pid, "组员", "d7e-member@example.com")
    task = _create_assigned_task(owner, pid, member_user["id"], "打卡任务")

    assert member_c.post(f"/api/tasks/{task}/checkins", json={"content": "超时", "hours": 24.5}).status_code == 422
    assert member_c.post(f"/api/tasks/{task}/checkins", json={"content": "负数", "hours": -1}).status_code == 422
    created = member_c.post(f"/api/tasks/{task}/checkins", json={"content": "完成登录页", "blockers": "样式冲突", "hours": 2.5})
    assert created.status_code == 201, created.text
    got = created.json()
    assert got["content"] == "完成登录页" and got["blockers"] == "样式冲突" and got["hours"] == 2.5


def test_contribution_report_outsider_forbidden(tmp_path, monkeypatch):
    """贡献账本报告：非项目成员 403。"""
    _setup(tmp_path, monkeypatch)
    owner, outsider = _client(), _client()
    _register_login(owner, "组长", "d7f-owner@example.com")
    pid = _project(owner, "报告越权项目")
    _register_login(outsider, "外人", "d7f-outsider@example.com")
    assert outsider.get(f"/api/projects/{pid}/contribution-report").status_code == 403


def test_invitation_expired_and_exhausted_valid_false(tmp_path, monkeypatch):
    """直插 DB 构造过期/用尽邀请 → GET /api/invitations/{code} valid:false。"""
    _setup(tmp_path, monkeypatch)
    owner = _client()
    _register_login(owner, "组长", "d7g-owner@example.com")
    pid = _project(owner, "邀请有效性项目")

    expired_row = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member", "expires_in_hours": 1}).json()
    exhausted_row = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member", "max_uses": 1}).json()

    expired_at = (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600)) + "+00:00")
    conn = api.db()
    conn.execute("UPDATE project_invitations SET expires_at=?, revoked=0, used_count=0 WHERE id=?", (expired_at, expired_row["id"]))
    conn.execute("UPDATE project_invitations SET expires_at=?, revoked=0, used_count=1 WHERE id=?",
                 (_iso_in_future(), exhausted_row["id"]))
    conn.commit(); conn.close()

    assert owner.get(f"/api/invitations/{expired_row['code']}").json()["valid"] is False
    assert owner.get(f"/api/invitations/{exhausted_row['code']}").json()["valid"] is False


def _iso_in_future() -> str:
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def test_weekly_report_history_limit_before_and_validation(tmp_path, monkeypatch):
    """周报 history：绑定 actor 后生成两期，before 过滤、limit 越界 422。"""
    _setup(tmp_path, monkeypatch)
    owner, member_c = _client(), _client()
    _register_login(owner, "组长", "d7h-owner@example.com")
    pid = _project(owner, "周报历史项目")
    member_user = _member(member_c, owner, pid, "组员", "d7h-member@example.com")
    task = _create_assigned_task(owner, pid, member_user["id"], "周报任务")
    member_c.post(f"/api/tasks/{task}/start")
    member_c.post(f"/api/tasks/{task}/complete", json={"actual_hours": 2})

    owner.get(f"/api/projects/{pid}/weekly-report", params={"refresh": True, "week_start": "2026-08-17"})
    owner.get(f"/api/projects/{pid}/weekly-report", params={"refresh": True, "week_start": "2026-08-24"})
    history = owner.get(f"/api/projects/{pid}/weekly-report/history")
    assert history.status_code == 200 and history.json()["count"] >= 2

    older = owner.get(f"/api/projects/{pid}/weekly-report/history", params={"before": "2026-08-21"})
    assert older.status_code == 200
    assert all(i["period_start"] < "2026-08-21" for i in older.json()["items"])
    assert older.json()["count"] == 1 and older.json()["items"][0]["period_start"] == "2026-08-17"

    assert owner.get(f"/api/projects/{pid}/weekly-report/history", params={"limit": 0}).status_code == 422
    assert owner.get(f"/api/projects/{pid}/weekly-report/history", params={"limit": 101}).status_code == 422
def test_weekly_report_history_auth_and_outsider_forbidden(tmp_path, monkeypatch):
    """周报历史：匿名 401、非成员 403。"""
    _setup(tmp_path, monkeypatch)
    anonymous = _client()
    owner, outsider = _client(), _client()
    _register_login(owner, "组长", "d7i-owner@example.com")
    pid = _project(owner, "周报权限项目")
    _register_login(outsider, "外人", "d7i-outsider@example.com")
    assert anonymous.get(f"/api/projects/{pid}/weekly-report/history").status_code == 401
    assert outsider.get(f"/api/projects/{pid}/weekly-report/history").status_code == 403
