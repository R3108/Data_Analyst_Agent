import type {
  AccountStatus,
  ActivityEntry,
  AdminOverview,
  AdminUser,
  AdminUserDetail,
  AlertChannel,
  AlertDelivery,
  AlertEvent,
  AlertKind,
  AlertSettings,
  AuditEvent,
  AuthConfig,
  AuthSession,
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
  PasswordStrength,
  PinKind,
  Preview,
  PrivacyAction,
  PrivacyReport,
  RecallMatch,
  RecallSummary,
  Role,
  RootCause,
  RouteResult,
  ScenarioQuery,
  ScenarioResult,
  Semantics,
  SessionDetail,
  SessionSummary,
  SharedDocument,
  SignInMethods,
  SignificanceOptions,
  SignificanceQuery,
  SignificanceResult,
  Source,
  SourceTest,
  UsageReport,
  User,
} from "./types";

/**
 * Where the API lives.
 *
 * Empty by default, meaning same-origin: `next.config.ts` rewrites `/api/*` to the
 * backend, so the browser only ever talks to this app's own origin. That is what makes
 * the session cookie first-party — it is set, sent and expired by one origin, with no
 * cross-site cookie rules to satisfy and nothing for a browser's third-party cookie
 * policy to block.
 *
 * Setting `NEXT_PUBLIC_API_URL` points the browser straight at the backend instead.
 * That still works, but the two hosts must then share a registrable domain
 * (`app.example.com` and `api.example.com`) for a `SameSite=Lax` cookie to travel, and
 * the backend's `CORS_ORIGINS` must name this app.
 */
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");

/** True when the API is reached through this app's own origin. */
export const SAME_ORIGIN_API = API_BASE === "";

const CSRF_COOKIE = "numera_csrf";
const CSRF_HEADER = "X-CSRF-Token";

/**
 * The session lives in an `HttpOnly` cookie this code cannot read — which is the point:
 * a script that can read a session token can steal it, and every dependency on the page
 * is such a script. What we *can* read is the CSRF cookie, which is deliberately not
 * `HttpOnly` and is worthless on its own. Echoing it back in a header is what proves to
 * the server that a state-changing request came from this app rather than from a form
 * on somebody else's site.
 */
function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  for (const entry of document.cookie.split(";")) {
    const [name, ...rest] = entry.trim().split("=");
    // The cookie takes the `__Host-` prefix over HTTPS, where the browser guarantees it
    // was set by this origin with `Secure` and `Path=/`.
    if (name === CSRF_COOKIE || name === `__Host-${CSRF_COOKIE}`) {
      return decodeURIComponent(rest.join("="));
    }
  }
  return null;
}

const UNSAFE = /^(POST|PUT|PATCH|DELETE)$/i;

/** Send cookies, and prove the request came from us when it changes something. */
function withAuth(init?: RequestInit): RequestInit {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
  if (UNSAFE.test(init?.method ?? "GET")) {
    const token = csrfToken();
    if (token) headers[CSRF_HEADER] = token;
  }
  return { ...init, headers, credentials: "include" };
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

  /** The session is gone or was never there — the caller should show the sign-in screen. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

/** Notified whenever the API says this browser is no longer signed in. */
type SessionLostHandler = () => void;
let onSessionLost: SessionLostHandler | null = null;

export function setSessionLostHandler(handler: SessionLostHandler | null): void {
  onSessionLost = handler;
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

const UNREACHABLE = SAME_ORIGIN_API
  ? "Cannot reach the Numera API. Is the backend running?"
  : `Cannot reach the Numera API at ${API_BASE}. Is the backend running?`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, withAuth(init));
  } catch {
    throw new ApiError(UNREACHABLE, "network_error", 0);
  }
  if (!response.ok) {
    const error = await toApiError(response);
    // One place decides what an expired session means, so no caller has to. The bootstrap
    // call is exempt: "nobody is signed in" is its ordinary answer, not a lost session.
    if (error.status === 401 && !path.startsWith("/api/auth/")) onSessionLost?.();
    throw error;
  }
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
    throw new ApiError(UNREACHABLE, "network_error", 0);
  }
  if (!response.ok) {
    const error = await toApiError(response);
    if (error.status === 401) onSessionLost?.();
    throw error;
  }

  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = match?.[1] ?? fallbackName;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Accounts and sessions.
 *
 * No function here takes or returns a session token: the browser holds it in a cookie
 * it cannot read, and the server sets and clears it. A password only ever travels up.
 */
