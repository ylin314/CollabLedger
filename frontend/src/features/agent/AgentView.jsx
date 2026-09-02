import { useEffect, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { getJson, sendJson } from "../../api/client";
import { PageTitle } from "../../shared/components";
import ReactMarkdown from "react-markdown";

function AgentView({ project, tasks = [], online, onRecommend, role }) {
  const unassigned = tasks.find((task) => !task.assignee_id) || tasks[0];
  const sessionStorageKey = `collab_agent_session_${project.id}`;
  const [messages, setMessages] = useState([
    {
      role: "agent",
      text: "你好，我是协作 Agent。可以帮你分析项目风险、生成周报，或推荐任务负责人。",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState(
    () => localStorage.getItem(sessionStorageKey) || "default",
  );
  const [sessions, setSessions] = useState([]);

  function welcome(text = "你好，我是协作 Agent。可以帮你分析项目风险、生成周报，或推荐任务负责人。") {
    return [{ role: "agent", text }];
  }

  function displayMessages(items) {
    return (items || []).map((item) => ({
      role: item.role === "user" ? "user" : "agent",
      text: item.role === "summary" ? `会话摘要：${item.content}` : item.content,
      meta: item.created_at
        ? new Date(item.created_at).toLocaleString()
        : "",
    }));
  }

  async function loadSessionMessages(nextSessionId) {
    try {
      const payload = await getJson(
        `/api/projects/${project.id}/agent/sessions/${encodeURIComponent(nextSessionId)}/messages`,
      );
      const restored = displayMessages(payload.items);
      setMessages(restored.length ? restored : welcome("当前会话还没有消息，可以开始提问。"));
    } catch {
      setMessages(welcome("暂时无法读取该会话历史，请稍后重试。"));
    }
  }

  async function loadSessions() {
    try {
      const payload = await getJson(
        `/api/projects/${project.id}/agent/sessions`,
      );
      setSessions(payload.items || []);
      return payload.items || [];
    } catch {
      setSessions([]);
      return [];
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function initializeSessions() {
      const available = await loadSessions();
      if (cancelled) return;
      const remembered = localStorage.getItem(sessionStorageKey) || "default";
      const next =
        remembered === "default" || available.some((item) => item.session_id === remembered)
          ? remembered
          : "default";
      setSessionId(next);
      localStorage.setItem(sessionStorageKey, next);
      await loadSessionMessages(next);
    }
    initializeSessions();
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  function newSession() {
    const next = `session-${Date.now()}`;
    setSessionId(next);
    localStorage.setItem(sessionStorageKey, next);
    setSessions((items) => [
      { session_id: next, message_count: 0, updated_at: null, last_message: null },
      ...items.filter((item) => item.session_id !== next),
    ]);
    setMessages(welcome("已开始一个新的项目分析会话。发送第一条消息后，该会话会自动保存。"));
  }

  async function selectSession(next) {
    setSessionId(next);
    localStorage.setItem(sessionStorageKey, next);
    await loadSessionMessages(next);
  }

  function sessionTitle(item) {
    if (item?.title) return item.title;
    if (item?.session_id && item.session_id !== "default") return item.session_id;
    return "默认会话";
  }

  async function renameSession() {
    const current = sessions.find((item) => item.session_id === sessionId);
    const currentTitle = sessionTitle(current) || (sessionId === "default" ? "默认会话" : sessionId);
    const title = window.prompt("请输入新的会话名称", currentTitle);
    if (title === null) return;
    const trimmed = title.trim();
    if (!trimmed || trimmed === currentTitle) return;
    try {
      const updated = await sendJson(
        `/api/projects/${project.id}/agent/sessions/${encodeURIComponent(sessionId)}`,
        { method: "PATCH", body: JSON.stringify({ title: trimmed }) },
      );
      setSessions((items) =>
        items.map((item) =>
          item.session_id === sessionId ? { ...item, title: updated.title } : item,
        ),
      );
    } catch (error) {
      setMessages((items) => [
        ...items,
        { role: "agent", text: `会话重命名失败：${error.message}` },
      ]);
    }
  }

  async function clearSession() {
    await sendJson(
      `/api/projects/${project.id}/agent/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
    setMessages(welcome("当前会话已清空。"));
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
      localStorage.setItem(sessionStorageKey, sessionId);
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
                <div className="message-bubble bubble-md">
                  {message.role === "agent" ? (
                    <ReactMarkdown>{message.text}</ReactMarkdown>
                  ) : (
                    message.text
                  )}
                  {message.meta && <small>{message.meta}</small>}
                  {message.warning && <small>{message.warning}</small>}
                  {message.citations?.length ? (
                    <div className="agent-citations">
                      {message.citations.map((citation, citationIndex) => (
                        <span
                          key={`${citation.type}-${citation.task_id || citation.user_id || citationIndex}`}
                        >
                          {citation.message || citation.title || citation.name || citation.type}
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
                selectSession(event.target.value);
              }}
            >
              <option value="default">
                {sessionTitle(sessions.find((item) => item.session_id === "default"))}
              </option>
              {sessions
                .filter((item) => item.session_id !== "default")
                .map((item) => (
                  <option key={item.session_id} value={item.session_id}>
                    {sessionTitle(item)}（{item.message_count}）
                  </option>
                ))}
            </select>
            <div className="session-actions">
              <button onClick={newSession}>
                <Plus size={14} />
                新对话
              </button>
              <button onClick={renameSession} disabled={busy}>
                <Pencil size={14} />
                重命名
              </button>
              {role === "owner" && (
                <button className="danger-text" onClick={clearSession}>
                  <Trash2 size={14} />
                  删除当前对话
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

