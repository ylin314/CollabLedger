import { useEffect, useState } from "react";
import { Archive, RotateCcw, Save, Trash2 } from "lucide-react";
import { formatApiError, sendJson } from "../../api/client";
import { PageTitle } from "../../shared/components";

function ProjectSettingsView({ project, onSaved, onReload, onDeleted, onToast }) {
  const [form, setForm] = useState({
    name: project.name || "",
    project_type: project.project_type || "课程项目",
    description: project.description || "",
    start_date: project.start_date || "",
    end_date: project.end_date || "",
  });
  const [deleteText, setDeleteText] = useState("");
  const [busy, setBusy] = useState("");
  const archived = project.status === "archived";

  useEffect(() => {
    setForm({
      name: project.name || "",
      project_type: project.project_type || "课程项目",
      description: project.description || "",
      start_date: project.start_date || "",
      end_date: project.end_date || "",
    });
    setDeleteText("");
  }, [project]);

  const update = (key, value) =>
    setForm((current) => ({ ...current, [key]: value }));

  async function save(event) {
    event.preventDefault();
    setBusy("save");
    try {
      const updated = await sendJson(`/api/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...form,
          description: form.description.trim() || null,
          start_date: form.start_date || null,
          end_date: form.end_date || null,
        }),
      });
      onSaved(updated);
      onToast("项目设置已保存");
    } catch (error) {
      onToast(formatApiError(error));
    } finally {
      setBusy("");
    }
  }

  async function changeArchive(action) {
    setBusy(action);
    try {
      await sendJson(`/api/projects/${project.id}/${action}`, {
        method: "POST",
      });
      await onReload();
      onToast(action === "archive" ? "项目已归档" : "项目已恢复");
    } catch (error) {
      onToast(formatApiError(error));
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    setBusy("delete");
    try {
      await sendJson(`/api/projects/${project.id}`, { method: "DELETE" });
      await onDeleted(project.id);
    } catch (error) {
      onToast(formatApiError(error));
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <PageTitle
        title="项目设置"
        action={
          <span className={`project-status-stamp ${archived ? "archived" : ""}`}>
            {archived ? "已归档" : "进行中"}
          </span>
        }
      />
      <div className="settings-layout">
        <form className="panel settings-form" onSubmit={save}>
          <div className="panel-header">
            <h2>基本信息</h2>
          </div>
          <div className="form-row">
            <label>
              项目名称
              <input
                value={form.name}
                disabled={archived}
                onChange={(event) => update("name", event.target.value)}
                required
              />
            </label>
            <label>
              项目类型
              <select
                value={form.project_type}
                disabled={archived}
                onChange={(event) => update("project_type", event.target.value)}
              >
                <option>课程项目</option>
                <option>竞赛项目</option>
                <option>科研项目</option>
                <option>其他</option>
              </select>
            </label>
          </div>
          <label>
            项目简介
            <textarea
              value={form.description}
              disabled={archived}
              onChange={(event) => update("description", event.target.value)}
            />
          </label>
          <div className="form-row">
            <label>
              开始日期
              <input
                type="date"
                value={form.start_date}
                disabled={archived}
                onChange={(event) => update("start_date", event.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                type="date"
                value={form.end_date}
                disabled={archived}
                onChange={(event) => update("end_date", event.target.value)}
              />
            </label>
          </div>
          {!archived && (
            <div className="modal-actions">
              <button className="primary-button" disabled={busy === "save"}>
                <Save aria-hidden="true" />
                {busy === "save" ? "保存中…" : "保存修改"}
              </button>
            </div>
          )}
        </form>

        <aside className="settings-side">
          <section className="panel settings-action-panel">
            <h2>{archived ? "恢复项目" : "归档项目"}</h2>
            <p>
              {archived
                ? "恢复后，成员可以继续创建任务和记录贡献。"
                : "归档后项目保留全部记录，但停止新增和修改。"}
            </p>
            <button
              className="ghost-button"
              disabled={Boolean(busy)}
              onClick={() => changeArchive(archived ? "restore" : "archive")}
            >
              {archived ? <RotateCcw aria-hidden="true" /> : <Archive aria-hidden="true" />}
              {busy === "archive" || busy === "restore"
                ? "处理中…"
                : archived
                  ? "恢复项目"
                  : "归档项目"}
            </button>
          </section>
          <section className="panel danger-zone">
            <h2>删除项目</h2>
            <p>删除后项目不会再出现在成员列表中。请输入项目名称确认。</p>
            <input
              value={deleteText}
              onChange={(event) => setDeleteText(event.target.value)}
              placeholder={project.name}
            />
            <button
              className="danger-button"
              disabled={deleteText !== project.name || Boolean(busy)}
              onClick={remove}
            >
              <Trash2 aria-hidden="true" />
              {busy === "delete" ? "正在删除…" : "删除项目"}
            </button>
          </section>
        </aside>
      </div>
    </>
  );
}

export { ProjectSettingsView };
