import type {
  ActivityEntry,
  AlertChannel,
  AlertDelivery,
  AlertEvent,
  AlertKind,
  AlertSettings,
  BoardDetail,
  BoardItem,
  BoardSummary,
  Briefing,
  ChatEvent,
  CohortOptions,
  CohortQuery,
  CohortResult,
  Comment,
  CommentListing,
  CommentSubject,
  ContractState,
  DataContract,
  Dataset,
  DatasetSummary,
  DatasetVersion,
  Dialect,
  DriverOptions,
  DriverQuery,
  DriverResult,
  ForecastOptions,
  ForecastQuery,
  ForecastResult,
  GoalSeekResult,
  Health,
  Identity,
  LeverKind,
  Monitor,
  MonitorDigest,
  MonitorDirection,
  MonitorRun,
  PinKind,
  Preview,
  PrivacyAction,
  PrivacyReport,
  RecallMatch,
  RecallSummary,
  RootCause,
  RouteResult,
  ScenarioQuery,
  ScenarioResult,
  Semantics,
  SessionDetail,
  SessionSummary,
  SharedDocument,
  SignificanceOptions,
  SignificanceQuery,
  SignificanceResult,
  Source,
  SourceTest,
  UsageReport,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

const TOKEN_KEY = "numera.workspace-token";

/**
 * Workspace access token, when the backend is running protected.
 *
 * Kept in localStorage rather than a cookie: the token is a shared secret typed by the
 * user, every request that needs it is same-origin JavaScript, and a cookie would be
 * sent on navigations the API never makes.
 */
export const auth = {
  get(): string | null {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(TOKEN_KEY);
    } catch {
      return null; // private mode, or site data blocked
    }
  },
  set(token: string | null): void {
    if (typeof window === "undefined") return;
    try {
      if (token) window.localStorage.setItem(TOKEN_KEY, token);
      else window.localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* nothing to do: the user will be asked again next request */
    }
  },
};

/** Merge the workspace token into a request's headers, if one is stored. */
function withAuth(init?: RequestInit): RequestInit | undefined {
  const token = auth.get();
  if (!token) return init;
  return {
    ...init,
    headers: { ...(init?.headers as Record<string, string> | undefined), Authorization: `Bearer ${token}` },
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: { error?: { message?: string; code?: string } } | undefined;
  try {
    body = await response.json();
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(
    body?.error?.message ?? `Request failed with status ${response.status}`,
    body?.error?.code ?? "http_error",
    response.status,
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, withAuth(init));
  } catch {
    throw new ApiError(
      `Cannot reach the Numera API at ${API_BASE}. Is the backend running?`,
      "network_error",
      0,
    );
  }
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/** Fetch a generated document and hand the browser a file to save. */
export async function downloadDocument(
  path: string,
  fallbackName: string,
  init?: RequestInit,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, withAuth(init));
  } catch {
    throw new ApiError(`Cannot reach the Numera API at ${API_BASE}.`, "network_error", 0);
  }
  if (!response.ok) throw await toApiError(response);

  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = match?.[1] ?? fallbackName;
  link.click();
  URL.revokeObjectURL(url);
}

