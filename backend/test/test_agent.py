from backend import main as api
from backend.agent import AgentConfig, AgentRuntime
import json
from backend.agent.llm import LLMClient


def test_agent_four_layers_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "agent.db")
    api.init_db()
    user = api.create_user(api.UserIn(name="测试成员", skills=["Python"]))
    project = api.create_project(api.ProjectIn(name="Agent 项目", owner_id=user["id"]))

    runtime = AgentRuntime(tmp_path / "agent.db", AgentConfig(base_url="", api_key="", model="deepseek-v4-flash"))
    result = runtime.run(project["id"], "现在项目最大的风险是什么？")

    assert result["source"] == "fallback"
    assert result["plan"]
    assert result["facts"]["project"]["name"] == "Agent 项目"
    assert result["memory"][-1]["role"] == "assistant"


def test_agent_llm_uses_chat_completions(monkeypatch, tmp_path):
    runtime = AgentRuntime(tmp_path / "agent.db", AgentConfig(base_url="https://example.com", api_key="key", model="deepseek-v4-flash"))
    seen = {}

    def fake_complete(messages, timeout=None, max_tokens=None):
        seen["messages"] = messages
        return json.dumps({"action": "answer", "answer": "模型回答"}, ensure_ascii=False)

    runtime.llm.complete = fake_complete
    # 工具需要项目事实；直接替换工具层，隔离数据库细节。
    runtime.tools.run = lambda project_id, name, arguments=None: {"project": {"name": "测试"}, "tasks": [], "members": [], "report": {"overall": {"tasks": 0, "completed": 0}}, "risks": {"risks": []}}
    result = runtime.run(1, "帮我总结本周工作")

    assert result["source"] == "llm"
    assert result["answer"] == "模型回答"
    assert seen["messages"][0]["role"] == "system"
    assert "ChatGPT" not in seen["messages"][0]["content"]


def test_llm_client_posts_openai_chat_completions(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    client = LLMClient(AgentConfig(base_url="https://aigw.saurlax.com/", api_key="secret", model="deepseek-v4-flash"))
    assert client.complete([{"role": "user", "content": "你好"}]) == "ok"
    assert calls["url"].endswith("/v1/chat/completions")
    assert calls["kwargs"]["headers"]["Authorization"] == "Bearer secret"
    assert calls["kwargs"]["json"]["model"] == "deepseek-v4-flash"


def test_agent_react_loop_tool_trace_and_citations(tmp_path, monkeypatch):
    runtime = AgentRuntime(tmp_path / "agent.db", AgentConfig(base_url="https://example.com", api_key="key", model="deepseek-v4-flash"))
    calls = []

    def fake_complete(messages, timeout=None, max_tokens=None):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action": "tool", "tool": "task_detail", "args": {"task_id": 7}}, ensure_ascii=False)
        return json.dumps({"action": "answer", "answer": "任务 7 状态为进行中。"}, ensure_ascii=False)

    runtime.llm.complete = fake_complete
    runtime.tools.run = lambda project_id, name, arguments=None: (
        {"task_id": 7, "found": True, "task": {"id": 7, "title": "开发登录页", "status": "in_progress"}}
        if name == "task_detail"
        else {"project": {"name": "测试"}, "tasks": [], "members": [], "report": {"overall": {"tasks": 0, "completed": 0}}, "risks": {"risks": []}}
    )
    result = runtime.run(1, "任务 7 现在是什么状态？")

    assert result["source"] == "llm"
    assert result["answer"] == "任务 7 状态为进行中。"
    assert len(calls) == 2
    assert result["tool_trace"][-1]["tool"] == "task_detail"
    assert result["tool_trace"][-1]["ok"] is True
    assert any(item["type"] == "task" and item["task_id"] == 7 for item in result["citations"])


def test_agent_unknown_tool_falls_back(tmp_path, monkeypatch):
    runtime = AgentRuntime(tmp_path / "agent.db", AgentConfig(base_url="https://example.com", api_key="key", model="deepseek-v4-flash"))
    runtime.llm.complete = lambda messages, timeout=None, max_tokens=None: json.dumps({"action": "tool", "tool": "delete_all", "args": {}}, ensure_ascii=False)
    runtime.tools.run = lambda project_id, name, arguments=None: {"project": {"name": "测试"}, "tasks": [], "members": [], "report": {"overall": {"tasks": 0, "completed": 0}}, "risks": {"risks": []}}
    result = runtime.run(1, "帮我看看项目情况")

    assert result["source"] == "fallback"
    assert "白名单外工具" in (result["llm_error"] or "")
    assert result["answer"]


def test_agent_memory_summary_triggers_and_compresses(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_THRESHOLD", "4")
    monkeypatch.setenv("AGENT_SUMMARY_LIMIT", "2")
    from backend.agent.memory import AgentMemory
    mem = AgentMemory(tmp_path / "m.db")
    for i in range(7):
        mem.append(1, "user", f"消息{i}")
        mem.append(1, "assistant", f"回答{i}")
    ok = mem.summarize_old(1, llm_complete=lambda messages, timeout=None: "摘要：早期对话要点")
    assert ok is True
    recent = mem.recent(1, limit=30)
    assert recent[0]["role"] == "summary"
    assert recent[0]["content"] == "摘要：早期对话要点"
    non_summary = [item for item in recent if item["role"] != "summary"]
    assert len(non_summary) == 10  # 14 条中最旧 4 条已压缩，其余保留

    # 再次压缩会继续推进：10 条中最旧 4 条被压缩，剩 6 条
    ok2 = mem.summarize_old(1, llm_complete=lambda messages, timeout=None: "摘要：第二批")
    assert ok2 is True
    recent2 = mem.recent(1, limit=30)
    assert len([item for item in recent2 if item["role"] != "summary"]) == 6
    # 第三次不足阈值，不再压缩
    assert mem.summarize_old(1, llm_complete=lambda messages, timeout=None: "摘要：第三批") is False


def test_agent_memory_summary_failure_keeps_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_THRESHOLD", "4")
    monkeypatch.setenv("AGENT_SUMMARY_LIMIT", "2")
    from backend.agent.memory import AgentMemory
    mem = AgentMemory(tmp_path / "m.db")
    for i in range(7):
        mem.append(1, "user", f"消息{i}")
        mem.append(1, "assistant", f"回答{i}")

    def boom(messages, timeout=None):
        raise RuntimeError("LLM 不可用")

    assert mem.summarize_old(1, llm_complete=boom) is False
    assert mem.summarize_old(1, llm_complete=None) is False
    recent = mem.recent(1, limit=30)
    assert not any(item["role"] == "summary" for item in recent)
    assert len(recent) == 14  # 原消息全部保留，未丢信息

