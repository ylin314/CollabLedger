import httpx

BASE = "http://127.0.0.1:8000"
owner = httpx.Client(base_url=BASE, timeout=20)
member = httpx.Client(base_url=BASE, timeout=20)

r = owner.post("/api/auth/register", json={"name": "组长", "email": "owner-demo@example.com", "password": "password-123"})
print("register owner", r.status_code)
r = owner.post("/api/auth/register", json={"name": "林晓", "email": "member-demo@example.com", "password": "password-123"})
print("register member", r.status_code)
r = owner.post("/api/auth/register", json={"name": "陈阳", "email": "member2-demo@example.com", "password": "password-123"})
print("register member2", r.status_code)

r = owner.post("/api/auth/login", json={"email": "owner-demo@example.com", "password": "password-123"})
assert r.status_code == 200, r.text
r = member.post("/api/auth/login", json={"email": "member-demo@example.com", "password": "password-123"})
assert r.status_code == 200, r.text

projects = owner.get("/api/projects").json()["items"]
for p in projects:
    if p["name"] in ("校园活动互助平台", "实验室考勤小程序"):
        owner.delete("/api/projects/" + str(p["id"]))
        print("deleted", p["name"])

r = owner.post("/api/projects", json={"name": "校园活动互助平台", "description": "课程小组期末项目：面向校园活动的互助发布与认领平台"})
assert r.status_code == 201, r.text
proj = r.json()
pid = proj["id"]
oid = proj["owner_id"]
print("project", pid, "owner", oid)

for m in ({"name": "林晓", "email": "member-demo@example.com", "skills": ["前端开发", "UI 设计"], "role": "member"},
          {"name": "陈阳", "email": "member2-demo@example.com", "skills": ["后端开发", "数据库"], "role": "member"}):
    r = owner.post(f"/api/projects/{pid}/members", json=m)
    assert r.status_code in (200, 201), r.text

members = owner.get(f"/api/projects/{pid}/members").json()["items"]
mid = next(m["user_id"] for m in members if m["name"] == "林晓")
mid2 = next(m["user_id"] for m in members if m["name"] == "陈阳")
print("members", mid, mid2)

def task(title, assignee, ttype, hours, due, prio):
    body = {"title": title, "task_type": ttype, "estimated_hours": hours, "priority": prio, "assignee_id": assignee or None}
    if due:
        body["due_date"] = due
    r = owner.post(f"/api/projects/{pid}/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]

ta = task("完成需求调研报告", mid, "调研", 6, None, "high")
tb = task("设计数据库 ER 图", mid2, "设计", 4, None, "medium")
tc = task("开发前端任务看板组件", mid, "开发", 10, "2026-09-04", "high")
td = task("编写接口对接文档", mid2, "文档", 3, "2026-09-05", "medium")
te = task("准备中期汇报 PPT", oid, "汇报", 3, "2026-09-03", "medium")
tg = task("修复登录态丢失问题", mid, "开发", 4, "2026-08-30", "high")
tf = task("测试用例设计与执行", None, "测试", 5, None, "medium")
th = task("部署脚本优化", None, "运维", 2, None, "low")
print("tasks", ta, tb, tc, td, te, tf, tg, th)

for tid in (ta, tb):
    rs = owner.post(f"/api/tasks/{tid}/start")
    print("start", tid, rs.status_code, rs.text[:150], "cookies=", dict(owner.cookies))
    rs.raise_for_status()
    rs = owner.post(f"/api/tasks/{tid}/complete")
    print("complete", tid, rs.status_code)
    rs.raise_for_status()
r = owner.post(f"/api/tasks/{ta}/review", json={"quality": 4.5, "comment": "调研覆盖 3 个竞品与 20 份问卷，结构完整"})
assert r.status_code == 201, r.text

r = member.post(f"/api/tasks/{tc}/checkins", json={"content": "完成看板卡片组件与拖拽交互", "hours": 3})
assert r.status_code == 201, r.text
r = member.post(f"/api/tasks/{tc}/checkins", json={"content": "联调任务状态接口，修复排序问题", "hours": 2, "blockers": "等待后端筛选接口"})
assert r.status_code == 201, r.text

def contrib(title, desc, q, sess=member, project=None):
    r = sess.post(f"/api/projects/{project or pid}/contributions", json={"title": title, "description": desc, "quantity": q})
    assert r.status_code == 201, r.text
    return r.json()["id"]

c1 = contrib("需求调研报告 v1.0", "完成 20 份问卷与 3 个竞品分析", 1)
c2 = contrib("前端看板组件联调", "任务卡片与状态接口联调通过", 1)
c3 = contrib("中期 PPT 数据图表", "整理贡献与工时图表素材", 1)
c4 = contrib("修复登录态丢失问题", "定位到 cookie 过期策略并修复", 1)
for cid in (c1, c2, c3):
    assert owner.post(f"/api/contributions/{cid}/confirm").status_code == 200
print("contributions", c1, c2, c3, "pending:", c4)

r = owner.post(f"/api/projects/{pid}/weekly-report", params={"week_start": "2026-08-24"})
assert r.status_code == 200, r.text

r = owner.post("/api/projects", json={"name": "实验室考勤小程序", "description": "上学期已结题项目"})
assert r.status_code == 201, r.text
p2 = r.json()["id"]
r = owner.post(f"/api/projects/{p2}/members", json={"name": "林晓", "email": "member-demo@example.com", "skills": ["前端开发"], "role": "member"})
assert r.status_code in (200, 201), r.text
c5 = contrib("考勤打卡页面开发", "完成扫码打卡全流程", 1, project=p2)
assert owner.post(f"/api/contributions/{c5}/confirm").status_code == 200
r = owner.patch(f"/api/projects/{p2}", json={"status": "archived"})
assert r.status_code == 200, r.text

print("SEED DONE pid=", pid)