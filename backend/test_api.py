import backend.main as api
from fastapi.testclient import TestClient


def test_core_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    api.init_db()
    with TestClient(api.app) as client:
        user = client.post("/api/users", json={"name": "测试成员", "skills": ["Python"]}).json()
        project = client.post("/api/projects", json={"name": "测试项目", "owner_id": user["id"]}).json()
        pid = project["id"]
        task = client.post(f"/api/projects/{pid}/tasks", json={"title": "接口开发", "task_type": "Python", "assignee_id": user["id"], "estimated_hours": 4}).json()
        assert task["status"] == "assigned"
        tid = task["id"]
        assert client.post(f"/api/tasks/{tid}/start", params={"user_id": user["id"]}).status_code == 200
        client.post(f"/api/tasks/{tid}/complete", params={"user_id": user["id"]})
        client.patch(f"/api/tasks/{tid}", json={"actual_hours": 3, "quality": 4.5, "user_id": user["id"]})
        assert client.get(f"/api/projects/{pid}/report").json()["overall"]["completed"] == 1
        assert client.get(f"/api/projects/{pid}/recommendations", params={"task_name": "Python 数据处理"}).json()["recommendations"]

