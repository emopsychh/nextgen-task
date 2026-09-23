import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
  type BacklogItem,
  type BacklogStatus,
  type Paginated,
  type Project,
  type Task,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime } from "../../lib/format";
import { linkStateFrom } from "../../lib/smartBack";
import { STATUS_LABEL } from "../../lib/status";

type PendingDelete = { id: number; title: string };
type DraftEdit = { id: number; title: string; notes: string };

function isPending(item: BacklogItem): boolean {
  return item.source === "client" && item.status !== "converted" && !item.converted_task;
}

type RequestStage = "new" | "planned" | "deferred" | "handed_over";
type BacklogView = "roadmap" | "backlog";
type RoadmapLane = "now" | "next";

type RoadmapProject = {
  project: Project;
  tasks: Task[];
  lane: RoadmapLane;
};

type RoadmapNodeKind = "project" | "task" | "idea" | "group";
type RoadmapNode = {
  id: string;
  kind: RoadmapNodeKind;
  entityId: number;
  x: number;
  y: number;
  memberIds?: number[];
};
type RoadmapPort = "top" | "right" | "bottom" | "left";
type RoadmapConnection = { from: string; to: string; fromSide?: RoadmapPort; toSide?: RoadmapPort };
type RoadmapDrag = { id: string; offsetX: number; offsetY: number };
type RoadmapConnectionDrag = { from: string; fromSide: RoadmapPort };
type RoadmapPan = { x: number; y: number; scrollLeft: number; scrollTop: number };
type RoadmapBoard = { id: string; name: string; nodes: RoadmapNode[]; connections: RoadmapConnection[] };
type RoadmapViewportFrame = { left: number; top: number; width: number; height: number };

const ROADMAP_CANVAS_WIDTH = 12000;
const ROADMAP_CANVAS_HEIGHT = 8000;
const ROADMAP_NODE_WIDTH = 236;
const ROADMAP_NODE_HEIGHT = 112;
const ROADMAP_PROJECT_NODE_HEIGHT = 210;
const ROADMAP_MAX_VISIBLE_TASKS = 5;
const ROADMAP_GROUP_GAP = 14;
const ROADMAP_GROUP_PADDING = 14;
const ROADMAP_GROUP_HEADER_HEIGHT = 45;
const ROADMAP_STORAGE_PREFIX = "nextgen-roadmap-layout:";
const ROADMAP_PORTS: RoadmapPort[] = ["top", "right", "bottom", "left"];

function roadmapNodeWidth(node: RoadmapNode): number {
  if (node.kind !== "group") return ROADMAP_NODE_WIDTH;
  const count = Math.max(1, node.memberIds?.length || 0);
  return ROADMAP_GROUP_PADDING * 2 + count * ROADMAP_NODE_WIDTH + Math.max(0, count - 1) * ROADMAP_GROUP_GAP;
}

function centerRoadmapNodes(nodes: RoadmapNode[]): RoadmapNode[] {
  if (nodes.length === 0) return nodes;
  const minX = Math.min(...nodes.map((node) => node.x));
  const maxX = Math.max(...nodes.map((node) => node.x + roadmapNodeWidth(node)));
  const minY = Math.min(...nodes.map((node) => node.y));
  const maxY = Math.max(...nodes.map((node) => node.y + ROADMAP_PROJECT_NODE_HEIGHT));
  const offsetX = ROADMAP_CANVAS_WIDTH / 2 - (minX + maxX) / 2;
  const offsetY = ROADMAP_CANVAS_HEIGHT / 2 - (minY + maxY) / 2;

  return nodes.map((node) => ({
    ...node,
    x: Math.round(Math.max(16, Math.min(ROADMAP_CANVAS_WIDTH - roadmapNodeWidth(node) - 16, node.x + offsetX))),
    y: Math.round(Math.max(16, Math.min(ROADMAP_CANVAS_HEIGHT - ROADMAP_PROJECT_NODE_HEIGHT - 16, node.y + offsetY))),
  }));
}

const REQUEST_STAGES: { id: RequestStage; label: string }[] = [
  { id: "new", label: "Новые" },
  { id: "planned", label: "Запланированные" },
  { id: "deferred", label: "Отложенные" },
  { id: "handed_over", label: "Переданы в работу" },
];

function roadmapDueLabel(task: Task | undefined): string {
  if (!task?.due_date) return "Срок уточняется";
  return `До ${formatDateTime(task.due_date)}`;
}

function RoadmapProjectCard({ item }: { item: RoadmapProject }) {
  const { project, tasks } = item;
  const previewTasks = tasks.slice(0, 3);
  const remainingTasks = Math.max(tasks.length - previewTasks.length, 0);
  const progress = project.tasks_count > 0 ? `${project.done_count}/${project.tasks_count}` : "Планируем";
  const nextTask = tasks.find((task) => task.status === "in_progress") || tasks[0];

  return (
    <article className="roadmap-work-card">
      <div className="roadmap-work-card-head">
        <span className="roadmap-work-kind">Проект</span>
        <span className="roadmap-work-progress">{progress}</span>
      </div>
      <Link to={`/projects/${project.id}`} className="roadmap-work-title">
        {project.name}
      </Link>
      <div className="roadmap-task-list">
        {previewTasks.length > 0 ? (
          previewTasks.map((task) => (
            <Link key={task.id} to={`/tasks/${task.id}`} className={`roadmap-task status-${task.status}`}>
              <span aria-hidden className="roadmap-task-dot" />
              <span>{task.title}</span>
            </Link>
          ))
        ) : (
          <span className="roadmap-no-task">Задачи появятся после планирования</span>
        )}
        {remainingTasks > 0 ? <span className="roadmap-more-tasks">Ещё {remainingTasks}</span> : null}
      </div>
      <div className="roadmap-work-card-foot">
        <span>{roadmapDueLabel(nextTask)}</span>
        <Link to={`/projects/${project.id}`}>Открыть проект</Link>
      </div>
    </article>
  );
}

function nodeTitle(node: RoadmapNode, projects: Project[], tasks: Task[], ideas: BacklogItem[]): string {
  if (node.kind === "group") return "Параллельно";
  if (node.kind === "project") return projects.find((item) => item.id === node.entityId)?.name || "Проект";
  if (node.kind === "task") return tasks.find((item) => item.id === node.entityId)?.title || "Задача";
  return ideas.find((item) => item.id === node.entityId)?.title || "Инициатива";
}

function nodeMeta(node: RoadmapNode, projects: Project[], tasks: Task[], ideas: BacklogItem[]): string {
  if (node.kind === "project") {
    const project = projects.find((item) => item.id === node.entityId);
    return project ? `${project.done_count}/${project.tasks_count} задач завершено` : "Проект";
  }
  if (node.kind === "task") {
    const task = tasks.find((item) => item.id === node.entityId);
    return task?.status === "in_progress" ? "В работе" : "Запланирована";
  }
  const idea = ideas.find((item) => item.id === node.entityId);
  return idea?.status === "deferred" ? "Отложено" : "На оценке";
}

function projectTasks(projectId: number, tasks: Task[]): Task[] {
  return tasks
    .filter((task) => task.project === projectId)
    .sort((a, b) => {
      if (a.status === "in_progress" && b.status !== "in_progress") return -1;
      if (b.status === "in_progress" && a.status !== "in_progress") return 1;
      return String(a.due_date || "9999").localeCompare(String(b.due_date || "9999"));
    });
}

function roadmapDueDate(node: RoadmapNode, projects: Project[], tasks: Task[]): string | null {
  if (node.kind === "task") return tasks.find((task) => task.id === node.entityId)?.due_date || null;
  if (node.kind === "project") {
    const project = projects.find((item) => item.id === node.entityId);
    return projectTasks(node.entityId, tasks).find((task) => task.status !== "done")?.due_date || project?.due_date || null;
  }
  return null;
}

function roadmapNodeDueLabel(date: string | null): string {
  return date ? `Срок: ${formatDateTime(date)}` : "Срок уточняется";
}

function roadmapProjectNodeHeight(projectId: number, isExpanded: boolean, tasks: Task[]): number {
  if (!isExpanded) return ROADMAP_PROJECT_NODE_HEIGHT;
  const taskCount = Math.min(projectTasks(projectId, tasks).length, ROADMAP_MAX_VISIBLE_TASKS);
  const hasMore = projectTasks(projectId, tasks).length > ROADMAP_MAX_VISIBLE_TASKS;
  return ROADMAP_PROJECT_NODE_HEIGHT + 18 + Math.max(1, taskCount) * 31 + (hasMore ? 22 : 0);
}

