import { Archive, ArrowRight, CheckCircle2, Users } from "lucide-react";
import type { ProjectRole, ProjectSummary } from "../../api/types";
import { PageTitle } from "../../shared/components";

function roleLabel(role: ProjectRole) {
  return { owner: "组长", member: "成员", viewer: "只读" }[role] || role;
}

interface HistoryProjectsViewProps {
  projects: ProjectSummary[];
  onOpen: (projectId: number) => void;
}

function HistoryProjectsView({ projects, onOpen }: HistoryProjectsViewProps) {
  return (
    <>
      <PageTitle title="历史项目" />
      {projects.length ? (
        <div className="history-project-list">
          {projects.map((project) => {
            const total = Number(project.task_count || 0);
            const completed = Number(project.completed_task_count || 0);
            const progress = total ? Math.round((completed / total) * 100) : 0;
            return (
              <article className="history-project-row" key={project.id}>
                <div className="history-project-mark">
                  <Archive aria-hidden="true" />
                </div>
                <div className="history-project-main">
                  <div className="history-project-title">
                    <h2>{project.name}</h2>
                    <span>{roleLabel(project.role)}</span>
                  </div>
                  {project.description && <p>{project.description}</p>}
                  <div className="history-project-facts">
                    <span><Users /> {project.member_count || 0} 位成员</span>
                    <span><CheckCircle2 /> {completed} / {total} 项完成</span>
                  </div>
                </div>
                <div className="history-project-progress">
                  <strong>{progress}%</strong>
                  <div className="history-progress-track">
                    <i style={{ width: `${progress}%` }} />
                  </div>
                </div>
                <button className="ghost-button" onClick={() => onOpen(project.id)}>
                  查看账本 <ArrowRight aria-hidden="true" />
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="panel history-empty">
          <Archive aria-hidden="true" />
          <strong>还没有已归档项目</strong>
        </div>
      )}
    </>
  );
}

export { HistoryProjectsView, roleLabel };