export const auth = {
  config: () => request<AuthConfig>("/api/auth/config"),
  /** The signed-in account, or null. Never throws on "not signed in". */
  session: () => request<{ user: User | null }>("/api/auth/session"),
  register: (body: { email: string; password: string; name?: string }) =>
    request<{ user: User }>("/api/auth/register", { method: "POST", ...json(body) }),
  login: (body: { email: string; password: string }) =>
    request<{ user: User }>("/api/auth/login", { method: "POST", ...json(body) }),
  logout: () => request<{ ok: true }>("/api/auth/logout", { method: "POST" }),
  forgotPassword: (email: string) =>
    request<{ ok: true; message: string; reset_link?: string }>("/api/auth/forgot-password", {
      method: "POST",
      ...json({ email }),
    }),
  resetPassword: (token: string, password: string) =>
    request<{ user: User }>("/api/auth/reset-password", {
      method: "POST",
      ...json({ token, password }),
    }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ user: User; signed_out_other_devices: boolean }>("/api/auth/change-password", {
      method: "POST",
      ...json({ current_password: currentPassword, new_password: newPassword }),
    }),
  updateProfile: (name: string) =>
    request<{ user: User }>("/api/auth/profile", { method: "PATCH", ...json({ name }) }),
  listSessions: () => request<AuthSession[]>("/api/auth/sessions"),
  revokeSession: (id: string) =>
    request<{ ok: true }>(`/api/auth/sessions/${id}`, { method: "DELETE" }),
  revokeAllSessions: () =>
    request<{ ok: true }>("/api/auth/sessions/revoke-all", { method: "POST" }),
  /** Advisory scoring for the sign-up meter. Nothing is stored server-side. */
  strength: (password: string) =>
    request<PasswordStrength>("/api/auth/password-strength", {
      method: "POST",
      ...json({ password }),
    }),
  methods: () => request<SignInMethods>("/api/auth/methods"),
  disconnectGoogle: () => request<SignInMethods>("/api/auth/google", { method: "DELETE" }),
  /**
   * Where to send the browser to sign in with Google — a full-page navigation, not a
   * fetch. The server redirects to Google and back, and the whole exchange happens
   * between the two servers; nothing from it passes through this code.
   */
  googleStartUrl: (options: { next?: string; intent?: "signin" | "link" } = {}) => {
    const params = new URLSearchParams();
    if (options.next && options.next !== "/") params.set("next", options.next);
    if (options.intent === "link") params.set("intent", "link");
    const query = params.toString();
    return `${API_BASE}/api/auth/google/start${query ? `?${query}` : ""}`;
  },
};

/** What to tell somebody whose Google sign-in came back with an error code. */
export function googleErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    google_cancelled: "Google sign-in was cancelled.",
    google_expired: "That sign-in took too long or was started in another tab. Please try again.",
    google_unavailable: "Sign in with Google is not enabled on this workspace.",
    google_email_unverified: "Your Google account's email address is not verified with Google.",
    google_link_required:
      "An account already uses this email. Sign in with your password, then connect Google from your account page.",
    google_in_use: "That Google account is already connected to a different Numera account.",
    google_already_linked: "A Google account is already connected. Disconnect it first.",
    google_unreachable: "Couldn't reach Google. Check your connection and try again.",
    registration_closed: "Sign-up is closed on this workspace. Ask an administrator for an invitation.",
    domain_not_allowed: "Sign-up on this workspace is limited to specific email domains.",
    user_limit_reached: "This workspace has reached its account limit.",
    account_suspended: "This account has been suspended. Contact an administrator.",
    rate_limited: "Too many attempts. Wait a few minutes and try again.",
  };
  return messages[code] ?? "Google sign-in didn't complete. Please try again.";
}

/** Administration. Every call is refused with 403 for a non-admin account. */
export const admin = {
  overview: () => request<AdminOverview>("/api/admin/overview"),
  listUsers: (params: { q?: string; role?: Role; status?: AccountStatus } = {}) => {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.role) query.set("role", params.role);
    if (params.status) query.set("status", params.status);
    const suffix = query.toString();
    return request<AdminUser[]>(`/api/admin/users${suffix ? `?${suffix}` : ""}`);
  },
  getUser: (id: string) => request<AdminUserDetail>(`/api/admin/users/${id}`),
  createUser: (body: { email: string; password: string; name?: string; role?: Role }) =>
    request<User>("/api/admin/users", { method: "POST", ...json(body) }),
  updateUser: (id: string, patch: { name?: string; role?: Role; status?: AccountStatus }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", ...json(patch) }),
  setPassword: (id: string, password: string) =>
    request<{ ok: true; must_change_password: boolean }>(`/api/admin/users/${id}/password`, {
      method: "POST",
      ...json({ password }),
    }),
  revokeSessions: (id: string) =>
    request<{ ok: true }>(`/api/admin/users/${id}/sessions/revoke-all`, { method: "POST" }),
  /** Irreversible: erases the account and its entire private workspace. */
  deleteUser: (id: string, confirmEmail: string) =>
    request<{ ok: true }>(
      `/api/admin/users/${id}?confirm_email=${encodeURIComponent(confirmEmail)}`,
      { method: "DELETE" },
    ),
  audit: (params: { limit?: number; user_id?: string; event?: string } = {}) => {
    const query = new URLSearchParams({ limit: String(params.limit ?? 100) });
    if (params.user_id) query.set("user_id", params.user_id);
    if (params.event) query.set("event", params.event);
    return request<AuditEvent[]>(`/api/admin/audit?${query}`);
  },
  purgeSessions: () =>
    request<{ removed_sessions: number }>("/api/admin/maintenance/purge-sessions", {
      method: "POST",
    }),
};

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
    const response = await fetch(`${API_BASE}/api/sessions/${id}/export`, withAuth());
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