function roadmapNodeHeight(
  node: RoadmapNode,
  isProjectExpanded: boolean,
  tasks: Task[],
  expandedNodes?: Set<string>,
): number {
  if (node.kind === "group") {
    const projectHeight = Math.max(
      ROADMAP_PROJECT_NODE_HEIGHT,
      ...(node.memberIds || []).map((projectId) => (
        roadmapProjectNodeHeight(projectId, expandedNodes?.has(`${node.id}:project-${projectId}`) || false, tasks)
      )),
    );
    return ROADMAP_GROUP_HEADER_HEIGHT + projectHeight + ROADMAP_GROUP_PADDING;
  }
  if (node.kind === "task") return 142;
  if (node.kind !== "project") return ROADMAP_NODE_HEIGHT;
  return roadmapProjectNodeHeight(node.entityId, isProjectExpanded, tasks);
}

function roadmapPortPoint(node: RoadmapNode, side: RoadmapPort, height = ROADMAP_NODE_HEIGHT) {
  const width = roadmapNodeWidth(node);
  if (side === "top") return { x: node.x + width / 2, y: node.y };
  if (side === "right") return { x: node.x + width, y: node.y + height / 2 };
  if (side === "bottom") return { x: node.x + width / 2, y: node.y + height };
  return { x: node.x, y: node.y + height / 2 };
}

function roadmapControlPoint(point: { x: number; y: number }, side: RoadmapPort, distance = 72) {
  if (side === "top") return { x: point.x, y: point.y - distance };
  if (side === "right") return { x: point.x + distance, y: point.y };
  if (side === "bottom") return { x: point.x, y: point.y + distance };
  return { x: point.x - distance, y: point.y };
}

const ROADMAP_ARROW_LENGTH = 11;
const ROADMAP_ARROW_HALF = 4.5;
const ROADMAP_PORT_GAP = 2;

function roadmapConnectionGeometry(
  from: RoadmapNode,
  to: RoadmapNode | { x: number; y: number },
  fromSide: RoadmapPort,
  toSide: RoadmapPort,
  fromHeight = ROADMAP_NODE_HEIGHT,
  toHeight = ROADMAP_NODE_HEIGHT,
) {
  const start = roadmapPortPoint(from, fromSide, fromHeight);
  const end = "id" in to ? roadmapPortPoint(to, toSide, toHeight) : to;
  const distanceFor = (side: RoadmapPort, point: { x: number; y: number }) => {
    const axis = side === "left" || side === "right"
      ? Math.abs(point.x - start.x)
      : Math.abs(point.y - start.y);
    return Math.max(36, Math.min(96, axis * 0.45));
  };
  const endControl = roadmapControlPoint(end, toSide, distanceFor(toSide, end));
  const dx = end.x - endControl.x;
  const dy = end.y - endControl.y;
  const length = Math.hypot(dx, dy) || 1;
  const dirX = dx / length;
  const dirY = dy / length;
  const landsOnPort = "id" in to;
  const tip = landsOnPort
    ? { x: end.x - dirX * ROADMAP_PORT_GAP, y: end.y - dirY * ROADMAP_PORT_GAP }
    : end;
  const base = landsOnPort
    ? { x: tip.x - dirX * ROADMAP_ARROW_LENGTH, y: tip.y - dirY * ROADMAP_ARROW_LENGTH }
    : end;
  const startControl = roadmapControlPoint(start, fromSide, distanceFor(fromSide, base));
  const curveEndControl = roadmapControlPoint(base, toSide, distanceFor(toSide, base));
  const px = -dirY * ROADMAP_ARROW_HALF;
  const py = dirX * ROADMAP_ARROW_HALF;
  const joinFrom = { x: base.x - dirX * 8, y: base.y - dirY * 8 };
  return {
    d: `M ${start.x} ${start.y} C ${startControl.x} ${startControl.y}, ${curveEndControl.x} ${curveEndControl.y}, ${base.x} ${base.y}`,
    join: landsOnPort ? `M ${joinFrom.x} ${joinFrom.y} L ${base.x} ${base.y}` : "",
    arrow: landsOnPort
      ? `M ${tip.x} ${tip.y} L ${base.x + px} ${base.y + py} L ${base.x - px} ${base.y - py} Z`
      : "",
  };
}

