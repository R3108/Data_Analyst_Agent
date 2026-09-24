"use client";

import {
  Bell,
  CalendarClock,
  CircleAlert,
  CloudUpload,
  Compass,
  Database,
  Download,
  FileDown,
  FileSpreadsheet,
  FileText,
  FlaskConical,
  LayoutDashboard,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  Notebook,
  Plus,
  Presentation,
  Printer,
  RefreshCw,
  Search,
  Share2,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Split,
  Sun,
  TrendingUp,
  TriangleAlert,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BoardView } from "@/components/boards/board-view";
import { BriefingsView } from "@/components/briefings-view";
import { ChatView, type PendingTurn } from "@/components/chat/chat-view";
import { CohortsView } from "@/components/cohorts-view";
import { CommandPalette, type PaletteCommand } from "@/components/command-palette";
import { DataPanel, type DataTab } from "@/components/data-panel";
import { DriversView } from "@/components/drivers-view";
import { ExportMenu, type ExportOption } from "@/components/export-menu";
import { ForecastView } from "@/components/forecast-view";
import { MonitorsView } from "@/components/monitors-view";
import { PinProvider, type PinTarget } from "@/components/pin";
import { ScenarioView } from "@/components/scenario-view";
import { ShareDialog, type ShareTarget } from "@/components/share-dialog";
import { Sidebar } from "@/components/sidebar";
import { SignificanceView } from "@/components/significance-view";
import { SourcesView } from "@/components/sources-view";
import { useConfirm } from "@/components/ui/confirm";
import { Button, IconButton } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { WatchProvider } from "@/components/watch";
import { Welcome } from "@/components/welcome";
import { ApiError, api, streamChat, streamInvestigation } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useModKey } from "@/lib/platform";
import { useSession } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import type {
  BoardDetail,
  BoardSummary,
  Dataset,
  DatasetSummary,
  DriverQuery,
  Health,
  Message,
  MonitorDigest,
  Semantics,
  SessionDetail,
  SessionSummary,
  Step,
  UsageReport,
} from "@/lib/types";

/** The full-width views that replace the chat surface. */
export type WorkspaceView =
  | "monitors"
  | "drivers"
  | "significance"
  | "scenarios"
  | "cohorts"
  | "forecast"
  | "sources"
  | "briefings";

const DATASET_VIEWS: WorkspaceView[] = [
  "drivers",
  "significance",
  "scenarios",
  "cohorts",
  "forecast",
];

const VIEW_TITLES: Record<WorkspaceView, string> = {
  monitors: "Monitors",
  drivers: "Drivers",
  significance: "Significance",
  scenarios: "Scenarios",
  cohorts: "Retention",
  forecast: "Forecast",
  sources: "Sources",
  briefings: "Briefings",
};

function upsertStep(steps: Step[], step: Step): Step[] {
  const index = steps.findIndex((s) => s.id === step.id);
  if (index === -1) return [...steps, step];
  const next = steps.slice();
  next[index] = { ...next[index], ...step };
  return next;
}

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unexpected error. Please try again.";
}

function setUrl(
  params: { session?: string; board?: string; view?: WorkspaceView; dataset?: string } | null,
) {
  const query = params?.session
    ? `?session=${params.session}`
    : params?.board
      ? `?board=${params.board}`
      : params?.view
        ? `?view=${params.view}${params.dataset ? `&dataset=${params.dataset}` : ""}`
        : "";
  window.history.replaceState(null, "", query || window.location.pathname);
}

