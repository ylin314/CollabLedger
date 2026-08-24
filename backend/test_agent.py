from backend import main as api
from backend.agent import AgentConfig, AgentRuntime
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

    def fake_complete(messages):
        seen["messages"] = messages
        return "模型回答"

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