function RoadmapBuilder({
  portalId,
  projects,
  tasks,
  ideas,
  taskProjectId,
}: {
  portalId: number | null;
  projects: Project[];
  tasks: Task[];
  ideas: BacklogItem[];
  taskProjectId?: number;
}) {
  const [nodes, setNodes] = useState<RoadmapNode[]>([]);
  const [connections, setConnections] = useState<RoadmapConnection[]>([]);
  const [boards, setBoards] = useState<RoadmapBoard[]>([]);
  const [selectedBoardId, setSelectedBoardId] = useState("");
  const [isLibraryCollapsed, setIsLibraryCollapsed] = useState(true);
  const [isCreatingBoard, setIsCreatingBoard] = useState(false);
  const [newBoardName, setNewBoardName] = useState("");
  const [drag, setDrag] = useState<RoadmapDrag | null>(null);
  const [connectionDrag, setConnectionDrag] = useState<RoadmapConnectionDrag | null>(null);
  const [connectionCursor, setConnectionCursor] = useState<{ x: number; y: number } | null>(null);
  const [connectionTarget, setConnectionTarget] = useState<{ nodeId: string; side: RoadmapPort } | null>(null);
  const [pan, setPan] = useState<RoadmapPan | null>(null);
  const [groupHoverId, setGroupHoverId] = useState<string | null>(null);
  const [expandedProjectNodes, setExpandedProjectNodes] = useState<Set<string>>(() => new Set());
  const [zoom, setZoom] = useState(100);
  const [viewportFrame, setViewportFrame] = useState<RoadmapViewportFrame>({ left: 0, top: 0, width: 0, height: 0 });
  const [layoutLoaded, setLayoutLoaded] = useState(false);
  const canvasViewportRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom);
  const pendingZoomRef = useRef(zoom);
  const zoomScrollFrameRef = useRef<number | null>(null);
  const zoomGestureRef = useRef<{
    contentX: number;
    contentY: number;
    pointerX: number;
    pointerY: number;
    nextZoom: number;
  } | null>(null);
  const viewportSyncFrameRef = useRef<number | null>(null);
  const centeredBoardRef = useRef("");
  zoomRef.current = zoom;

  const syncViewportFrame = useCallback(() => {
    if (viewportSyncFrameRef.current !== null) return;
    viewportSyncFrameRef.current = window.requestAnimationFrame(() => {
      viewportSyncFrameRef.current = null;
      const viewport = canvasViewportRef.current;
      if (!viewport) return;
      const scale = zoomRef.current / 100;
      const next = {
        left: viewport.scrollLeft / scale,
        top: viewport.scrollTop / scale,
        width: viewport.clientWidth / scale,
        height: viewport.clientHeight / scale,
      };
      setViewportFrame((current) => (
        Math.abs(current.left - next.left) < 0.5
        && Math.abs(current.top - next.top) < 0.5
        && Math.abs(current.width - next.width) < 0.5
        && Math.abs(current.height - next.height) < 0.5
          ? current
          : next
      ));
    });
  }, []);

  const applyZoom = useCallback((nextZoom: number, pointer?: { clientX: number; clientY: number }) => {
    const viewport = canvasViewportRef.current;
    const currentZoom = zoomRef.current;
    const clampedZoom = Math.max(75, Math.min(125, nextZoom));
    if (!viewport || (clampedZoom === currentZoom && zoomScrollFrameRef.current === null)) return;

    pendingZoomRef.current = clampedZoom;
    if (!zoomGestureRef.current) {
      const bounds = viewport.getBoundingClientRect();
      const pointerX = pointer ? pointer.clientX - bounds.left : viewport.clientWidth / 2;
      const pointerY = pointer ? pointer.clientY - bounds.top : viewport.clientHeight / 2;
      zoomGestureRef.current = {
        contentX: (viewport.scrollLeft + pointerX) / (currentZoom / 100),
        contentY: (viewport.scrollTop + pointerY) / (currentZoom / 100),
        pointerX,
        pointerY,
        nextZoom: clampedZoom,
      };
    } else {
      zoomGestureRef.current.nextZoom = clampedZoom;
    }
    if (zoomScrollFrameRef.current !== null) return;
    zoomScrollFrameRef.current = window.requestAnimationFrame(() => {
      zoomScrollFrameRef.current = null;
      const gesture = zoomGestureRef.current;
      zoomGestureRef.current = null;
      if (!gesture) return;
      zoomRef.current = gesture.nextZoom;
      pendingZoomRef.current = gesture.nextZoom;
      flushSync(() => setZoom(gesture.nextZoom));
      viewport.scrollLeft = gesture.contentX * (gesture.nextZoom / 100) - gesture.pointerX;
      viewport.scrollTop = gesture.contentY * (gesture.nextZoom / 100) - gesture.pointerY;
      syncViewportFrame();
    });
  }, [syncViewportFrame]);

  useEffect(() => {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    const handleChange = () => syncViewportFrame();
    viewport.addEventListener("scroll", handleChange, { passive: true });
    window.addEventListener("resize", handleChange);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(handleChange);
    observer?.observe(viewport);
    handleChange();
    return () => {
      viewport.removeEventListener("scroll", handleChange);
      window.removeEventListener("resize", handleChange);
      observer?.disconnect();
      if (viewportSyncFrameRef.current !== null) {
        window.cancelAnimationFrame(viewportSyncFrameRef.current);
        viewportSyncFrameRef.current = null;
      }
    };
  }, [syncViewportFrame]);

  useEffect(() => {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;

    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      applyZoom(pendingZoomRef.current + (event.deltaY < 0 ? 5 : -5), {
        clientX: event.clientX,
        clientY: event.clientY,
      });
    };

    viewport.addEventListener("wheel", handleWheel, { passive: false });
    return () => {
      viewport.removeEventListener("wheel", handleWheel);
      if (zoomScrollFrameRef.current !== null) {
        window.cancelAnimationFrame(zoomScrollFrameRef.current);
        zoomScrollFrameRef.current = null;
      }
      zoomGestureRef.current = null;
    };
  }, [applyZoom]);

  useEffect(() => {
    const viewport = canvasViewportRef.current;
    if (!viewport || !selectedBoardId || centeredBoardRef.current === selectedBoardId) return;

    const frame = window.requestAnimationFrame(() => {
      const scale = zoomRef.current / 100;
      const centerX = nodes.length > 0
        ? (Math.min(...nodes.map((node) => node.x)) + Math.max(...nodes.map((node) => node.x + roadmapNodeWidth(node)))) / 2
        : ROADMAP_CANVAS_WIDTH / 2;
      const centerY = nodes.length > 0
        ? (Math.min(...nodes.map((node) => node.y)) + Math.max(...nodes.map((node) => node.y + ROADMAP_NODE_HEIGHT))) / 2
        : ROADMAP_CANVAS_HEIGHT / 2;
      viewport.scrollLeft = Math.max(0, centerX * scale - viewport.clientWidth / 2);
      viewport.scrollTop = Math.max(0, centerY * scale - viewport.clientHeight / 2);
      centeredBoardRef.current = selectedBoardId;
      syncViewportFrame();
    });

    return () => window.cancelAnimationFrame(frame);
  }, [nodes, selectedBoardId, syncViewportFrame]);

  useEffect(() => {
    setLayoutLoaded(false);
    setNodes([]);
    setConnections([]);
    setBoards([]);
    setSelectedBoardId("");
    centeredBoardRef.current = "";
    if (!portalId) return;
    const layoutKey = `${ROADMAP_STORAGE_PREFIX}${portalId}${taskProjectId ? `:project:${taskProjectId}` : ""}`;
    try {
      const raw = localStorage.getItem(layoutKey);
      if (raw) {
        const saved = JSON.parse(raw) as { boards?: RoadmapBoard[]; selectedBoardId?: string; nodes?: RoadmapNode[]; connections?: RoadmapConnection[] };
        if (Array.isArray(saved.boards) && saved.boards.length > 0) {
          const centeredBoards = saved.boards.map((board) => ({ ...board, nodes: centerRoadmapNodes(board.nodes || []) }));
          const selectedId = centeredBoards.some((board) => board.id === saved.selectedBoardId) ? saved.selectedBoardId! : centeredBoards[0].id;
          const selectedBoard = centeredBoards.find((board) => board.id === selectedId)!;
          setBoards(centeredBoards);
          setSelectedBoardId(selectedId);
          setNodes(selectedBoard.nodes);
          setConnections(selectedBoard.connections || []);
        } else {
          const initialBoard: RoadmapBoard = {
            id: "main",
            name: "Основная доска",
            nodes: centerRoadmapNodes(Array.isArray(saved.nodes) ? saved.nodes : []),
            connections: Array.isArray(saved.connections) ? saved.connections : [],
          };
          setBoards([initialBoard]);
          setSelectedBoardId(initialBoard.id);
          setNodes(initialBoard.nodes);
          setConnections(initialBoard.connections);
        }
      } else {
        const initialBoard: RoadmapBoard = { id: "main", name: "Основная доска", nodes: [], connections: [] };
        setBoards([initialBoard]);
        setSelectedBoardId(initialBoard.id);
      }
    } catch {
      // A malformed old layout should never prevent access to the planning board.
    } finally {
      setLayoutLoaded(true);
    }
  }, [portalId, taskProjectId]);

  useEffect(() => {
    if (!portalId || !layoutLoaded) return;
    setBoards((current) => current.map((board) => (
      board.id === selectedBoardId ? { ...board, nodes, connections } : board
    )));
  }, [selectedBoardId, nodes, connections, portalId, layoutLoaded]);

  useEffect(() => {
    if (!portalId || !layoutLoaded || boards.length === 0) return;
    const layoutKey = `${ROADMAP_STORAGE_PREFIX}${portalId}${taskProjectId ? `:project:${taskProjectId}` : ""}`;
    localStorage.setItem(
      layoutKey,
      JSON.stringify({ boards, selectedBoardId })
    );
  }, [portalId, taskProjectId, boards, selectedBoardId, layoutLoaded]);

  function removeNode(nodeId: string) {
    const node = nodes.find((item) => item.id === nodeId);
    if (node?.kind === "group") {
      const released = (node.memberIds || [])
        .filter((projectId) => !nodes.some((item) => item.kind === "project" && item.entityId === projectId))
        .map((projectId, index) => ({
          id: `project-${projectId}`,
          kind: "project" as const,
          entityId: projectId,
          x: Math.min(ROADMAP_CANVAS_WIDTH - ROADMAP_NODE_WIDTH - 16, node.x + roadmapNodeWidth(node) + 24),
          y: Math.min(ROADMAP_CANVAS_HEIGHT - ROADMAP_PROJECT_NODE_HEIGHT - 16, node.y + index * 36),
        }));
      setNodes((current) => current.filter((item) => item.id !== nodeId).concat(released));
    } else {
      setNodes((current) => current.filter((item) => item.id !== nodeId));
    }
    setConnections((current) => current.filter((connection) => connection.from !== nodeId && connection.to !== nodeId));
    setExpandedProjectNodes((current) => {
      const next = new Set(current);
      next.delete(nodeId);
      return next;
    });
  }

  function removeConnection(connectionToRemove: RoadmapConnection) {
    setConnections((current) => current.filter((connection) => connection !== connectionToRemove));
  }

  function selectBoard(boardId: string) {
    if (boardId === selectedBoardId) return;
    const nextBoard = boards.find((board) => board.id === boardId);
    if (!nextBoard) return;
    setBoards((current) => current.map((board) => (
      board.id === selectedBoardId ? { ...board, nodes, connections } : board
    )));
    setSelectedBoardId(boardId);
    setNodes(nextBoard.nodes);
    setConnections(nextBoard.connections);
    setExpandedProjectNodes(new Set());
    centeredBoardRef.current = "";
  }

  function createBoard(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newBoardName.trim();
    if (!name) return;
    const board: RoadmapBoard = { id: `board-${Date.now()}`, name, nodes: [], connections: [] };
    setBoards((current) => current.map((item) => (
      item.id === selectedBoardId ? { ...item, nodes, connections } : item
    )).concat(board));
    setSelectedBoardId(board.id);
    setNodes([]);
    setConnections([]);
    setExpandedProjectNodes(new Set());
    setNewBoardName("");
    setIsCreatingBoard(false);
    setIsLibraryCollapsed(true);
    centeredBoardRef.current = "";
  }

  function moveFromMinimap(event: React.MouseEvent<HTMLButtonElement>) {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const scale = zoomRef.current / 100;
    const worldX = ((event.clientX - bounds.left) / bounds.width) * ROADMAP_CANVAS_WIDTH;
    const worldY = ((event.clientY - bounds.top) / bounds.height) * ROADMAP_CANVAS_HEIGHT;
    viewport.scrollLeft = worldX * scale - viewport.clientWidth / 2;
    viewport.scrollTop = worldY * scale - viewport.clientHeight / 2;
    syncViewportFrame();
  }

  function addProjectToBoard(projectId: number, x: number, y: number) {
    setNodes((current) => {
      if (current.some((node) => (node.kind === "project" && node.entityId === projectId) || (node.kind === "group" && node.memberIds?.includes(projectId)))) return current;
      return [...current, {
        id: `project-${projectId}`,
        kind: "project",
        entityId: projectId,
        x: Math.max(16, Math.min(ROADMAP_CANVAS_WIDTH - ROADMAP_NODE_WIDTH - 16, x)),
        y: Math.max(16, Math.min(ROADMAP_CANVAS_HEIGHT - ROADMAP_PROJECT_NODE_HEIGHT - 16, y)),
      }];
    });
  }

  function addGroup() {
    const viewport = canvasViewportRef.current;
    const scale = zoom / 100;
    const x = viewport ? viewport.scrollLeft / scale + 56 : 80;
    const y = viewport ? viewport.scrollTop / scale + 72 : 80;
    setNodes((current) => [...current, {
      id: `group-${Date.now()}`,
      kind: "group",
      entityId: 0,
      memberIds: [],
      x: Math.max(16, Math.min(ROADMAP_CANVAS_WIDTH - ROADMAP_NODE_WIDTH - 16, x)),
      y: Math.max(16, Math.min(ROADMAP_CANVAS_HEIGHT - 140, y)),
    }]);
  }

  function absorbProject(groupId: string, projectId: number) {
    const standalone = nodes.find((node) => node.kind === "project" && node.entityId === projectId);
    setNodes((current) => current
      .filter((node) => !(node.kind === "project" && node.entityId === projectId))
      .map((node) => {
        if (node.id !== groupId) return node;
        const next = { ...node, memberIds: Array.from(new Set([...(node.memberIds || []), projectId])) };
        return { ...next, x: Math.min(next.x, ROADMAP_CANVAS_WIDTH - roadmapNodeWidth(next) - 16) };
      }));
    if (standalone) {
      setConnections((current) => current.filter((connection) => connection.from !== standalone.id && connection.to !== standalone.id));
    }
  }

  function ejectProject(groupId: string, projectId: number) {
    const group = nodes.find((node) => node.id === groupId);
    if (!group) return;
    setNodes((current) => current
      .map((node) => (
        node.id === groupId
          ? { ...node, memberIds: (node.memberIds || []).filter((id) => id !== projectId) }
          : node
      ))
      .concat(current.some((node) => node.kind === "project" && node.entityId === projectId) ? [] : [{
        id: `project-${projectId}`,
        kind: "project" as const,
        entityId: projectId,
        x: Math.min(ROADMAP_CANVAS_WIDTH - ROADMAP_NODE_WIDTH - 16, group.x + roadmapNodeWidth(group) + 24),
        y: group.y,
      }]));
  }

  function addTaskToBoard(taskId: number, x: number, y: number) {
    setNodes((current) => {
      if (current.some((node) => node.kind === "task" && node.entityId === taskId)) return current;
      return [...current, {
        id: `task-${taskId}`,
        kind: "task",
        entityId: taskId,
        x: Math.max(16, Math.min(ROADMAP_CANVAS_WIDTH - ROADMAP_NODE_WIDTH - 16, x)),
        y: Math.max(16, Math.min(ROADMAP_CANVAS_HEIGHT - 142 - 16, y)),
      }];
    });
  }

  function startProjectDrag(event: React.DragEvent<HTMLButtonElement>, projectId: number) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-nextgen-project", String(projectId));
  }

  function startTaskDrag(event: React.DragEvent<HTMLButtonElement>, taskId: number) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-nextgen-task", String(taskId));
  }

  function dropProject(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const bounds = event.currentTarget.getBoundingClientRect();
    const scale = zoom / 100;
    const x = (event.clientX - bounds.left) / scale;
    const y = (event.clientY - bounds.top) / scale;
    if (taskProjectId) {
      const taskId = Number(event.dataTransfer.getData("application/x-nextgen-task"));
      if (!Number.isFinite(taskId) || taskId <= 0) return;
      addTaskToBoard(taskId, x - ROADMAP_NODE_WIDTH / 2, y - 71);
      return;
    }
    const projectId = Number(event.dataTransfer.getData("application/x-nextgen-project"));
    if (!Number.isFinite(projectId) || projectId <= 0) return;
    addProjectToBoard(projectId, x - ROADMAP_NODE_WIDTH / 2, y - ROADMAP_PROJECT_NODE_HEIGHT / 2);
  }

  function toggleProjectTasks(nodeId: string) {
    setExpandedProjectNodes((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }

  function startDrag(event: React.PointerEvent<HTMLElement>, node: RoadmapNode) {
    if (event.target instanceof Element && event.target.closest("button")) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = event.currentTarget.getBoundingClientRect();
    const scale = zoom / 100;
    setDrag({ id: node.id, offsetX: (event.clientX - bounds.left) / scale, offsetY: (event.clientY - bounds.top) / scale });
  }

  function startPan(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || event.target !== event.currentTarget) return;
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setPan({ x: event.clientX, y: event.clientY, scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop });
  }

  function moveNode(event: React.PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const scale = zoom / 100;
    if (pan) {
      const viewport = canvasViewportRef.current;
      if (viewport) {
        viewport.scrollLeft = pan.scrollLeft - (event.clientX - pan.x);
        viewport.scrollTop = pan.scrollTop - (event.clientY - pan.y);
      }
      return;
    }
    if (connectionDrag) {
      setConnectionCursor({ x: (event.clientX - bounds.left) / scale, y: (event.clientY - bounds.top) / scale });
      const elementUnderPointer = document.elementFromPoint(event.clientX, event.clientY);
      const target = elementUnderPointer instanceof HTMLElement ? elementUnderPointer.closest<HTMLElement>("[data-roadmap-port]") : null;
      const nodeId = target?.dataset.nodeId;
      const side = target?.dataset.portSide as RoadmapPort | undefined;
      const nextTarget = nodeId && side && nodeId !== connectionDrag.from ? { nodeId, side } : null;
      setConnectionTarget((current) => (
        current?.nodeId === nextTarget?.nodeId && current?.side === nextTarget?.side ? current : nextTarget
      ));
      return;
    }
    if (!drag) return;
    const draggedNode = nodes.find((node) => node.id === drag.id);
    const draggedNodeHeight = draggedNode ? roadmapNodeHeight(draggedNode, expandedProjectNodes.has(draggedNode.id), tasks, expandedProjectNodes) : ROADMAP_NODE_HEIGHT;
    const draggedNodeWidth = draggedNode ? roadmapNodeWidth(draggedNode) : ROADMAP_NODE_WIDTH;
    const x = Math.max(16, Math.min(ROADMAP_CANVAS_WIDTH - draggedNodeWidth - 16, (event.clientX - bounds.left) / scale - drag.offsetX));
    const y = Math.max(16, Math.min(ROADMAP_CANVAS_HEIGHT - draggedNodeHeight - 16, (event.clientY - bounds.top) / scale - drag.offsetY));
    setNodes((current) => current.map((node) => (node.id === drag.id ? { ...node, x, y } : node)));
    const under = document.elementFromPoint(event.clientX, event.clientY);
    const groupId = under instanceof HTMLElement ? under.closest<HTMLElement>("[data-roadmap-group]")?.dataset.roadmapGroup : undefined;
    const nextHover = draggedNode?.kind === "project" && groupId && groupId !== drag.id ? groupId : null;
    setGroupHoverId((current) => (current === nextHover ? current : nextHover));
  }

  function startConnection(event: React.PointerEvent<HTMLButtonElement>, nodeId: string, side: RoadmapPort) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setConnectionDrag({ from: nodeId, fromSide: side });
    setConnectionCursor(null);
  }

  function finishPointerAction(event: React.PointerEvent<HTMLDivElement>) {
    if (connectionDrag) {
      const elementUnderPointer = document.elementFromPoint(event.clientX, event.clientY);
      const target = elementUnderPointer instanceof HTMLElement ? elementUnderPointer.closest<HTMLElement>("[data-roadmap-port]") : null;
      const to = target?.dataset.nodeId;
      const toSide = target?.dataset.portSide as RoadmapPort | undefined;
      if (to && to !== connectionDrag.from && toSide) {
        setConnections((current) => (
          current.some((connection) => connection.from === connectionDrag.from && connection.to === to && connection.fromSide === connectionDrag.fromSide && connection.toSide === toSide)
            ? current
            : [...current, { from: connectionDrag.from, to, fromSide: connectionDrag.fromSide, toSide }]
        ));
      }
    } else if (drag && groupHoverId) {
      const dragged = nodes.find((node) => node.id === drag.id);
      if (dragged?.kind === "project") absorbProject(groupHoverId, dragged.entityId);
    }
    setDrag(null);
    setConnectionDrag(null);
    setConnectionCursor(null);
    setConnectionTarget(null);
    setGroupHoverId(null);
    setPan(null);
  }

  const scale = zoom / 100;
  const availableProjects = projects.filter((project) => (
    project.is_active
    && !nodes.some((node) => (
      (node.kind === "project" && node.entityId === project.id)
      || (node.kind === "group" && node.memberIds?.includes(project.id))
    ))
  ));
  const availableTasks = taskProjectId
    ? tasks.filter((task) => task.project === taskProjectId && !nodes.some((node) => node.kind === "task" && node.entityId === task.id))
    : [];
  const nodeHeights = new Map(nodes.map((node) => [node.id, roadmapNodeHeight(node, expandedProjectNodes.has(node.id), tasks, expandedProjectNodes)]));

  return (
    <section className="roadmap-builder" aria-label="Редактор дорожной карты">
      <div className="roadmap-canvas-viewport" ref={canvasViewportRef}>
        <div className="roadmap-board-controls">
          <div className="roadmap-left-tools">
            {taskProjectId ? (
              <Link to="/requests" className="roadmap-project-back" title="Вернуться в общую дорожную карту">
                <span aria-hidden>←</span>
                Общая карта
              </Link>
            ) : null}
            <label className="roadmap-current-board">
              <select value={selectedBoardId} onChange={(event) => selectBoard(event.target.value)} aria-label="Текущая доска">
                {boards.map((board) => <option key={board.id} value={board.id}>{board.name}</option>)}
              </select>
            </label>
            <button
              type="button"
              className={`roadmap-tool-button${isCreatingBoard ? " is-active" : ""}`}
              title="Создать доску"
              aria-label="Создать доску"
              aria-expanded={isCreatingBoard}
              onClick={() => {
                const shouldOpen = !isCreatingBoard;
                setIsCreatingBoard(shouldOpen);
                setIsLibraryCollapsed(!shouldOpen);
              }}
            >+</button>
            <button
              type="button"
              className={`roadmap-tool-button roadmap-projects-trigger${!isLibraryCollapsed && !isCreatingBoard ? " is-active" : ""}`}
              aria-label={taskProjectId ? "Библиотека задач" : "Библиотека проектов"}
              aria-expanded={!isLibraryCollapsed && !isCreatingBoard}
              title={taskProjectId ? "Библиотека задач" : "Библиотека проектов"}
              onClick={() => { setIsLibraryCollapsed((current) => !current); setIsCreatingBoard(false); }}
            >
              <span className="roadmap-library-icon" aria-hidden />
            </button>
            {taskProjectId ? null : (
              <button type="button" className="roadmap-group-add" onClick={addGroup}>
                Параллельно
              </button>
            )}
          </div>
          <div className="roadmap-builder-intro">
            <label className="roadmap-zoom-control">
              <span>{zoom}%</span>
              <input aria-label={`Масштаб ${zoom}%`} type="range" min="75" max="125" step="5" value={zoom} onChange={(event) => applyZoom(Number(event.target.value))} />
            </label>
          </div>

          {!isLibraryCollapsed ? (
          <aside className="roadmap-library" aria-label={isCreatingBoard ? "Создание доски" : taskProjectId ? "Библиотека задач" : "Библиотека проектов"}>
              <div className="roadmap-library-body">
                <header className="roadmap-library-topbar">
                  <div>
                    <strong>{isCreatingBoard ? "Новая доска" : taskProjectId ? "Библиотека задач" : "Библиотека проектов"}</strong>
                  </div>
                </header>

                {isCreatingBoard ? (
                  <form className="roadmap-new-board-form" onSubmit={createBoard}>
                    <input autoFocus value={newBoardName} onChange={(event) => setNewBoardName(event.target.value)} placeholder="Название новой доски" />
                    <button type="submit">Создать</button>
                  </form>
                ) : taskProjectId ? (
                <section className="roadmap-library-section roadmap-project-library">
                  <div className="roadmap-library-heading">
                    <span>Задачи</span>
                    <small>{availableTasks.length}</small>
                  </div>
                  <p className="roadmap-library-hint">Перетащите задачу на поле, чтобы добавить её в план.</p>
                  <div className="roadmap-project-source-list">
                    {availableTasks.length > 0 ? availableTasks.map((task) => (
                      <button key={task.id} type="button" draggable onDragStart={(event) => startTaskDrag(event, task.id)}>
                        <span className="roadmap-source-project-copy">
                          <strong>{task.title}</strong>
                          <small>{STATUS_LABEL[task.status]}<span>{task.due_date ? roadmapDueLabel(task) : "Срок уточняется"}</span></small>
                        </span>
                      </button>
                    )) : <span className="roadmap-source-empty">Все задачи проекта уже есть на этой доске.</span>}
                  </div>
                </section>
                ) : (
                <section className="roadmap-library-section roadmap-project-library">
                  <div className="roadmap-library-heading">
                    <span>Проекты</span>
                    <small>{availableProjects.length}</small>
                  </div>
                  <p className="roadmap-library-hint">Перетащите проект на поле, чтобы добавить его в план.</p>
                  <div className="roadmap-project-source-list">
                    {availableProjects.length > 0 ? availableProjects.map((project) => {
                      const relatedTasks = projectTasks(project.id, tasks);
                      const nextTask = relatedTasks.find((task) => task.status !== "done");
                      const isComplete = project.tasks_count > 0 && project.done_count === project.tasks_count;
                      return (
                        <button key={project.id} type="button" draggable onDragStart={(event) => startProjectDrag(event, project.id)}>
                          <span className="roadmap-source-project-copy">
                            <strong>{project.name}</strong>
                            <small>{project.tasks_count ? `${project.done_count} из ${project.tasks_count} задач` : "Планирование"}<span>{roadmapDueLabel(nextTask)}</span></small>
                            <i className="roadmap-source-progress" aria-hidden><i style={{ width: `${project.tasks_count ? Math.min(100, project.done_count / project.tasks_count * 100) : 0}%` }} className={isComplete ? "is-complete" : ""} /></i>
                          </span>
                        </button>
                      );
                    }) : <span className="roadmap-source-empty">Все активные проекты уже есть на этой доске.</span>}
                  </div>
                </section>
                )}
              </div>
          </aside>
          ) : null}
        </div>
        <div className="roadmap-canvas-stage" style={{ width: `${ROADMAP_CANVAS_WIDTH * scale}px`, height: `${ROADMAP_CANVAS_HEIGHT * scale}px` }}>
          <div
            className={`roadmap-canvas${connectionDrag ? " is-connecting" : ""}${pan ? " is-panning" : ""}`}
            style={{ width: ROADMAP_CANVAS_WIDTH, height: ROADMAP_CANVAS_HEIGHT, zoom: scale }}
            onPointerDown={startPan}
            onPointerMove={moveNode}
            onPointerUp={finishPointerAction}
            onPointerCancel={() => { setDrag(null); setConnectionDrag(null); setConnectionCursor(null); setConnectionTarget(null); setGroupHoverId(null); setPan(null); }}
            onDragOver={(event) => event.preventDefault()}
            onDrop={dropProject}
          >
            <svg className="roadmap-connections" width={ROADMAP_CANVAS_WIDTH} height={ROADMAP_CANVAS_HEIGHT} aria-hidden>
              {connections.map((connection) => {
                const from = nodes.find((node) => node.id === connection.from);
                const to = nodes.find((node) => node.id === connection.to);
                if (!from || !to) return null;
                const geometry = roadmapConnectionGeometry(from, to, connection.fromSide || "right", connection.toSide || "left", nodeHeights.get(from.id), nodeHeights.get(to.id));
                return (
                  <g key={`${connection.from}-${connection.to}-${connection.fromSide || "right"}-${connection.toSide || "left"}`} className="roadmap-connection">
                    <path
                      className="roadmap-connection-hit"
                      d={geometry.d}
                      tabIndex={0}
                      role="button"
                      aria-label="Удалить связь"
                      onClick={(event) => { event.stopPropagation(); removeConnection(connection); }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          removeConnection(connection);
                        }
                      }}
                    >
                      <title>Нажмите, чтобы удалить связь</title>
                    </path>
                    <path className="roadmap-connection-line" d={geometry.d} />
                    <path className="roadmap-connection-join" d={geometry.join} />
                    <path className="roadmap-connection-arrow" d={geometry.arrow} />
                  </g>
                );
              })}
              {connectionDrag && connectionCursor ? (() => {
                const from = nodes.find((node) => node.id === connectionDrag.from);
                return from ? <path className="is-preview" d={roadmapConnectionGeometry(from, connectionCursor, connectionDrag.fromSide, "left", nodeHeights.get(from.id)).d} /> : null;
              })() : null}
            </svg>

            {nodes.map((node) => {
              const title = nodeTitle(node, projects, tasks, ideas);
              const path = node.kind === "project" ? `/projects/${node.entityId}` : node.kind === "task" ? `/tasks/${node.entityId}` : `/requests/${node.entityId}`;
              const isProjectExpanded = node.kind === "project" && expandedProjectNodes.has(node.id);
              const relatedTasks = node.kind === "project" ? projectTasks(node.entityId, tasks) : [];
              const visibleTasks = relatedTasks.slice(0, ROADMAP_MAX_VISIBLE_TASKS);
              const projectNodes = nodes.filter((item) => item.kind === "project");
              const flowIndex = projectNodes.findIndex((item) => item.id === node.id) + 1;
              const nodeHeight = roadmapNodeHeight(node, isProjectExpanded, tasks, expandedProjectNodes);
              const dueLabel = roadmapNodeDueLabel(roadmapDueDate(node, projects, tasks));
              const isGroup = node.kind === "group";
              return (
                <article
                  key={node.id}
                  className={`roadmap-node is-${node.kind}${isProjectExpanded ? " is-expanded" : ""}${connectionDrag?.from === node.id ? " is-connecting" : ""}${drag?.id === node.id ? " is-dragging" : ""}${isGroup && groupHoverId === node.id ? " is-group-target" : ""}`}
                  style={{ left: node.x, top: node.y, width: roadmapNodeWidth(node), height: nodeHeight }}
                  data-roadmap-group={isGroup ? node.id : undefined}
                  onDragOver={isGroup ? (event) => { event.preventDefault(); event.stopPropagation(); setGroupHoverId(node.id); } : undefined}
                  onDragLeave={isGroup ? () => setGroupHoverId((current) => (current === node.id ? null : current)) : undefined}
                  onDrop={isGroup ? (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    const projectId = Number(event.dataTransfer.getData("application/x-nextgen-project"));
                    if (Number.isFinite(projectId) && projectId > 0) absorbProject(node.id, projectId);
                    setGroupHoverId(null);
                  } : undefined}
                >
                  <header className="roadmap-node-drag" onPointerDown={(event) => startDrag(event, node)}>
                    <span>{isGroup ? "Параллельно" : node.kind === "project" ? `Проект${projectNodes.length > 1 ? ` · Поток ${flowIndex}` : ""}` : node.kind === "task" ? "Задача" : "Идея"}</span>
                    <button
                      type="button"
                      title="Убрать с дорожной карты"
                      aria-label="Убрать с дорожной карты"
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={() => removeNode(node.id)}
                    >
                      ×
                    </button>
                  </header>
                  {isGroup ? (
                    <div className="roadmap-group-body">
                      {(node.memberIds || []).length > 0 ? (node.memberIds || []).map((projectId) => {
                        const project = projects.find((item) => item.id === projectId);
                        const groupedTasks = projectTasks(projectId, tasks);
                        const visibleGroupedTasks = groupedTasks.slice(0, ROADMAP_MAX_VISIBLE_TASKS);
                        const memberNodeId = `${node.id}:project-${projectId}`;
                        const isMemberExpanded = expandedProjectNodes.has(memberNodeId);
                        const memberDue = roadmapNodeDueLabel(
                          groupedTasks.find((task) => task.status !== "done")?.due_date || project?.due_date || null
                        );
                        return (
                          <article key={projectId} className={`roadmap-group-project${isMemberExpanded ? " is-expanded" : ""}`}>
                            <header className="roadmap-group-project-head">
                              <span>Проект</span>
                              <button type="button" aria-label="Убрать проект из параллельного потока" onClick={() => ejectProject(node.id, projectId)}>×</button>
                            </header>
                            <Link to={`/projects/${projectId}`} className="roadmap-node-title">
                              {project?.name || "Проект"}
                            </Link>
                            <span className="roadmap-node-meta">
                              {project ? `${project.done_count}/${project.tasks_count} задач завершено` : "Проект"}
                            </span>
                            <span className="roadmap-node-due">{memberDue}</span>
                            <button type="button" className="roadmap-project-tasks-toggle" onClick={() => toggleProjectTasks(memberNodeId)} aria-expanded={isMemberExpanded}>
                              {isMemberExpanded ? "Свернуть задачи" : "Раскрыть задачи"}
                              <span>{groupedTasks.length}</span>
                            </button>
                            <Link to={`/projects/${projectId}/roadmap`} className="roadmap-project-open-map">
                              Открыть дорожную карту
                            </Link>
                            {isMemberExpanded ? (
                              <div className="roadmap-project-task-list">
                                {visibleGroupedTasks.length > 0 ? visibleGroupedTasks.map((task) => (
                                  <Link key={task.id} to={`/tasks/${task.id}`} className={`roadmap-project-task status-${task.status}`}>
                                    <span aria-hidden />
                                    <span>{task.title}</span>
                                    <time>{task.due_date ? formatDateTime(task.due_date) : "Без срока"}</time>
                                  </Link>
                                )) : <span className="roadmap-project-task-empty">Задач пока нет</span>}
                                {groupedTasks.length > visibleGroupedTasks.length ? <span className="roadmap-project-task-more">Ещё {groupedTasks.length - visibleGroupedTasks.length}</span> : null}
                              </div>
                            ) : null}
                          </article>
                        );
                      }) : <p>Перетащите сюда проекты, которые идут одновременно</p>}
                    </div>
                  ) : (
                    <>
                  <Link to={path} className="roadmap-node-title">
                    {title}
                  </Link>
                  <span className="roadmap-node-meta">{nodeMeta(node, projects, tasks, ideas)}</span>
                  {node.kind === "project" || node.kind === "task" ? <span className="roadmap-node-due">{dueLabel}</span> : null}
                  {node.kind === "project" ? (
                    <>
                      <button type="button" className="roadmap-project-tasks-toggle" onClick={() => toggleProjectTasks(node.id)} aria-expanded={isProjectExpanded}>
                        {isProjectExpanded ? "Свернуть задачи" : "Раскрыть задачи"}
                        <span>{relatedTasks.length}</span>
                      </button>
                      <Link to={`/projects/${node.entityId}/roadmap`} className="roadmap-project-open-map">
                        Открыть дорожную карту
                      </Link>
                      {isProjectExpanded ? (
                        <div className="roadmap-project-task-list">
                          {visibleTasks.length > 0 ? visibleTasks.map((task) => (
                            <Link key={task.id} to={`/tasks/${task.id}`} className={`roadmap-project-task status-${task.status}`}>
                              <span aria-hidden />
                              <span>{task.title}</span>
                              <time>{task.due_date ? formatDateTime(task.due_date) : "Без срока"}</time>
                            </Link>
                          )) : <span className="roadmap-project-task-empty">Задач пока нет</span>}
                          {relatedTasks.length > visibleTasks.length ? <span className="roadmap-project-task-more">Ещё {relatedTasks.length - visibleTasks.length}</span> : null}
                        </div>
                      ) : null}
                    </>
                  ) : null}
                    </>
                  )}
                  {ROADMAP_PORTS.map((side) => (
                    <button
                      key={side}
                      type="button"
                      className={`roadmap-node-port is-${side}${connectionTarget?.nodeId === node.id && connectionTarget.side === side ? " is-target" : ""}`}
                      data-roadmap-port
                      data-node-id={node.id}
                      data-port-side={side}
                      aria-label={`Создать связь: ${side}`}
                      title="Потяните к порту другой карточки"
                      onPointerDown={(event) => startConnection(event, node.id, side)}
                    />
                  ))}
                </article>
              );
            })}

            {nodes.length === 0 ? <span className="roadmap-empty-canvas" aria-live="polite">{taskProjectId ? "Перетащите задачу на поле" : "Перетащите проект на поле"}</span> : null}
          </div>
        </div>
        {nodes.length > 0 && zoom <= 100 ? (
          <button type="button" className="roadmap-minimap" aria-label="Миникарта дорожной карты" onClick={moveFromMinimap}>
            <span className="roadmap-minimap-grid" aria-hidden />
            {nodes.map((node) => (
              <span
                key={node.id}
                className={`roadmap-minimap-node is-${node.kind}`}
                style={{ left: `${node.x / ROADMAP_CANVAS_WIDTH * 100}%`, top: `${node.y / ROADMAP_CANVAS_HEIGHT * 100}%` }}
                aria-hidden
              />
            ))}
            <span
              className="roadmap-minimap-viewport"
              style={{
                left: `${viewportFrame.left / ROADMAP_CANVAS_WIDTH * 100}%`,
                top: `${viewportFrame.top / ROADMAP_CANVAS_HEIGHT * 100}%`,
                width: `${viewportFrame.width / ROADMAP_CANVAS_WIDTH * 100}%`,
                height: `${viewportFrame.height / ROADMAP_CANVAS_HEIGHT * 100}%`,
              }}
              aria-hidden
            />
          </button>
        ) : null}
      </div>
    </section>
  );
}