export const api = {
  health: () => request<Health>("/api/health"),
  me: () => request<Identity>("/api/me"),

  listDatasets: () => request<DatasetSummary[]>("/api/datasets"),
  getDataset: (id: string) => request<Dataset>(`/api/datasets/${id}`),
  previewDataset: (id: string, limit = 100) => request<Preview>(`/api/datasets/${id}/preview?limit=${limit}`),
  deleteDataset: (id: string) => request<void>(`/api/datasets/${id}`, { method: "DELETE" }),
  loadSample: () => request<Dataset>("/api/datasets/sample", { method: "POST" }),
  uploadDataset: (file: File, sheet?: string, replaces?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (sheet) form.append("sheet", sheet);
    if (replaces) form.append("replaces", replaces);
    return request<Dataset>("/api/datasets", { method: "POST", body: form });
  },
  datasetVersions: (id: string) => request<DatasetVersion[]>(`/api/datasets/${id}/versions`),
  getSemantics: (id: string) => request<Semantics>(`/api/datasets/${id}/semantics`),
  saveSemantics: (id: string, semantics: Semantics) =>
    request<Semantics>(`/api/datasets/${id}/semantics`, { method: "PUT", ...json(semantics) }),
  downloadDataset: (id: string, format: "csv" | "parquet" = "csv") =>
    downloadDocument(`/api/datasets/${id}/download?format=${format}`, `dataset-clean.${format}`),

  getContract: (id: string) => request<ContractState>(`/api/datasets/${id}/contract`),
  saveContract: (id: string, contract: DataContract) =>
    request<ContractState>(`/api/datasets/${id}/contract`, { method: "PUT", ...json(contract) }),
  suggestContract: (id: string) =>
    request<DataContract>(`/api/datasets/${id}/contract/suggest`, { method: "POST" }),
  checkContract: (id: string) =>
    request<ContractState>(`/api/datasets/${id}/contract/check`, { method: "POST" }),
  downloadContract: (id: string) =>
    downloadDocument(`/api/datasets/${id}/contract.md`, "data-contract.md"),

  driverOptions: (id: string) => request<DriverOptions>(`/api/datasets/${id}/drivers/options`),
  explainDrivers: (id: string, query: DriverQuery = {}) =>
    request<DriverResult>(`/api/datasets/${id}/drivers`, { method: "POST", ...json(query) }),
  downloadDrivers: (id: string, query: DriverQuery = {}) =>
    downloadDocument(`/api/datasets/${id}/drivers/export.md`, "driver-analysis.md", {
      method: "POST",
      ...json(query),
    }),

  cohortOptions: (id: string) => request<CohortOptions>(`/api/datasets/${id}/cohorts/options`),
  analyzeCohorts: (id: string, query: CohortQuery = {}) =>
    request<CohortResult>(`/api/datasets/${id}/cohorts`, { method: "POST", ...json(query) }),
  downloadCohorts: (id: string, query: CohortQuery = {}) =>
    downloadDocument(`/api/datasets/${id}/cohorts/export.md`, "cohort-retention.md", {
      method: "POST",
      ...json(query),
    }),

  forecastOptions: (id: string) => request<ForecastOptions>(`/api/datasets/${id}/forecast/options`),
  forecast: (id: string, query: ForecastQuery = {}) =>
    request<ForecastResult>(`/api/datasets/${id}/forecast`, { method: "POST", ...json(query) }),
  downloadForecast: (id: string, query: ForecastQuery = {}) =>
    downloadDocument(`/api/datasets/${id}/forecast/export.md`, "forecast.md", {
      method: "POST",
      ...json(query),
    }),

  getPrivacy: (id: string) => request<PrivacyReport>(`/api/datasets/${id}/privacy`),
  scanPrivacy: (id: string) =>
    request<PrivacyReport>(`/api/datasets/${id}/privacy/scan`, { method: "POST" }),
  savePrivacy: (id: string, policy: Record<string, PrivacyAction>) =>
    request<PrivacyReport>(`/api/datasets/${id}/privacy`, { method: "PUT", ...json({ policy }) }),
  /** Irreversible: rewrites the cleaned table and purges the original upload. */
  applyPrivacy: (id: string) =>
    request<PrivacyReport>(`/api/datasets/${id}/privacy/apply`, { method: "POST" }),
  downloadPrivacy: (id: string) =>
    downloadDocument(`/api/datasets/${id}/privacy.md`, "privacy-review.md"),

  significanceOptions: (id: string) =>
    request<SignificanceOptions>(`/api/datasets/${id}/significance/options`),
  testSignificance: (id: string, query: SignificanceQuery = {}) =>
    request<SignificanceResult>(`/api/datasets/${id}/significance`, { method: "POST", ...json(query) }),
  downloadSignificance: (id: string, query: SignificanceQuery = {}) =>
    downloadDocument(`/api/datasets/${id}/significance/export.md`, "significance-test.md", {
      method: "POST",
      ...json(query),
    }),

  scenarioOptions: (id: string) =>
    request<ScenarioResult["options"]>(`/api/datasets/${id}/scenarios/options`),
  simulateScenario: (id: string, query: ScenarioQuery = {}) =>
    request<ScenarioResult>(`/api/datasets/${id}/scenarios`, { method: "POST", ...json(query) }),
  goalSeek: (id: string, body: ScenarioQuery & { target: number; lever: LeverKind; segment?: string | null }) =>
    request<GoalSeekResult>(`/api/datasets/${id}/scenarios/goal-seek`, { method: "POST", ...json(body) }),
  downloadScenario: (id: string, query: ScenarioQuery = {}) =>
    downloadDocument(`/api/datasets/${id}/scenarios/export.md`, "scenario.md", {
      method: "POST",
      ...json(query),
    }),

  listSources: () => request<{ sources: Source[]; dialects: Dialect[] }>("/api/sources"),
  testSource: (body: { name?: string; dsn: string; query: string }) =>
    request<SourceTest>("/api/sources/test", { method: "POST", ...json(body) }),
  createSource: (body: { name?: string; dsn: string; query: string; refresh_minutes?: number }) =>
    request<Source>("/api/sources", { method: "POST", ...json(body) }),
  updateSource: (
    id: string,
    patch: { name?: string; dsn?: string; query?: string; refresh_minutes?: number; enabled?: boolean },
  ) => request<Source>(`/api/sources/${id}`, { method: "PATCH", ...json(patch) }),
  deleteSource: (id: string) => request<void>(`/api/sources/${id}`, { method: "DELETE" }),
  syncSource: (id: string) =>
    request<{ source: Source; dataset: Dataset }>(`/api/sources/${id}/sync`, { method: "POST" }),

  listBriefings: (datasetId?: string) =>
    request<Briefing[]>(`/api/briefings${datasetId ? `?dataset_id=${datasetId}` : ""}`),
  createBriefing: (body: {
    dataset_id: string;
    question: string;
    title?: string;
    schedule_hours?: number;
    deliver?: boolean;
  }) => request<Briefing>("/api/briefings", { method: "POST", ...json(body) }),
  updateBriefing: (
    id: string,
    patch: { title?: string; question?: string; schedule_hours?: number; enabled?: boolean; deliver?: boolean },
  ) => request<Briefing>(`/api/briefings/${id}`, { method: "PATCH", ...json(patch) }),
  deleteBriefing: (id: string) => request<void>(`/api/briefings/${id}`, { method: "DELETE" }),
  runBriefing: (id: string, deliver?: boolean) =>
    request<{ briefing: Briefing; session_id: string }>(
      `/api/briefings/${id}/run${deliver === undefined ? "" : `?deliver=${deliver}`}`,
      { method: "POST" },
    ),

  listComments: (subjectKind: CommentSubject, subjectId: string) =>
    request<CommentListing>(
      `/api/comments?subject_kind=${subjectKind}&subject_id=${encodeURIComponent(subjectId)}`,
    ),
  commentCounts: (subjectKind: CommentSubject, ids: string[]) =>
    ids.length
      ? request<Record<string, number>>(
          `/api/comments/counts?subject_kind=${subjectKind}&ids=${encodeURIComponent(ids.join(","))}`,
        )
      : Promise.resolve({} as Record<string, number>),
  addComment: (body: {
    subject_kind: CommentSubject;
    subject_id: string;
    body: string;
    parent_id?: string;
    author?: string;
  }) => request<Comment>("/api/comments", { method: "POST", ...json(body) }),
  updateComment: (id: string, patch: { body?: string; resolved?: boolean }) =>
    request<Comment>(`/api/comments/${id}`, { method: "PATCH", ...json(patch) }),
  deleteComment: (id: string) => request<void>(`/api/comments/${id}`, { method: "DELETE" }),

  activity: (limit = 50) => request<ActivityEntry[]>(`/api/activity?limit=${limit}`),
  routeQuestion: (question: string) =>
    request<RouteResult>("/api/route", { method: "POST", ...json({ question }) }),
  recall: (question: string, datasetId?: string) =>
    request<{ question: string; matches: RecallMatch[]; summary: RecallSummary | null }>(
      `/api/recall?q=${encodeURIComponent(question)}${datasetId ? `&dataset_id=${datasetId}` : ""}`,
    ),

  alertSettings: () => request<AlertSettings>("/api/alerts"),
  createChannel: (body: { kind: AlertKind; target: string; name?: string; events?: AlertEvent[] }) =>
    request<AlertChannel>("/api/alerts/channels", { method: "POST", ...json(body) }),
  updateChannel: (
    id: string,
    patch: { name?: string; target?: string; events?: AlertEvent[]; enabled?: boolean },
  ) => request<AlertChannel>(`/api/alerts/channels/${id}`, { method: "PATCH", ...json(patch) }),
  deleteChannel: (id: string) => request<void>(`/api/alerts/channels/${id}`, { method: "DELETE" }),
  testChannel: (id: string) =>
    request<AlertDelivery>(`/api/alerts/channels/${id}/test`, { method: "POST" }),
  sendDigest: () =>
    request<{ sent: number; deliveries: AlertDelivery[] }>("/api/alerts/digest", { method: "POST" }),

  listMonitors: (datasetId?: string) =>
    request<Monitor[]>(`/api/monitors${datasetId ? `?dataset_id=${datasetId}` : ""}`),
  getMonitor: (id: string) => request<Monitor>(`/api/monitors/${id}`),
  createMonitor: (body: {
    message_id: string;
    index: number;
    direction: MonitorDirection;
    threshold: number;
    title?: string;
  }) => request<Monitor>("/api/monitors", { method: "POST", ...json(body) }),
  updateMonitor: (
    id: string,
    patch: { title?: string; direction?: MonitorDirection; threshold?: number; enabled?: boolean },
  ) => request<Monitor>(`/api/monitors/${id}`, { method: "PATCH", ...json(patch) }),
  deleteMonitor: (id: string) => request<void>(`/api/monitors/${id}`, { method: "DELETE" }),
  runMonitor: (id: string) =>
    request<{ monitor: Monitor; run: MonitorRun }>(`/api/monitors/${id}/run`, { method: "POST" }),
  /** Why did this metric move? The driver drill-down on the monitor's own measure. */
  diagnoseMonitor: (id: string, measure?: string) =>
    request<RootCause>(
      `/api/monitors/${id}/diagnose${measure ? `?measure=${encodeURIComponent(measure)}` : ""}`,
      { method: "POST" },
    ),
  runAllMonitors: (datasetId?: string) =>
    request<{ ran: number; results: { monitor: Monitor; run: MonitorRun }[] }>(
      `/api/monitors/run${datasetId ? `?dataset_id=${datasetId}` : ""}`,
      { method: "POST" },
    ),
  monitorDigest: () => request<MonitorDigest>("/api/monitors/digest"),
  downloadMonitorBriefing: () =>
    downloadDocument("/api/monitors/digest.md", "monitor-briefing.md"),

  listSessions: () => request<SessionSummary[]>("/api/sessions"),
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  createSession: (datasetId: string) =>
    request<SessionDetail>("/api/sessions", { method: "POST", ...json({ dataset_id: datasetId }) }),
  renameSession: (id: string, title: string) =>
    request<SessionSummary>(`/api/sessions/${id}`, { method: "PATCH", ...json({ title }) }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  exportSession: async (id: string) => {
    const response = await fetch(`${API_BASE}/api/sessions/${id}/export`);
    if (!response.ok) throw await toApiError(response);
    return response.text();
  },
  shareSession: (id: string) => request<{ token: string }>(`/api/sessions/${id}/share`, { method: "POST" }),
  unshareSession: (id: string) => request<void>(`/api/sessions/${id}/share`, { method: "DELETE" }),
  downloadSessionNotebook: (id: string) =>
    downloadDocument(`/api/sessions/${id}/notebook`, "analysis.ipynb"),
  downloadSessionPdf: (id: string) => downloadDocument(`/api/sessions/${id}/export.pdf`, "analysis.pdf"),
  downloadSessionPptx: (id: string) => downloadDocument(`/api/sessions/${id}/export.pptx`, "analysis.pptx"),

  usage: (days = 30) => request<UsageReport>(`/api/usage?days=${days}`),

  listBoards: () => request<BoardSummary[]>("/api/boards"),
  getBoard: (id: string) => request<BoardDetail>(`/api/boards/${id}`),
  createBoard: (title: string, description = "") =>
    request<BoardDetail>("/api/boards", { method: "POST", ...json({ title, description }) }),
  updateBoard: (id: string, patch: { title?: string; description?: string }) =>
    request<BoardDetail>(`/api/boards/${id}`, { method: "PATCH", ...json(patch) }),
  deleteBoard: (id: string) => request<void>(`/api/boards/${id}`, { method: "DELETE" }),
  pinToBoard: (boardId: string, body: { kind: PinKind; message_id: string; index: number }) =>
    request<BoardItem>(`/api/boards/${boardId}/items`, { method: "POST", ...json(body) }),
  /** Pin a deterministic result; the server recomputes it before snapshotting. */
  pinComputed: (
    boardId: string,
    body: {
      kind: "chart" | "table";
      source: "drivers" | "significance" | "scenarios" | "cohorts" | "forecast";
      dataset_id: string;
      index: number;
      params: DriverQuery | SignificanceQuery | ScenarioQuery | CohortQuery | ForecastQuery;
      title?: string;
    },
  ) => request<BoardItem>(`/api/boards/${boardId}/items`, { method: "POST", ...json(body) }),
  addNote: (boardId: string, text: string) =>
    request<BoardItem>(`/api/boards/${boardId}/items`, { method: "POST", ...json({ kind: "note", text }) }),
  updateBoardItem: (boardId: string, itemId: string, patch: { title?: string; wide?: boolean; text?: string }) =>
    request<BoardItem>(`/api/boards/${boardId}/items/${itemId}`, { method: "PATCH", ...json(patch) }),
  removeBoardItem: (boardId: string, itemId: string) =>
    request<void>(`/api/boards/${boardId}/items/${itemId}`, { method: "DELETE" }),
  reorderBoard: (boardId: string, itemIds: string[]) =>
    request<BoardDetail>(`/api/boards/${boardId}/order`, { method: "PUT", ...json({ item_ids: itemIds }) }),
  shareBoard: (id: string) => request<{ token: string }>(`/api/boards/${id}/share`, { method: "POST" }),
  unshareBoard: (id: string) => request<void>(`/api/boards/${id}/share`, { method: "DELETE" }),
  downloadBoardPdf: (id: string) => downloadDocument(`/api/boards/${id}/export.pdf`, "board.pdf"),
  downloadBoardPptx: (id: string) => downloadDocument(`/api/boards/${id}/export.pptx`, "board.pptx"),

  getShared: (token: string) => request<SharedDocument>(`/api/share/${encodeURIComponent(token)}`),
};

/** POST a question and consume the Server-Sent Events stream. */
export function streamChat(
  sessionId: string,
  message: string,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamEvents(`/api/sessions/${sessionId}/chat`, { message }, onEvent, signal);
}

/**
 * Run a deep-research investigation. Emits the same events as a chat turn, plus one
 * `assistant_message` per sub-analysis as it completes, so the UI needs no special case.
 */
export function streamInvestigation(
  sessionId: string,
  objective: string | null,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamEvents(`/api/sessions/${sessionId}/investigate`, { objective }, onEvent, signal);
}

async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, withAuth({ method: "POST", ...json(body), signal }));
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(`Cannot reach the Numera API at ${API_BASE}.`, "network_error", 0);
  }
  if (!response.ok || !response.body) throw await toApiError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseEventBlock(block);
      if (parsed) onEvent(parsed);
    }
  }
}

function parseEventBlock(block: string): ChatEvent | null {
  let name = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { event: name, data: JSON.parse(data.join("\n")) } as ChatEvent;
  } catch {
    return null;
  }
}
