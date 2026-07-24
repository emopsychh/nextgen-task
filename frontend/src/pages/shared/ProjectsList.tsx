import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrapList, type Paginated, type Project } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import {
  getPortalLabel,
  portalDisplayName,
  setPortalLabel,
} from "../../lib/portalLabelCache";
import { projectProgress } from "../../lib/projectProgress";

export function ProjectsList() {
  const { portalId: routePortalId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, isAgency, portal?.id]);

  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [enteringId, setEnteringId] = useState<number | null>(null);
  const [title, setTitle] = useState("Проекты");

  const load = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<Project[] | Paginated<Project>>(
      `/api/projects/?portal=${portalId}`,
      {},
      token
    );
    setProjects(unwrapList(data));
  }, [token, portalId]);

  useEffect(() => {
    if (!portalId) return;
    if (!isAgency && portal) {
      const label = portalDisplayName(portal);
      if (label) {
        setPortalLabel(portalId, label);
        setTitle("Проекты");
        return;
      }
    }
    const cached = getPortalLabel(portalId);
    setTitle(cached ? `Проекты · ${cached}` : "Проекты");
  }, [portalId, isAgency, portal]);

  useEffect(() => {
    if (!token || !portalId) return;
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, portalId, load]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId,
    onEvent: () => {
      void load().catch(() => undefined);
    },
  });

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !portalId || !isAgency) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<Project>(
        "/api/projects/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            name,
            description,
          }),
        },
        token
      );
      setName("");
      setDescription("");
      setShowCreate(false);
      setEnteringId(created.id);
      toast.show("Откройте его, чтобы добавить задачи", "Проект создан");
      await load();
      window.dispatchEvent(new Event("projects-updated"));
      window.setTimeout(() => setEnteringId(null), 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать");
    } finally {
      setBusy(false);
    }
  }

  if (!portalId) {
    return (
      <div className="tasks-page">
        <p className="muted">Выберите клиента, чтобы открыть проекты.</p>
      </div>
    );
  }

  return (
    <div className="tasks-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{title}</h1>
          <p className="page-sub">Все модули клиента — открытые и завершённые</p>
        </div>
        {isAgency ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowCreate((v) => !v)}
            data-tour="tour-new-project"
          >
            {showCreate ? "Закрыть" : "Новый проект"}
          </button>
        ) : null}
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {isAgency && showCreate ? (
        <form className="connect-panel create-project-panel stack" onSubmit={createProject}>
          <div>
            <h2 className="section-title">Новый проект</h2>
            <p className="muted">
              В Bitrix это задача внутри проекта компании; внутри — подзадачи.
            </p>
          </div>
          <div className="field">
            <label>Название</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Например, Интеграция оплаты"
              required
              autoFocus
            />
          </div>
          <div className="field">
            <label>Описание</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Кратко, что входит в модуль"
            />
          </div>
          <button className="btn btn-accent" disabled={busy} style={{ alignSelf: "start" }}>
            {busy ? "Создаём…" : "Создать проект"}
          </button>
        </form>
      ) : null}

      {projects.length === 0 ? (
        <div className="empty-linked workspace-empty">
          <p className="muted">
            {isAgency
              ? "Пока нет проектов. Создайте первый модуль."
              : "Пока нет проектов у этого кабинета."}
          </p>
        </div>
      ) : (
        <ul className="projects-hub-grid">
          {projects.map((p) => {
            const { done, total, pct } = projectProgress(p);
            return (
              <li key={p.id}>
                <Link
                  to={`/projects/${p.id}`}
                  className={`projects-hub-card${enteringId === p.id ? " is-entering" : ""}`}
                >
                  <div className="projects-hub-card-top">
                    <strong className="projects-hub-card-title">{p.name}</strong>
                    <span className="projects-hub-card-pct">{pct}%</span>
                  </div>
                  <span className="muted">
                    {done}/{total} задач
                  </span>
                  <span className="projects-hub-card-bar" aria-hidden>
                    <span style={{ width: `${pct}%` }} />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