function ClientRoadmap({
  projects,
  ideas,
  onOpenBacklog,
}: {
  projects: RoadmapProject[];
  ideas: BacklogItem[];
  onOpenBacklog: () => void;
}) {
  const lanes: { id: RoadmapLane; title: string; hint: string }[] = [
    { id: "now", title: "Сейчас", hint: "В работе и ближайшие шаги" },
    { id: "next", title: "Далее", hint: "Следующая очередь" },
  ];

  return (
    <section className="roadmap-section" aria-label="Дорожная карта развития">
      <div className="roadmap-intro">
        <div>
          <p className="roadmap-eyebrow">Единый план</p>
          <h2>Дорожная карта развития</h2>
          <p>Проекты и задачи собраны в один понятный путь.</p>
        </div>
        <div className="roadmap-intro-stats" aria-label="Статистика плана">
          <span><strong>{projects.length}</strong> проектов</span>
          <span><strong>{projects.reduce((sum, item) => sum + item.tasks.length, 0)}</strong> задач впереди</span>
        </div>
      </div>

      <div className="roadmap-board" role="list">
        {lanes.map((lane, index) => {
          const laneProjects = projects.filter((item) => item.lane === lane.id);
          return (
            <Fragment key={lane.id}>
              <section className={`roadmap-column is-${lane.id}`} role="listitem">
                <header className="roadmap-column-head">
                  <span className="roadmap-column-index">0{index + 1}</span>
                  <div>
                    <h3>{lane.title}</h3>
                    <p>{lane.hint}</p>
                  </div>
                  <span className="roadmap-column-count">{laneProjects.length}</span>
                </header>
                <div className="roadmap-column-body">
                  {laneProjects.length > 0 ? (
                    laneProjects.map((item) => <RoadmapProjectCard key={item.project.id} item={item} />)
                  ) : (
                    <p className="roadmap-empty">Пока нет запланированных работ.</p>
                  )}
                </div>
              </section>
              <div className="roadmap-connector" aria-hidden>
                <span>Следующий этап</span>
              </div>
            </Fragment>
          );
        })}

        <section className="roadmap-column is-ideas" role="listitem">
          <header className="roadmap-column-head">
            <span className="roadmap-column-index">03</span>
            <div>
              <h3>Идеи</h3>
              <p>На оценке и в резерве</p>
            </div>
            <span className="roadmap-column-count">{ideas.length}</span>
          </header>
          <div className="roadmap-column-body">
            {ideas.slice(0, 4).map((idea) => (
              <Link key={idea.id} to={`/requests/${idea.id}`} className="roadmap-idea-card">
                <span>{idea.status === "deferred" ? "Позже" : "На оценке"}</span>
                <strong>{idea.title}</strong>
                {idea.notes ? <p>{idea.notes}</p> : null}
              </Link>
            ))}
            {ideas.length === 0 ? <p className="roadmap-empty">Новых инициатив пока нет.</p> : null}
            <button type="button" className="roadmap-add-idea" onClick={onOpenBacklog}>
              <span aria-hidden>+</span>
              Добавить инициативу
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}

void ClientRoadmap;

export function ProjectRoadmapPage() {
  const { projectId: rawProjectId } = useParams();
  const projectId = Number(rawProjectId);
  const { token, portal } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !Number.isFinite(projectId) || projectId <= 0) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    Promise.all([
      api<Project>(`/api/projects/${projectId}/`, { signal: controller.signal }, token),
      api<Paginated<Task> | Task[]>(
        `/api/tasks/?project=${projectId}&page=1&page_size=200`,
        { signal: controller.signal },
        token
      ),
    ])
      .then(([projectData, taskData]) => {
        setProject(projectData);
        setTasks(unwrapList(taskData).filter((task) => task.project === projectId));
      })
      .catch((err) => {
        if (!isAbortError(err)) setError(err instanceof Error ? err.message : "Не удалось открыть дорожную карту");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token, projectId]);

  return (
    <div className="tasks-page request-page is-roadmap-view project-roadmap-page">
      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? (
        <div className="empty-linked data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">{project?.name ? `Собираем карту «${project.name}»…` : "Собираем дорожную карту…"}</p>
        </div>
      ) : (
        <RoadmapBuilder
          portalId={portal?.id ?? project?.portal ?? null}
          projects={project ? [project] : []}
          tasks={tasks}
          ideas={[]}
          taskProjectId={projectId}
        />
      )}
    </div>
  );
}