function download(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

const slugify = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "analysis";

export function Workspace() {
  const toast = useToast();
  const confirm = useConfirm();
  const mod = useModKey();
  const { setMode } = useTheme();
  // The signed-in account. `RequireSession` above has already guaranteed there is one;
  // every request below is authorised again by the server regardless.
  const { user, isAdmin, signOut } = useSession();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [board, setBoard] = useState<BoardDetail | null>(null);
  // One slot for every full-width view, so they cannot be open at the same time.
  const [view, setView] = useState<
    { kind: WorkspaceView; datasetId: string | null; query: DriverQuery } | null
  >(null);
  const [queuedQuestion, setQueuedQuestion] = useState<string | null>(null);
  const [digest, setDigest] = useState<MonitorDigest | null>(null);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [uploading, setUploading] = useState<"file" | "sample" | null>(null);
  const [loadingSession, setLoadingSession] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [dataOpen, setDataOpen] = useState(false);
  const [dataTab, setDataTab] = useState<DataTab>("overview");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shareTarget, setShareTarget] = useState<ShareTarget | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const datasetCache = useRef(new Map<string, Dataset>());

  const refreshDigest = useCallback(async () => {
    try {
      setDigest(await api.monitorDigest());
    } catch {
      /* monitors are optional context, not worth a banner */
    }
  }, []);

  const refreshLists = useCallback(async () => {
    try {
      const [nextDatasets, nextSessions, nextBoards] = await Promise.all([
        api.listDatasets(),
        api.listSessions(),
        api.listBoards(),
      ]);
      setDatasets(nextDatasets);
      setSessions(nextSessions);
      setBoards(nextBoards);
      setUsage(await api.usage(30));
      void refreshDigest();
    } catch {
      /* connectivity problems are surfaced by the health banner */
    }
  }, [refreshDigest]);

  const loadDataset = useCallback(async (id: string) => {
    const cached = datasetCache.current.get(id);
    if (cached) return cached;
    const fetched = await api.getDataset(id);
    datasetCache.current.set(id, fetched);
    return fetched;
  }, []);

  const leaveCurrentView = () => {
    abortRef.current?.abort();
    setSidebarOpen(false);
    setBoardId(null);
    setBoard(null);
    setView(null);
  };

  const openSession = useCallback(
    async (id: string) => {
      leaveCurrentView();
      setLoadingSession(true);
      try {
        const detail = await api.getSession(id);
        const detailDataset = await loadDataset(detail.dataset_id);
        setSession(detail);
        setDataset(detailDataset);
        setUrl({ session: id });
      } catch (error) {
        toast.error("Couldn't open analysis", describe(error));
        setUrl(null);
      } finally {
        setLoadingSession(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [loadDataset, toast],
  );

  const openBoard = useCallback((id: string) => {
    abortRef.current?.abort();
    setSidebarOpen(false);
    setView(null);
    setSession(null);
    setDataset(null);
    setBoard(null);
    setBoardId(id);
    setUrl({ board: id });
  }, []);

  /**
   * Open a full-width view. The dataset-scoped ones (drivers, significance, scenarios)
   * can be pre-aimed by a signal or a chart at a particular measure and dimension.
   */
  const openView = useCallback(
    (kind: WorkspaceView, datasetId: string | null = null, query: DriverQuery = {}) => {
      abortRef.current?.abort();
      setSidebarOpen(false);
      setBoardId(null);
      setBoard(null);
      setSession(null);
      setDataset(null);
      setView({ kind, datasetId, query });
      setUrl({ view: kind, dataset: datasetId ?? undefined });
    },
    [],
  );

  const startSession = useCallback(
    async (datasetId: string) => {
      leaveCurrentView();
      try {
        const [detail, detailDataset] = await Promise.all([api.createSession(datasetId), loadDataset(datasetId)]);
        setSession(detail);
        setDataset(detailDataset);
        setDataTab("overview");
        setUrl({ session: detail.id });
        void refreshLists();
      } catch (error) {
        toast.error("Couldn't start an analysis", describe(error));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [loadDataset, refreshLists, toast],
  );

  const checkHealth = useCallback(() => {
    api
      .health()
      .then((next) => {
        setHealth(next);
        setHealthError(null);
        void refreshLists();
      })
      .catch((error) => setHealthError(describe(error)));
  }, [refreshLists]);

  useEffect(() => {
    checkHealth();
    const params = new URLSearchParams(window.location.search);
    const initialSession = params.get("session");
    const initialBoard = params.get("board");
    const initialView = params.get("view");
    if (initialSession) void openSession(initialSession);
    else if (initialBoard) openBoard(initialBoard);
    else if (initialView && initialView in VIEW_TITLES) {
      openView(initialView as WorkspaceView, params.get("dataset"));
    }
    if (window.matchMedia("(min-width: 1280px)").matches) setDataOpen(true);

    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ingest = async (kind: "file" | "sample", run: () => Promise<Dataset>) => {
    setUploading(kind);
    try {
      const created = await run();
      datasetCache.current.set(created.id, created);
      const signals = created.profile.signals?.length ?? 0;
      toast.success(
        `${created.name} is ready`,
        `${created.n_rows.toLocaleString()} rows cleaned${signals ? ` · ${signals} signals spotted` : ""}.`,
      );
      await startSession(created.id);
    } catch (error) {
      toast.error(kind === "file" ? "Upload failed" : "Couldn't load the sample", describe(error));
    } finally {
      setUploading(null);
    }
  };

  const resync = (sessionId: string) =>
    api
      .getSession(sessionId)
      .then((detail) => setSession((current) => (current?.id === sessionId ? detail : current)))
      .catch(() => undefined);

  /**
   * Consume one SSE run — a chat turn or an investigation.
   *
   * An investigation emits an `assistant_message` per sub-analysis before the final
   * brief, which is exactly what a chat turn does once; the only difference here is that
   * the pending indicator survives until the stream closes rather than until the first
   * message lands.
   */
  const consume = async (
    run: (onEvent: (event: Parameters<Parameters<typeof streamChat>[2]>[0]) => void, signal: AbortSignal) => Promise<void>,
    question: string,
    investigating: boolean,
  ) => {
    if (!session || pending) return;
    const sessionId = session.id;
    const controller = new AbortController();
    abortRef.current = controller;
    setPending({ question, steps: [], investigating });

    const append = (message: Message) =>
      setSession((current) =>
        current?.id === sessionId ? { ...current, messages: [...current.messages, message] } : current,
      );

    try {
      await run((event) => {
        switch (event.event) {
          case "session":
            setSession((current) => (current?.id === sessionId ? { ...current, title: event.data.title } : current));
            break;
          case "user_message":
            append(event.data);
            setPending((current) => current && { ...current, userMessageId: event.data.id });
            break;
          case "recall":
            setPending((current) => current && { ...current, recall: event.data });
            break;
          case "step":
            setPending((current) => current && { ...current, steps: upsertStep(current.steps, event.data) });
            break;
          case "assistant_message":
            append(event.data);
            // An investigation keeps working after a sub-analysis lands; a chat turn does
            // not, so only the latter is finished by its first message.
            if (!investigating) setPending(null);
            break;
          case "error":
            if (event.data.message_record) append(event.data.message_record);
            setPending(null);
            break;
        }
      }, controller.signal);
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        window.setTimeout(() => void resync(sessionId), 500);
      } else {
        toast.error(investigating ? "Investigation failed" : "Analysis failed", describe(error));
        void resync(sessionId);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setPending(null);
      void refreshLists();
    }
  };

  const send = (text: string) =>
    consume((onEvent, signal) => streamChat(session!.id, text, onEvent, signal), text, false);

  /** Deep research: scope an objective, run several analyses, synthesise one brief. */
  const investigate = (objective: string | null) =>
    consume(
      (onEvent, signal) => streamInvestigation(session!.id, objective, onEvent, signal),
      objective ?? "Give me a complete picture of this dataset.",
      true,
    );

  const newAnalysis = () => {
    leaveCurrentView();
    setSession(null);
    setDataset(null);
    setUrl(null);
  };

  /** Hand a finding to the agent: open a session on that dataset and ask. */
  const askWithDataset = async (datasetId: string, question: string) => {
    await startSession(datasetId);
    setQueuedQuestion(question);
  };

  const activeDatasetId = () => session?.dataset_id ?? view?.datasetId ?? datasets[0]?.id ?? null;

  // `send` reads the open session from its closure, so the question waits for the state to land.
  useEffect(() => {
    if (!queuedQuestion || !session || pending) return;
    const question = queuedQuestion;
    setQueuedQuestion(null);
    void send(question);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queuedQuestion, session?.id, pending]);

  const createBoard = async (title = "Untitled board") => {
    try {
      const created = await api.createBoard(title);
      await refreshLists();
      openBoard(created.id);
    } catch (error) {
      toast.error("Couldn't create board", describe(error));
    }
  };

  const pin = useCallback(
    async (target: PinTarget, targetBoardId: string | null, newBoardTitle?: string) => {
      try {
        let id = targetBoardId;
        let title = boards.find((b) => b.id === targetBoardId)?.title ?? "board";
        if (!id) {
          const created = await api.createBoard(newBoardTitle ?? "Key metrics");
          id = created.id;
          title = created.title;
        }
        // `datasetId` is what separates the two provenances; narrowing on it rather than
        // on the optional `source` keeps the union discriminated.
        if ("datasetId" in target) {
          // Addressed by provenance: the server recomputes before it snapshots.
          await api.pinComputed(id, {
            kind: target.kind,
            source: target.source,
            dataset_id: target.datasetId,
            index: target.index,
            params: target.params,
          });
        } else {
          await api.pinToBoard(id, { kind: target.kind, message_id: target.messageId, index: target.index });
        }
        const destination = id;
        toast.success(`Pinned to ${title}`, target.label, { label: "View board", onClick: () => openBoard(destination) });
        void refreshLists();
      } catch (error) {
        toast.error("Couldn't pin", describe(error));
      }
    },
    [boards, openBoard, refreshLists, toast],
  );

  const confirmDelete = async (
    prompt: { title: string; body: string; confirmLabel: string },
    action: () => Promise<void>,
    failure: string,
  ) => {
    if (!(await confirm(prompt))) return;
    try {
      await action();
      void refreshLists();
    } catch (error) {
      toast.error(failure, describe(error));
    }
  };

  const deleteSession = (id: string) => {
    const title = sessions.find((s) => s.id === id)?.title;
    return confirmDelete(
      {
        title: title ? `Delete “${title}”?` : "Delete this analysis?",
        body: "The conversation, its charts and any share link are removed. This cannot be undone.",
        confirmLabel: "Delete analysis",
      },
      async () => {
        await api.deleteSession(id);
        if (session?.id === id) newAnalysis();
      },
      "Couldn't delete analysis",
    );
  };

  const deleteBoard = (id: string) => {
    const title = boards.find((b) => b.id === id)?.title;
    return confirmDelete(
      {
        title: title ? `Delete “${title}”?` : "Delete this board?",
        body: "Everything pinned to it is removed and its share link stops working. This cannot be undone.",
        confirmLabel: "Delete board",
      },
      async () => {
        await api.deleteBoard(id);
        if (boardId === id) newAnalysis();
      },
      "Couldn't delete board",
    );
  };

  const deleteDataset = (id: string) => {
    const name = datasets.find((d) => d.id === id)?.name;
    return confirmDelete(
      {
        title: name ? `Delete “${name}”?` : "Delete this dataset?",
        body: "Every analysis that uses this dataset is deleted with it. This cannot be undone.",
        confirmLabel: "Delete dataset",
      },
      async () => {
        await api.deleteDataset(id);
        datasetCache.current.delete(id);
        if (session?.dataset_id === id) newAnalysis();
      },
      "Couldn't delete dataset",
    );
  };

  const exportMarkdown = async () => {
    if (!session) return;
    download(`${slugify(session.title)}.md`, await api.exportSession(session.id), "text/markdown;charset=utf-8");
    toast.success("Report exported", "Saved as a Markdown file.");
  };

  /** File the chosen file as the next version of the open dataset. */
  const uploadVersion = async (file: File) => {
    if (!dataset) return;
    const previous = dataset;
    setUploading("file");
    try {
      const created = await api.uploadDataset(file, undefined, previous.id);
      datasetCache.current.set(created.id, created);
      toast.success(
        `${created.name} v${created.version ?? 2} is ready`,
        created.version_diff?.headline ?? `${created.n_rows.toLocaleString()} rows cleaned.`,
      );
      await startSession(created.id);
      setDataTab("overview");
      setDataOpen(true);
      // Monitors on this lineage are re-checked server-side as the upload lands.
      window.setTimeout(() => void refreshDigest(), 600);
    } catch (error) {
      toast.error("Couldn't upload the new version", describe(error));
    } finally {
      setUploading(null);
    }
  };

  const openPrint = () => {
    const query = session ? `session=${session.id}` : board ? `board=${board.id}` : null;
    if (query) window.open(`/report?${query}&print=1`, "_blank", "noopener");
  };

  const openShare = () => {
    if (session) setShareTarget({ kind: "session", id: session.id, title: session.title, token: session.share_token ?? null });
    else if (board) setShareTarget({ kind: "board", id: board.id, title: board.title, token: board.share_token });
  };

  // Declared after openPrint on purpose: this memo runs during the first render, so a
  // reference to a `const` below it would hit the temporal dead zone.
  const exportOptions = useMemo<ExportOption[]>(() => {
    if (session && session.messages.length > 0) {
      const id = session.id;
      return [
        { id: "md", label: "Markdown report", hint: "Portable plain text",
          icon: <FileText className="size-4" />, run: exportMarkdown },
        { id: "ipynb", label: "Jupyter notebook", hint: "Runnable code and helpers",
          icon: <Notebook className="size-4" />, run: () => api.downloadSessionNotebook(id) },
        { id: "pdf", label: "PDF document", hint: "Formatted, with charts",
          icon: <FileDown className="size-4" />, run: () => api.downloadSessionPdf(id) },
        { id: "pptx", label: "PowerPoint deck", hint: "Editable native charts",
          icon: <Presentation className="size-4" />, run: () => api.downloadSessionPptx(id) },
        { id: "print", label: "Print view", hint: "Browser print or PDF",
          icon: <Printer className="size-4" />, run: () => openPrint() },
      ];
    }
    if (board) {
      const id = board.id;
      return [
        { id: "pdf", label: "PDF document", hint: "Formatted dashboard",
          icon: <FileDown className="size-4" />, run: () => api.downloadBoardPdf(id) },
        { id: "pptx", label: "PowerPoint deck", hint: "Editable native charts",
          icon: <Presentation className="size-4" />, run: () => api.downloadBoardPptx(id) },
        { id: "print", label: "Print view", hint: "Browser print or PDF",
          icon: <Printer className="size-4" />, run: () => openPrint() },
      ];
    }
    return [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, board]);

  const commands = useMemo<PaletteCommand[]>(() => {
    const list: PaletteCommand[] = [
      { id: "new", group: "Actions", label: "New analysis", icon: <Plus className="size-4" />, run: newAnalysis },
      { id: "sample", group: "Actions", label: "Load sample retail dataset", icon: <Sparkles className="size-4" />,
        keywords: "demo example", run: () => void ingest("sample", api.loadSample) },
      { id: "board", group: "Actions", label: "Create a new board", icon: <LayoutDashboard className="size-4" />,
        keywords: "dashboard", run: () => void createBoard() },
      { id: "monitors", group: "Actions", label: "Open monitors", icon: <Bell className="size-4" />,
        keywords: "alerts watch kpi threshold slack webhook email", run: () => openView("monitors") },
      { id: "drivers", group: "Actions", label: "Explain why a number moved",
        icon: <Split className="size-4" />,
        keywords: "drivers root cause drill down waterfall contribution mix volume rate",
        run: () => openView("drivers", activeDatasetId()) },
      { id: "significance", group: "Actions", label: "Test whether a difference is real",
        icon: <FlaskConical className="size-4" />,
        keywords: "significance statistics p value confidence interval ab test noise sample power",
        run: () => openView("significance", activeDatasetId()) },
      { id: "scenarios", group: "Actions", label: "Model a what-if scenario",
        icon: <SlidersHorizontal className="size-4" />,
        keywords: "scenario what if goal seek target lever plan simulate",
        run: () => openView("scenarios", activeDatasetId()) },
      { id: "cohorts", group: "Actions", label: "See whether customers come back",
        icon: <Users className="size-4" />,
        keywords: "cohort retention churn repeat loyalty survival ltv lifetime value",
        run: () => openView("cohorts", activeDatasetId()) },
      { id: "forecast", group: "Actions", label: "Forecast a measure with a backtest",
        icon: <TrendingUp className="size-4" />,
        keywords: "forecast projection predict trend seasonal backtest mase accuracy horizon",
        run: () => openView("forecast", activeDatasetId()) },
      { id: "sources", group: "Actions", label: "Connect a SQL database",
        icon: <Database className="size-4" />,
        keywords: "source postgres mysql duckdb sql warehouse query refresh sync",
        run: () => openView("sources") },
      { id: "briefings", group: "Actions", label: "Schedule a recurring question",
        icon: <CalendarClock className="size-4" />,
        keywords: "briefing schedule recurring digest weekly daily cron report",
        run: () => openView("briefings") },
    ];
    if (session) {
      list.push({
        id: "investigate", group: "Actions", label: "Investigate this dataset in depth",
        icon: <Compass className="size-4" />,
        keywords: "deep research investigation multi step brief objective",
        run: () => void investigate(null),
      });
    }
    if ((digest?.total ?? 0) > 0) {
      list.push({
        id: "run-monitors", group: "Actions", label: "Check every monitor now",
        icon: <Bell className="size-4" />, keywords: "refresh alerts sweep",
        run: () => {
          void api
            .runAllMonitors()
            .then(({ results }) => {
              const breached = results.filter((r) => r.run.status === "breached").length;
              toast.success(`Checked ${results.length} monitor${results.length === 1 ? "" : "s"}`,
                            breached ? `${breached} outside range.` : "All within range.");
              void refreshDigest();
            })
            .catch((error) => toast.error("Couldn't run the monitors", describe(error)));
        },
      });
    }
    if (session || board) {
      list.push(
        { id: "share", group: "Actions", label: `Share this ${session ? "analysis" : "board"}`,
          icon: <Share2 className="size-4" />, keywords: "link public", run: openShare },
        { id: "print", group: "Actions", label: "Print or save as PDF", icon: <Printer className="size-4" />,
          keywords: "pdf report export", run: openPrint },
      );
    }
    if (session) {
      list.push(
        { id: "data", group: "Actions", label: "Toggle data panel", icon: <Database className="size-4" />,
          keywords: "columns profile cleaning", run: () => setDataOpen((open) => !open) },
        { id: "export", group: "Actions", label: "Export as Markdown", icon: <Download className="size-4" />,
          run: () => void exportMarkdown().catch((error) => toast.error("Export failed", describe(error))) },
        { id: "notebook", group: "Actions", label: "Export as a Jupyter notebook",
          icon: <Notebook className="size-4" />, keywords: "ipynb code reproduce",
          run: () => void api.downloadSessionNotebook(session.id) },
        { id: "pdf", group: "Actions", label: "Export as PDF", icon: <FileDown className="size-4" />,
          run: () => void api.downloadSessionPdf(session.id) },
        { id: "pptx", group: "Actions", label: "Export as a PowerPoint deck",
          icon: <Presentation className="size-4" />, keywords: "slides deck",
          run: () => void api.downloadSessionPptx(session.id) },
        { id: "metrics", group: "Actions", label: "Define metrics for this dataset",
          icon: <Database className="size-4" />, keywords: "semantic glossary definitions rules",
          run: () => {
            setDataTab("metrics");
            setDataOpen(true);
          } },
        { id: "contract", group: "Actions", label: "Set the data contract for this dataset",
          icon: <ShieldCheck className="size-4" />,
          keywords: "expectations quality gate schema freshness validation",
          run: () => {
            setDataTab("contract");
            setDataOpen(true);
          } },
        { id: "privacy", group: "Actions", label: "Review personal data in this dataset",
          icon: <ShieldAlert className="size-4" />,
          keywords: "privacy pii gdpr redact mask hash anonymise personal data email phone",
          run: () => {
            setDataTab("privacy");
            setDataOpen(true);
          } },
        { id: "version", group: "Actions", label: "Upload a new version of this dataset",
          icon: <CloudUpload className="size-4" />, keywords: "refresh replace update data",
          run: () => {
            setDataTab("versions");
            setDataOpen(true);
          } },
      );
    }
    list.push(
      { id: "theme-light", group: "Theme", label: "Light theme", icon: <Sun className="size-4" />, run: () => setMode("light") },
      { id: "theme-dark", group: "Theme", label: "Dark theme", icon: <Moon className="size-4" />, run: () => setMode("dark") },
      { id: "theme-system", group: "Theme", label: "System theme", icon: <Monitor className="size-4" />, run: () => setMode("system") },
    );
    sessions.forEach((s) =>
      list.push({ id: `s-${s.id}`, group: "Analyses", label: s.title, hint: s.dataset_name,
                  icon: <MessageSquare className="size-4" />, run: () => void openSession(s.id) }),
    );
    boards.forEach((b) =>
      list.push({ id: `b-${b.id}`, group: "Boards", label: b.title, hint: `${b.item_count} items`,
                  icon: <LayoutDashboard className="size-4" />, run: () => openBoard(b.id) }),
    );
    datasets.forEach((d) =>
      list.push({ id: `d-${d.id}`, group: "Datasets", label: `New analysis with ${d.name}`,
                  hint: `${d.n_rows.toLocaleString()} rows`, icon: <FileSpreadsheet className="size-4" />,
                  run: () => void startSession(d.id) }),
    );
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, board, sessions, boards, datasets, digest]);

  const pinContext = useMemo(() => ({ boards, pin }), [boards, pin]);
  const watchContext = useMemo(() => ({ onCreated: () => void refreshDigest() }), [refreshDigest]);
  const headerTitle = view ? VIEW_TITLES[view.kind] : (board?.title ?? session?.title);

  return (
    <PinProvider value={pinContext}>
      <WatchProvider value={watchContext}>
      <div className="flex h-dvh overflow-hidden bg-canvas">
        <Sidebar
          open={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          health={health}
          healthError={healthError}
          usage={usage}
          sessions={sessions}
          boards={boards}
          datasets={datasets}
          activeSessionId={session?.id ?? null}
          activeBoardId={boardId}
          activeView={view?.kind ?? null}
          user={user}
          isAdmin={isAdmin}
          onSignOut={() => void signOut()}
          monitorDigest={digest}
          onSelectSession={openSession}
          onSelectBoard={openBoard}
          onOpenView={(kind) => openView(kind, DATASET_VIEWS.includes(kind) ? activeDatasetId() : null)}
          onNewAnalysis={newAnalysis}
          onCreateBoard={() => void createBoard()}
          onOpenPalette={() => setPaletteOpen(true)}
          onStartWithDataset={startSession}
          onDeleteSession={deleteSession}
          onDeleteBoard={deleteBoard}
          onDeleteDataset={deleteDataset}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center gap-1.5 border-b border-line bg-panel/70 px-3 backdrop-blur sm:px-4">
            <IconButton className="lg:hidden" label="Open menu" onClick={() => setSidebarOpen(true)}>
              <Menu className="size-4" />
            </IconButton>
            <h1 className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">
              {headerTitle ?? <span className="text-ink-2">{boardId ? "Board" : "New analysis"}</span>}
            </h1>
            <IconButton label={`Search (${mod} K)`} onClick={() => setPaletteOpen(true)}>
              <Search className="size-4" />
            </IconButton>
            {session && dataset && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setDataOpen((open) => !open)}
                aria-pressed={dataOpen}
                className={cn(dataOpen && "bg-muted text-ink")}
              >
                <Database className="size-3.5" />
                <span className="hidden max-w-44 truncate md:inline">{dataset.name}</span>
              </Button>
            )}
            {(session || board) && (
              <>
                <Button variant="ghost" size="sm" onClick={openShare}>
                  <Share2 className="size-3.5" />
                  <span className="hidden sm:inline">Share</span>
                </Button>
                <ExportMenu options={exportOptions} />
              </>
            )}
          </header>

          {/* Operator instructions (commands, env vars) are for administrators; everyone
              else gets the plain-language version of the same problem. */}
          {healthError && (
            <Banner
              tone="bad"
              icon={<CircleAlert className="size-4" />}
              action={
                <button
                  onClick={checkHealth}
                  className="inline-flex items-center gap-1 font-medium underline underline-offset-2"
                >
                  <RefreshCw className="size-3.5" />
                  Retry
                </button>
              }
            >
              Numera can&apos;t reach its server right now, so nothing can be loaded or saved.
              {isAdmin ? (
                <>
                  {" "}
                  Check that the backend is running (
                  <code className="font-mono">uvicorn app.asgi:app --port 8000</code>).
                </>
              ) : (
                " Check your connection, or try again in a moment."
              )}
            </Banner>
          )}
          {health && !health.llm_credentials_detected && (
            <Banner tone="warn" icon={<TriangleAlert className="size-4" />}>
              AI analysis is unavailable because no model API key is configured.
              {isAdmin ? (
                <>
                  {" "}
                  Add <code className="font-mono">OPENAI_API_KEY</code> to the backend{" "}
                  <code className="font-mono">.env</code> and restart it.
                </>
              ) : (
                " Ask your administrator to finish setting up Numera. Drivers, forecasts and the other tools still work."
              )}
            </Banner>
          )}
          {usage?.budget.exhausted && (
            <Banner tone="bad" icon={<CircleAlert className="size-4" />}>
              The ${usage.budget.limit_usd?.toFixed(2)} monthly AI budget is used up. New analyses are paused until{" "}
              {new Date(`${usage.budget.reset_at}T00:00:00`).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
              {isAdmin ? (
                <>
                  , or raise <code className="font-mono">AI_MONTHLY_BUDGET_USD</code> and restart the backend.
                </>
              ) : (
                ". Your administrator can raise the limit sooner."
              )}
            </Banner>
          )}
          {dataset && (dataset.privacy_scan?.counts.high ?? 0) > 0 && !dataset.privacy?.applied_at && (
            <Banner tone="warn" icon={<ShieldAlert className="size-4" />}>
              {dataset.privacy_scan?.counts.high === 1
                ? `1 column in ${dataset.name} holds`
                : `${dataset.privacy_scan?.counts.high} columns in ${dataset.name} hold`}{" "}
              personal data. Example values are already withheld from every prompt —{" "}
              <button
                onClick={() => {
                  setDataTab("privacy");
                  setDataOpen(true);
                }}
                className="font-medium underline underline-offset-2"
              >
                decide whether to redact the table
              </button>
              .
            </Banner>
          )}
          {usage?.budget.enabled && !usage.budget.exhausted && (usage.budget.used_pct ?? 0) >= 80 && (
            <Banner tone="warn" icon={<TriangleAlert className="size-4" />}>
              AI budget is {Math.round(usage.budget.used_pct ?? 0)}% used with $
              {(usage.budget.remaining_usd ?? 0).toFixed(2)} remaining this month.
            </Banner>
          )}

          <div className="flex min-h-0 flex-1">
            <div className="flex min-w-0 flex-1 flex-col">
              {view?.kind === "monitors" ? (
                <MonitorsView
                  onOpenSession={(id) => void openSession(id)}
                  onChanged={setDigest}
                  onExplain={(id, drillDown) => openView("drivers", id, drillDown)}
                  emailConfigured={health?.alerts?.email_configured}
                />
              ) : view?.kind === "sources" ? (
                <SourcesView onOpenDataset={(id) => void startSession(id)} />
              ) : view?.kind === "briefings" ? (
                <BriefingsView
                  datasets={datasets}
                  intervalMinutes={health?.briefings?.interval_minutes}
                  onOpenSession={(id) => void openSession(id)}
                />
              ) : view && DATASET_VIEWS.includes(view.kind) ? (
                // A new dataset or a new pre-aimed query is a different question entirely.
                <DatasetView
                  key={`${view.kind}:${view.datasetId ?? ""}:${JSON.stringify(view.query)}`}
                  kind={view.kind}
                  datasets={datasets}
                  datasetId={view.datasetId}
                  query={view.query}
                  onDatasetChange={(id) => openView(view.kind, id)}
                  onAsk={(id, question) => void askWithDataset(id, question)}
                />
              ) : boardId ? (
                <BoardView
                  key={boardId}
                  boardId={boardId}
                  onChange={(detail) => {
                    setBoard(detail);
                    setBoards((current) =>
                      current.map((b) => (b.id === detail.id ? { ...b, title: detail.title, item_count: detail.items.length } : b)),
                    );
                  }}
                  onOpenSession={(id) => void openSession(id)}
                />
              ) : loadingSession ? (
                <div className="mx-auto w-full max-w-3xl space-y-4 px-6 pt-10">
                  <div className="h-6 w-2/3 animate-pulse rounded bg-muted" />
                  <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
                  <div className="h-40 animate-pulse rounded-xl bg-muted" />
                </div>
              ) : session ? (
                <ChatView
                  session={session}
                  dataset={dataset}
                  pending={pending}
                  onSend={(text) => void send(text)}
                  onStop={() => abortRef.current?.abort()}
                  onInvestigate={(objective) => void investigate(objective)}
                  onOpenSession={(id) => void openSession(id)}
                  budgetExhausted={usage?.budget.exhausted}
                  onOpenCleaning={() => {
                    setDataTab("cleaning");
                    setDataOpen(true);
                  }}
                  onExplain={(query) => openView("drivers", session.dataset_id, query)}
                />
              ) : (
                <Welcome
                  uploading={uploading}
                  onUpload={(file) => ingest("file", () => api.uploadDataset(file))}
                  onSample={() => ingest("sample", api.loadSample)}
                  datasets={datasets}
                  onPickDataset={startSession}
                  maxUploadMb={health?.limits.max_upload_mb ?? 50}
                />
              )}
            </div>

            {session && dataset && dataOpen && !boardId && !view && (
              <DataPanel
                key={dataset.id}
                dataset={dataset}
                tab={dataTab}
                onTabChange={setDataTab}
                onClose={() => setDataOpen(false)}
                onSemanticsChange={(semantics: Semantics) => {
                  const updated = { ...dataset, semantics };
                  datasetCache.current.set(dataset.id, updated);
                  setDataset(updated);
                }}
                onUploadVersion={(file) => void uploadVersion(file)}
                onDatasetChanged={() => {
                  // Redaction rewrites the table, so the open copy is stale by definition.
                  datasetCache.current.delete(dataset.id);
                  void api
                    .getDataset(dataset.id)
                    .then((fresh) => {
                      datasetCache.current.set(fresh.id, fresh);
                      setDataset(fresh);
                    })
                    .catch(() => undefined);
                  void refreshLists();
                }}
              />
            )}
          </div>
        </main>
      </div>

        <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
        {shareTarget && (
          <ShareDialog
            target={shareTarget}
            onClose={() => setShareTarget(null)}
            onTokenChange={(token) => {
              if (shareTarget.kind === "session") {
                setSession((current) => (current?.id === shareTarget.id ? { ...current, share_token: token } : current));
              } else {
                setBoard((current) => (current?.id === shareTarget.id ? { ...current, share_token: token } : current));
              }
              void refreshLists();
            }}
          />
        )}
      </WatchProvider>
    </PinProvider>
  );
}

/**
 * The three dataset-scoped deterministic views share a signature — a dataset, an
 * optionally pre-aimed query, and a hand-off to the agent — so one dispatcher keeps the
 * workspace's render tree flat.
 */
function DatasetView({
  kind,
  datasets,
  datasetId,
  query,
  onDatasetChange,
  onAsk,
}: {
  kind: WorkspaceView;
  datasets: DatasetSummary[];
  datasetId: string | null;
  query: DriverQuery;
  onDatasetChange: (id: string) => void;
  onAsk: (datasetId: string, question: string) => void;
}) {
  const shared = { datasets, datasetId, onDatasetChange, onAsk };
  if (kind === "significance") return <SignificanceView {...shared} />;
  if (kind === "scenarios") return <ScenarioView {...shared} />;
  if (kind === "cohorts") return <CohortsView {...shared} />;
  if (kind === "forecast") return <ForecastView {...shared} />;
  return <DriversView {...shared} initialQuery={query} />;
}

function Banner({
  tone,
  icon,
  action,
  children,
}: {
  tone: "bad" | "warn";
  icon: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div
      role="status"
      className={cn(
        "no-print flex shrink-0 items-start gap-2.5 border-b border-line px-4 py-2.5 text-[13px] leading-snug",
        tone === "bad" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn",
      )}
    >
      <span className="mt-px shrink-0">{icon}</span>
      <p className="min-w-0 flex-1 text-ink">{children}</p>
      {action && <span className="shrink-0 text-ink">{action}</span>}
    </div>
  );
}
