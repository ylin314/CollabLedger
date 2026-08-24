from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api


def test_auth_project_isolation_and_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "stage1.db")
    api.init_db()
    with TestClient(api.app) as client:
        alice = client.post(
            "/api/auth/register",
            json={"name": "Alice", "email": "alice@example.com", "password": "password-alice"},
        )
        assert alice.status_code == 201
        alice_token = alice.json()["access_token"]
        ah = {"Authorization": f"Bearer {alice_token}"}

        bob = client.post(
            "/api/auth/register",
            json={"name": "Bob", "email": "bob@example.com", "password": "password-bob"},
        )
        assert bob.status_code == 201
        bob_token = bob.json()["access_token"]
        bh = {"Authorization": f"Bearer {bob_token}"}

        project = client.post("/api/projects", headers=ah, json={"name": "Alice project"})
        assert project.status_code == 201
        pid = project.json()["id"]
        assert [p["id"] for p in client.get("/api/projects", headers=ah).json()] == [pid]
        assert client.get("/api/projects", headers=bh).json() == []
        assert client.get(f"/api/projects/{pid}", headers=bh).status_code == 403

        invite = client.post(f"/api/projects/{pid}/invitations", headers=ah, json={"email": "bob@example.com", "role": "member"})
        assert invite.status_code == 201
        invite_token = invite.json()["token"]
        accepted = client.post("/api/auth/accept-invitation", headers=bh, json={"token": invite_token})
        assert accepted.status_code == 200
        assert accepted.json()["role"] == "member"
        assert client.get(f"/api/projects/{pid}", headers=bh).status_code == 200

        # viewer 只能读取，不能写入任务。
        bob_id = bob.json()["user"]["id"]
        assert client.patch(f"/api/projects/{pid}/members/{bob_id}", headers=ah, json={"role": "viewer"}).status_code == 200
        assert client.post(f"/api/projects/{pid}/tasks", headers=bh, json={"title": "viewer cannot create"}).status_code == 403

        assert client.post("/api/auth/logout", headers=bh).json()["revoked"] is True
        assert client.get("/api/auth/me", headers=bh).status_code == 401


def test_work_logs_and_quality_reviews(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "stage1_logs.db")
    api.init_db()
    with TestClient(api.app) as client:
        owner = client.post("/api/auth/register", json={"name": "Owner", "email": "owner@example.com", "password": "password-owner"}).json()
        member = client.post("/api/auth/register", json={"name": "Member", "email": "member@example.com", "password": "password-member"}).json()
        oh = {"Authorization": f"Bearer {owner['access_token']}"}
        mh = {"Authorization": f"Bearer {member['access_token']}"}
        pid = client.post("/api/projects", headers=oh, json={"name": "Log project"}).json()["id"]
        invite = client.post(f"/api/projects/{pid}/invitations", headers=oh, json={"email": "member@example.com"}).json()
        assert client.post("/api/auth/accept-invitation", headers=mh, json={"token": invite["token"]}).status_code == 200
        member_id = member["user"]["id"]
        task = client.post(f"/api/projects/{pid}/tasks", headers=oh, json={"title": "Reviewable task", "assignee_id": member_id}).json()

        log = client.post(f"/api/projects/{pid}/work-logs/check-in", headers=mh, params={"work_date": "2026-08-24", "note": "开始工作"})
        assert log.status_code == 201
        logged = client.post(f"/api/projects/{pid}/work-logs/check-out", headers=mh, params={"work_date": "2026-08-24", "hours": 2.5})
        assert logged.status_code == 201
        assert logged.json()["check_in"] and logged.json()["check_out"]
        assert len(client.get(f"/api/projects/{pid}/work-logs", headers=oh).json()) == 1

        review = client.post(
            f"/api/projects/{pid}/quality-reviews",
            headers=oh,
            json={"task_id": task["id"], "reviewee_id": member_id, "score": 4.5, "comment": "交付质量好"},
        )
        assert review.status_code == 201
        summary = client.get(f"/api/projects/{pid}/quality-summary", headers=mh)
        assert summary.status_code == 200
        assert summary.json()["members"][0]["average_score"] == 4.5