export function ClientTaskRequests() {
  const { token, portal } = useAuth();
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();
  const portalId = portal?.id ?? null;

  const [items, setItems] = useState<BacklogItem[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [edit, setEdit] = useState<DraftEdit | null>(null);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<BacklogView>("roadmap");
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragOverStage, setDragOverStage] = useState<RequestStage | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !portalId) return;
      const [data, taskData, projectData] = await Promise.all([
        api<BacklogItem[]>(`/api/backlog-items/?portal=${portalId}`, { signal }, token),
        api<Paginated<Task> | Task[]>(
          `/api/tasks/?portal=${portalId}&page=1&page_size=200`,
          { signal },
          token
        ).catch((e) => {
          if (isAbortError(e)) throw e;
          return [] as Task[];
        }),
        api<Paginated<Project> | Project[]>(
          `/api/projects/?portal=${portalId}&page=1&page_size=200`,
          { signal },
          token
        ).catch((e) => {
          if (isAbortError(e)) throw e;
          return [] as Project[];
        }),
      ]);
      if (signal?.aborted) return;
      setItems(Array.isArray(data) ? data : []);
      setTasks(unwrapList(taskData));
      setProjects(unwrapList(projectData));
    },
    [token, portalId]
  );

  useEffect(() => {
    if (!token || !portalId || isAgency) return;
    setLoading(true);
    setError(null);
    const ac = new AbortController();
    void load(ac.signal)
      .catch((e) => {
        if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [token, portalId, isAgency, load]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: Boolean(token && portalId && !isAgency),
    onEvent: () => {
      void load().catch(() => undefined);
    },
  });

  const taskById = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
  const clientItems = useMemo(() => items.filter((item) => item.source === "client"), [items]);
  const itemsByStage = useMemo(() => {
    const columns: Record<RequestStage, BacklogItem[]> = {
      new: [],
      planned: [],
      deferred: [],
      handed_over: [],
    };
    for (const item of clientItems) {
      const task = item.converted_task ? taskById.get(item.converted_task) : null;
      const stage: RequestStage = item.converted_task || task
        ? "handed_over"
        : item.status === "deferred"
          ? "deferred"
          : item.status === "in_progress"
            ? "planned"
            : "new";
      columns[stage].push(item);
    }
    return columns;
  }, [clientItems, taskById]);

  const roadmapProjects = useMemo(() => {
    const open = projects
      .filter((project) => project.is_active)
      .map((project) => ({
        project,
        tasks: tasks
          .filter((task) => task.project === project.id && task.status !== "done")
          .sort((a, b) => {
            if (a.status === "in_progress" && b.status !== "in_progress") return -1;
            if (b.status === "in_progress" && a.status !== "in_progress") return 1;
            return String(a.due_date || "9999").localeCompare(String(b.due_date || "9999"));
          }),
      }))
      .sort((a, b) => {
        const aDue = a.tasks[0]?.due_date || a.project.due_date || "9999";
        const bDue = b.tasks[0]?.due_date || b.project.due_date || "9999";
        return String(aDue).localeCompare(String(bDue));
      });

    const inProgress = open.filter((item) => item.tasks.some((task) => task.status === "in_progress"));
    const nowIds = new Set((inProgress.length > 0 ? inProgress : open.slice(0, 1)).slice(0, 2).map((item) => item.project.id));
    return open.map((item): RoadmapProject => ({
      ...item,
      lane: nowIds.has(item.project.id) ? "now" : "next",
    }));
  }, [projects, tasks]);

  void roadmapProjects;

  const roadmapIdeas = useMemo(
    () => clientItems.filter((item) => !item.converted_task && item.status !== "converted"),
    [clientItems]
  );

  if (isAgency) {
    return <Navigate to="/" replace />;
  }

  async function createRequest() {
    if (!token || !portalId) return;
    const nextTitle = title.trim();
    if (!nextTitle) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api<BacklogItem>(
        "/api/backlog-items/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            title: nextTitle,
            notes: notes.trim(),
          }),
        },
        token
      );
      setItems((prev) => [created, ...prev]);
      setTitle("");
      setNotes("");
      setShowCreate(false);
      toast.show("Агентство увидит её в бэклоге", "Заявка отправлена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить заявку");
    } finally {
      setCreating(false);
    }
  }

  async function confirmDelete() {
    if (!token || !pendingDelete) return;
    setDeleting(true);
    setError(null);
    try {
      await api(`/api/backlog-items/${pendingDelete.id}/`, { method: "DELETE" }, token);
      setItems((prev) => prev.filter((item) => item.id !== pendingDelete.id));
      setPendingDelete(null);
      toast.show("Заявка удалена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    } finally {
      setDeleting(false);
    }
  }

  function startEdit(item: BacklogItem) {
    setEdit({ id: item.id, title: item.title, notes: item.notes || "" });
  }

  function statusForStage(stage: RequestStage): BacklogStatus | null {
    if (stage === "new") return "idea";
    if (stage === "planned") return "in_progress";
    if (stage === "deferred") return "deferred";
    return null;
  }

  async function dropOnStage(stage: RequestStage) {
    const movingId = dragId;
    setDragId(null);
    setDragOverStage(null);
    if (!token || movingId == null) return;
    const nextStatus = statusForStage(stage);
    const moving = items.find((item) => item.id === movingId);
    if (!nextStatus || !moving || moving.converted_task || moving.status === "converted") return;
    if (itemsByStage[stage].some((item) => item.id === movingId)) return;
    const previous = moving.status;
    setItems((current) => current.map((item) => (item.id === movingId ? { ...item, status: nextStatus } : item)));
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${movingId}/`,
        { method: "PATCH", body: JSON.stringify({ status: nextStatus }) },
        token
      );
      setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      setItems((current) => current.map((item) => (item.id === movingId ? { ...item, status: previous } : item)));
      setError(err instanceof Error ? err.message : "Не удалось перенести заявку");
    }
  }

  async function saveEdit() {
    if (!token || !edit) return;
    const nextTitle = edit.title.trim();
    if (!nextTitle) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${edit.id}/`,
        {
          method: "PATCH",
          body: JSON.stringify({ title: nextTitle, notes: edit.notes.trim() }),
        },
        token
      );
      setItems((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setEdit(null);
      toast.show("Агентство увидит обновлённый текст", "Заявка сохранена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={`tasks-page request-page${view === "roadmap" ? " is-roadmap-view" : ""}`}>
      <div className="request-view-switch roadmap-view-switch" role="group" aria-label="Режим просмотра">
        <button type="button" className={view === "roadmap" ? "is-active" : ""} onClick={() => setView("roadmap")} aria-pressed={view === "roadmap"}>
          Дорожная карта
        </button>
        <button type="button" className={view === "backlog" ? "is-active" : ""} onClick={() => setView("backlog")} aria-pressed={view === "backlog"}>
          Беклог
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div key={view} className={`request-view-body${view === "roadmap" ? " is-roadmap" : ""}`}>
      {view === "roadmap" ? (
        <RoadmapBuilder portalId={portalId} projects={projects} tasks={tasks} ideas={roadmapIdeas} />
      ) : (
      <section className="request-kanban-section" aria-label="Статусы заявок">
        {loading && items.length === 0 ? (
          <div className="empty-linked workspace-empty data-loading-state">
            <span className="data-loading-spinner" aria-hidden />
            <p className="muted">Загружаем заявки…</p>
          </div>
        ) : (
          <div className="backlog-funnel request-kanban">
            {REQUEST_STAGES.map((stage, stageIndex) => {
              const stageItems = itemsByStage[stage.id];
              return (
                <section
                  key={stage.id}
                  className={`backlog-funnel-col request-kanban-col is-${stage.id}${dragOverStage === stage.id ? " is-drop" : ""}`}
                  onDragOver={(event) => {
                    if (!statusForStage(stage.id) || dragId == null) return;
                    event.preventDefault();
                    setDragOverStage(stage.id);
                  }}
                  onDragLeave={() => setDragOverStage((current) => (current === stage.id ? null : current))}
                  onDrop={(event) => {
                    event.preventDefault();
                    void dropOnStage(stage.id);
                  }}
                >
                  <header className="backlog-funnel-head">
                    <div className="backlog-funnel-step">
                      <span className="backlog-funnel-num">{stageIndex + 1}</span>
                      <div>
                        <h3 className="backlog-funnel-title">{stage.label}</h3>
                      </div>
                    </div>
                    <span className="backlog-funnel-count">{stageItems.length}</span>
                  </header>
                  <div className="backlog-funnel-cards">
                    {stage.id === "new" ? (
                      showCreate ? (
                        <form
                          className="backlog-card request-kanban-create stack"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void createRequest();
                          }}
                        >
                          <div>
                            <strong>Новая заявка</strong>
                            <p className="muted">Коротко опишите задачу для агентства.</p>
                          </div>
                          <div className="field">
                            <label>Название</label>
                            <input
                              value={title}
                              onChange={(event) => setTitle(event.target.value)}
                              placeholder="Например, Сверстать главную"
                              required
                              autoFocus
                            />
                          </div>
                          <div className="field">
                            <label>Описание</label>
                            <textarea
                              value={notes}
                              onChange={(event) => setNotes(event.target.value)}
                              rows={4}
                              placeholder="Контекст, ссылки, что уже есть"
                            />
                          </div>
                          <div className="request-card-actions">
                            <button
                              type="submit"
                              className="btn btn-primary"
                              disabled={creating || !title.trim()}
                            >
                              {creating ? "Отправляем…" : "Отправить"}
                            </button>
                            <button
                              type="button"
                              className="btn btn-ghost"
                              disabled={creating}
                              onClick={() => setShowCreate(false)}
                            >
                              Отмена
                            </button>
                          </div>
                        </form>
                      ) : (
                        <button
                          type="button"
                          className="request-kanban-add"
                          onClick={() => setShowCreate(true)}
                        >
                          <span aria-hidden>+</span>
                          Добавить заявку
                        </button>
                      )
                    ) : null}
                    {stageItems.length === 0 ? (
                      <p className="backlog-funnel-empty muted">Пока пусто</p>
                    ) : stageItems.map((item) => {
                      const isEditing = edit?.id === item.id;
                      const isAwaiting = isPending(item);
                      const task = item.converted_task ? taskById.get(item.converted_task) : null;
                      return (
                      <article
                        key={item.id}
                        className={`backlog-card request-kanban-card${isEditing ? " is-editing" : ""}${dragId === item.id ? " is-dragging" : ""}`}
                        draggable={!isEditing && !item.converted_task && item.status !== "converted"}
                        onDragStart={(event) => {
                          if (event.target instanceof Element && event.target.closest("button")) {
                            event.preventDefault();
                            return;
                          }
                          event.dataTransfer.effectAllowed = "move";
                          setDragId(item.id);
                        }}
                        onDragEnd={() => {
                          setDragId(null);
                          setDragOverStage(null);
                        }}
                      >
                {isEditing && edit ? (
                  <form
                    className="request-edit stack"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveEdit();
                    }}
                  >
                    <div className="field">
                      <label>Название</label>
                      <input
                        value={edit.title}
                        onChange={(e) =>
                          setEdit((cur) => (cur ? { ...cur, title: e.target.value } : cur))
                        }
                        required
                        autoFocus
                      />
                    </div>
                    <div className="field">
                      <label>Описание</label>
                      <textarea
                        value={edit.notes}
                        onChange={(e) =>
                          setEdit((cur) => (cur ? { ...cur, notes: e.target.value } : cur))
                        }
                        rows={4}
                      />
                    </div>
                    <div className="request-card-actions">
                      <button
                        type="submit"
                        className="btn btn-primary"
                        disabled={saving || !edit.title.trim()}
                      >
                        {saving ? "Сохраняем…" : "Сохранить"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={saving}
                        onClick={() => setEdit(null)}
                      >
                        Отмена
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                <Link to={`/requests/${item.id}`} className="request-card-copy request-card-open" draggable={false}>
                  <strong>{item.title}</strong>
                  {item.notes ? <p>{item.notes}</p> : null}
                </Link>
                <div className="request-card-foot">
                  <span className="muted">{formatDateTime(item.created_at)}</span>
                  <div className="request-card-actions">
                    {isAwaiting && item.can_edit ? (
                      <button type="button" className="request-text-action" onClick={() => startEdit(item)}>
                        Изменить
                      </button>
                    ) : null}
                    {isAwaiting && item.can_delete ? (
                      <button
                        type="button"
                        className="request-text-action is-danger"
                        onClick={() => setPendingDelete({ id: item.id, title: item.title })}
                      >
                        Удалить
                      </button>
                    ) : null}
                    {!isAwaiting && item.converted_task ? (
                      <Link to={`/tasks/${item.converted_task}`} state={fromState} className="request-text-action">
                        {task?.status === "done" ? "Открыть результат" : "Открыть задачу"}
                      </Link>
                    ) : null}
                  </div>
                </div>
                  </>
                )}
                      </article>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </section>
      )}
      </div>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        danger
        title={pendingDelete ? `Удалить «${pendingDelete.title}»?` : "Удалить заявку?"}
        description="Заявку можно удалить только пока агентство не приняло её в работу."
        confirmLabel={deleting ? "Удаляем…" : "Удалить"}
        cancelLabel="Оставить"
        onCancel={() => {
          if (!deleting) setPendingDelete(null);
        }}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
