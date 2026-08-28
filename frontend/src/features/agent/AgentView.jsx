import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { getJson, sendJson } from "../../api/client";
import { PageTitle } from "../../shared/components";

function AgentView({ project, tasks = [], online, onRecommend, role }) {
  const unassigned = tasks.find((task) => !task.assignee_id) || tasks[0];
  const [messages, setMessages] = useState([
    {
      role: "agent",
      text: "你好，我是协作 Agent。可以帮你分析项目风险、生成周报，或推荐任务负责人。",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState("default");
  const [sessions, setSessions] = useState([]);
  async function loadSessions() {
    try {
      const payload = await getJson(
        `/api/projects/${project.id}/agent/sessions`,
      );
      setSessions(payload.items || []);
    } catch {
      setSessions([]);
    }
  }
  useEffect(() => {
    loadSessions();
  }, [project.id]);
  function newSession() {
    const next = `session-${Date.now()}`;
    setSessionId(next);
    setMessages([{ role: "agent", text: "已开始一个新的项目分析会话。" }]);
  }
  async function clearSession() {
    await sendJson(
      `/api/projects/${project.id}/agent/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
    setMessages([{ role: "agent", text: "当前会话已清空。" }]);
    await loadSessions();
  }
  async function ask(text = input) {
    if (!text.trim() || busy) return;
    setMessages((items) => [...items, { role: "user", text }]);
    setInput("");
    setBusy(true);
    try {
      const result = await sendJson(`/api/projects/${project.id}/agent/chat`, {
        method: "POST",
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      const generated = result.generated_at
        ? new Date(result.generated_at).toLocaleString()
        : "";
      const meta = [
        result.source === "fallback" ? "规则兜底" : "AI 生成",
        generated,
      ]
        .filter(Boolean)
        .join(" · ");
      setMessages((items) => [
        ...items,
        {
          role: "agent",
          text: result.answer,
          meta,
          warning: result.llm_error
            ? "LLM 服务暂不可用，以上为规则兜底结果。"
            : "",
          toolTrace: result.tool_trace || [],
          citations: result.citations || [],
        },
      ]);
      await loadSessions();
    } catch (error) {
      setMessages((items) => [
        ...items,
        { role: "agent", text: `请求失败：${error.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="COLLAB AGENT"
        title="协作 Agent"
      />
      <div className="agent-layout">
        <section className="agent-chat panel">
          <div className="agent-chat-head">
            <div className="agent-face">✦</div>
            <div>
              <strong>协作 Agent</strong>
            </div>
            <span className="agent-online">
              <i /> 在线
            </span>
          </div>
          <div className="messages">
            {messages.map((message, index) => (
              <div key={index} className={`message ${message.role}`}>
                <div className="message-avatar">
                  {message.role === "agent" ? "✦" : "我"}
                </div>
                <div className="message-bubble">
                  {message.text}
                  {message.meta && <small>{message.meta}</small>}
                  {message.warning && <small>{message.warning}</small>}
                  {message.citations?.length ? (
                    <div className="agent-citations">
                      {message.citations.map((citation, citationIndex) => (
                        <span
                          key={`${citation.type}-${citation.task_id || citation.user_id || citationIndex}`}
                        >
                          {citation.title || citation.name || citation.type}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {message.toolTrace?.length ? (
                    <details className="agent-trace">
                      <summary>
                        查看事实读取过程（{message.toolTrace.length} 步）
                      </summary>
                      {message.toolTrace.map((step, stepIndex) => (
                        <div key={`${step.tool}-${stepIndex}`}>
                          <strong>{step.tool}</strong>
                          <span>
                            {step.ok ? "读取成功" : step.error || "读取失败"}
                          </span>
                        </div>
                      ))}
                    </details>
                  ) : null}
                </div>
              </div>
            ))}
            {busy && (
              <div className="message agent">
                <div className="message-avatar">✦</div>
                <div className="message-bubble typing">
                  <i />
                  <i />
                  <i />
                </div>
              </div>
            )}
          </div>
          <div className="suggestions">
            <button onClick={() => ask("目前项目最大的风险是什么？")}>
              ⌁ 目前项目最大的风险是什么？
            </button>
            <button onClick={() => ask("帮我总结一下这周我们组的工作")}>
              ◷ 总结本周工作
            </button>
            <button
              onClick={() =>
                unassigned ? onRecommend(unassigned) : ask("这个任务应该给谁？")
              }
            >
              ✦ 谁适合做未分配任务？
            </button>
          </div>
          <div className="agent-input">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && ask()}
              placeholder="输入你想了解的项目问题…"
            />
            <button onClick={() => ask()}>发送 ↑</button>
          </div>
        </section>
        <aside className="agent-side">
          <div className="panel agent-session-panel">
            <div className="panel-header">
              <div>
                <h2>分析会话</h2>
              </div>
            </div>
            <select
              value={sessionId}
              onChange={(event) => {
                setSessionId(event.target.value);
                setMessages([
                  { role: "agent", text: "已切换会话，可以继续询问项目问题。" },
                ]);
              }}
            >
              <option value="default">默认会话</option>
              {sessions
                .filter((item) => item.session_id !== "default")
                .map((item) => (
                  <option key={item.session_id} value={item.session_id}>
                    {item.session_id}（{item.message_count}）
                  </option>
                ))}
            </select>
            <div className="session-actions">
              <button onClick={newSession}>
                <Plus size={14} />
                新会话
              </button>
              {role === "owner" && (
                <button className="danger-text" onClick={clearSession}>
                  <Trash2 size={14} />
                  清空当前
                </button>
              )}
            </div>
          </div>
          <div className="panel">
            <div className="panel-header">
              <div>
                <h2>可询问 Agent</h2>
              </div>
            </div>
            <div className="question-list">
              <button onClick={() => ask("目前项目最大的风险是什么？")}>
                目前项目最大的风险是什么？<span>→</span>
              </button>
              <button onClick={() => ask("帮我总结一下这周我们组的工作")}>
                帮我总结一下这周我们组的工作<span>→</span>
              </button>
              <button
                onClick={() =>
                  unassigned
                    ? onRecommend(unassigned)
                    : ask("这个任务应该给谁？")
                }
              >
                这个任务应该给谁？<span>→</span>
              </button>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}

export { AgentView };
