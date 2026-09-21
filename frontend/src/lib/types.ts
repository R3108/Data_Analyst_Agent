export type KpiFormat = "auto" | "number" | "integer" | "currency" | "percent" | "text";

export interface Health {
  status: string;
  version: string;
  model: string;
  llm_credentials_detected: boolean;
  sandbox: { timeout_s: number; memory_mb: number };
  monitors?: { interval_minutes: number; root_cause?: boolean };
  privacy?: { scan_enabled: boolean };
  alerts?: { enabled: boolean; email_configured: boolean; digest_hours: number };
  briefings?: { interval_minutes: number };
  investigations?: { max_steps: number };
  recall?: { enabled: boolean; limit: number };
  sources?: { dialects: string[]; sync_interval_minutes: number };
  workspace?: { protected: boolean; isolation?: string };
  limits: { max_upload_mb: number; max_rows: number; ai_monthly_budget_usd: number };
  /** False on the anonymous half of the response, which carries liveness only. */
  authenticated?: boolean;
}

export type Role = "admin" | "user";
export type AccountStatus = "active" | "suspended";

/** The signed-in account. Never carries anything secret. */
export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  status?: AccountStatus;
  must_change_password?: boolean;
  last_login_at?: string | null;
  password_changed_at?: string | null;
  created_at?: string;
  updated_at?: string;
  /** False for an account created through Google that has not set a password yet. */
  has_password?: boolean;
}

/** `/api/auth/methods` — the ways this account can sign in. */
export interface SignInMethods {
  password: boolean;
  google: { email: string | null; connected_at: string; last_used_at: string | null } | null;
  google_available: boolean;
}

/** `/api/me` — the account, plus the display name the comment UI already reads. */
export interface Identity extends User {
  name: string;
  protected: boolean;
}

/** What the sign-in screens need before anyone is signed in. */
export interface AuthConfig {
  registration_enabled: boolean;
  allowed_domains: string[];
  first_run: boolean;
  password_reset_enabled: boolean;
  email_delivery: boolean;
  min_password_length: number;
  google_enabled?: boolean;
}

export interface PasswordStrength {
  score: number;
  label: string;
  suggestions: string[];
}

export interface AuthSession {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}

export interface AccountUsage {
  datasets: number;
  sessions: number;
  messages: number;
  boards: number;
  monitors: number;
  sources: number;
  rows: number;
  storage_bytes: number;
}

export interface AdminUser extends User {
  usage: AccountUsage;
  active_sessions: number;
}

export interface AdminUserDetail extends User {
  usage: AccountUsage;
  sessions: AuthSession[];
  recent_events: AuditEvent[];
}

export interface AuditEvent {
  id: string;
  created_at: string;
  event: string;
  outcome: string;
  user_id: string | null;
  email: string | null;
  actor_id: string | null;
  ip: string | null;
  user_agent: string | null;
  detail: string | null;
  user_email?: string | null;
  user_name?: string | null;
}

export interface AdminOverview {
  version: string;
  users: { total: number; admins: number; suspended: number; new_this_week: number };
  sessions: { active: number; signed_in_users: number };
  activity_7d: { sign_ins: number; registrations: number; password_resets: number };
  storage: { total_bytes: number; datasets: number };
  security: {
    password_algorithm: string;
    argon2_available: boolean;
    secure_cookies: boolean;
    same_site: string;
    auth_secret_configured: boolean;
    environment: string;
    registration_enabled: boolean;
    allowed_domains: string[];
    session_idle_days: number;
    session_absolute_days: number;
    email_delivery: boolean;
    trust_forwarded_for: boolean;
  };
}

export interface MetricDefinition {
  name: string;
  definition: string;
  format?: KpiFormat;
  unit?: string;
  higher_is_better?: boolean;
}

export interface GlossaryEntry {
  term: string;
  definition: string;
}

/** The binding business definitions the agent must honour for a dataset. */
export interface Semantics {
  metrics: MetricDefinition[];
  rules: string[];
  glossary: GlossaryEntry[];
}

export interface VersionDiff {
  previous_dataset_id: string | null;
  previous_version: number;
  current_version: number;
  rows: { previous: number; current: number; change: number; change_pct: number | null };
  columns_added: string[];
  columns_removed: string[];
  type_changes: { column: string; previous: string; current: string }[];
  measures: { column: string; previous_total: number; current_total: number; change_pct: number | null }[];
  quality: { previous: number; current: number; change: number };
  coverage: {
    column: string;
    previous_end: string;
    current_end: string;
    extended: boolean;
    previous_start: string;
    current_start: string;
  } | null;
  notable: string[];
  headline: string;
}

export interface DatasetVersion {
  id: string;
  name: string;
  original_filename: string;
  n_rows: number;
  n_cols: number;
  size_bytes: number;
  created_at: string;
  version: number;
  parent_dataset_id: string | null;
  root_dataset_id: string | null;
  version_diff: VersionDiff | null;
  contract_result?: ContractResult | null;
}

/* --- Data contracts ------------------------------------------------------ */

export type ExpectationKind =
  | "schema"
  | "not_null"
  | "unique"
  | "range"
  | "allowed_values"
  | "row_count"
  | "freshness";

export type ContractStatus = "pass" | "warn" | "fail" | "empty";
export type CheckStatus = "pass" | "warn" | "fail" | "error";

export interface Expectation {
  id: string;
  kind: ExpectationKind;
  column: string | null;
  severity: "fail" | "warn";
  params: Record<string, unknown>;
  description: string;
  enabled: boolean;
}

export interface DataContract {
  expectations: Expectation[];
}

export interface CheckResult {
  id: string;
  kind: ExpectationKind;
  column: string | null;
  severity: "fail" | "warn";
  description: string;
  status: CheckStatus;
  detail: string;
  observed: unknown;
}

export interface ContractResult {
  status: ContractStatus;
  checked_at: string;
  counts: { pass: number; warn: number; fail: number };
  score: number | null;
  results: CheckResult[];
  failures: CheckResult[];
  headline: string;
}

export interface ContractState {
  contract: DataContract;
  result: ContractResult | null;
  suggested: boolean;
}

/* --- Driver analysis ----------------------------------------------------- */

export type Aggregation = "sum" | "mean";
export type PeriodMode = "auto" | "yoy" | "month" | "week" | "halves" | "custom";

export interface DriverOptions {
  measures: { name: string; aggregation: Aggregation }[];
  dimensions: string[];
  date_columns: string[];
  defaults: {
    measure: string | null;
    date_column: string | null;
    aggregation: Aggregation;
    period: PeriodMode;
  };
  available: boolean;
}

export interface DriverQuery {
  measure?: string;
  date_column?: string;
  dimensions?: string[];
  /** Look through this dimension; otherwise the best-scoring one leads. */
  focus?: string;
  aggregation?: Aggregation;
  period?: PeriodMode;
  baseline_start?: string;
  baseline_end?: string;
  current_start?: string;
  current_end?: string;
  top_n?: number;
}

export interface Contributor {
  label: string;
  baseline: number;
  current: number;
  change: number;
  change_pct: number | null;
  contribution_pct: number | null;
  surprise: number | null;
  share_baseline: number | null;
  share_current: number | null;
  share_change: number | null;
  baseline_rows: number;
  current_rows: number;
  status: "grew" | "shrank" | "new" | "lost" | "flat" | "other";
}

export interface ShiftShare {
  dimension: string;
  terms: { key: string; label: string; value: number; detail: string; share: number | null }[];
  total: number;
  residual: number;
  closes: boolean;
  largest: string | null;
}

export interface DriverBreakdown {
  column: string;
  categories: number;
  score: number;
  top_share: number;
  contributors: Contributor[];
  gained: Contributor[];
  lost: Contributor[];
  shift_share: ShiftShare;
}

export interface DriverResult {
  measure: string;
  aggregation: Aggregation;
  date_column: string;
  period: {
    mode: PeriodMode;
    baseline: { start: string; end: string; label: string };
    current: { start: string; end: string; label: string };
    baseline_rows: number;
    current_rows: number;
  };
  total: {
    baseline: number;
    current: number;
    change: number;
    change_pct: number | null;
    direction: "up" | "down" | "flat";
  };
  shift_share: ShiftShare | null;
  dimensions: DriverBreakdown[];
  best_dimension: string | null;
  options: DriverOptions;
  headline: string;
  narrative: string[];
  caveats: string[];
  follow_up: string;
  charts: ChartOutput[];
  tables: TableOutput[];
}

/* --- Cohorts & retention -------------------------------------------------- */

export type Granularity = "auto" | "day" | "week" | "month" | "quarter";

export interface CohortOptions {
  entities: { name: string; unique: number; rows_per_entity: number; role: string }[];
  date_columns: string[];
  measures: string[];
  granularities: Granularity[];
  defaults: {
    entity: string | null;
    date_column: string | null;
    measure: string | null;
    granularity: Granularity;
    periods: number;
  };
  available: boolean;
  reason: string | null;
}

export interface CohortQuery {
  entity?: string;
  date_column?: string;
  /** Explicit null means "count entities only, no value curve". */
  measure?: string | null;
  granularity?: Granularity;
  periods?: number;
  min_cohort_size?: number;
}

export interface CohortRow {
  cohort: number;
  label: string;
  start: string | null;
  size: number;
  observed_offsets: number;
  /** `null` at an offset the cohort has not lived long enough to have. Never zero. */
  active: (number | null)[];
  retention: (number | null)[];
  value: (number | null)[];
  value_per_entity: (number | null)[];
  revenue_retention: (number | null)[];
}

export interface CohortResult {
  entity: string;
  date_column: string;
  measure: string | null;
  granularity: Granularity;
  period_noun: string;
  horizon: number;
  cohorts: CohortRow[];
  folded: number;
  offsets: number[];
  curve: {
    offsets: number[];
    retention: (number | null)[];
    value_per_entity: (number | null)[];
    cumulative_value_per_entity: (number | null)[];
    cohorts_observed: number[];
    entities_observed: number[];
  };
  summary: {
    entities: number;
    cohorts: number;
    grain: string;
    repeat_rate: number | null;
    one_and_done_pct: number | null;
    median_active_periods: number | null;
    mean_active_periods: number | null;
    median_periods_to_return: number | null;
    retention_1: number | null;
    retention_3: number | null;
    retention_6: number | null;
    retention_12: number | null;
    benchmark_offset: number | null;
    best_cohort: { cohort: number; retention: number; size: number } | null;
    worst_cohort: { cohort: number; retention: number; size: number } | null;
    revenue_retention_1: number | null;
    value_per_entity_total: number | null;
  };
  coverage: {
    rows: number;
    rows_dropped: number;
    entities: number;
    periods: number;
    first_period: string;
    last_period: string;
    final_period_complete: boolean;
    min_cohort_size: number;
  };
  options: CohortOptions;
  headline: string;
  narrative: string[];
  caveats: string[];
  follow_up: string;
  charts: ChartOutput[];
  tables: TableOutput[];
}

/* --- Forecasting ---------------------------------------------------------- */

export interface ForecastMethod {
  id: string;
  label: string;
  detail: string;
}

export interface ForecastOptions {
  measures: { name: string; aggregation: Aggregation }[];
  date_columns: string[];
  granularities: Granularity[];
  methods: ForecastMethod[];
  defaults: {
    measure: string | null;
    date_column: string | null;
    aggregation: Aggregation;
    granularity: Granularity;
    horizon: number;
    method: string;
    interval: number;
  };
  available: boolean;
  reason: string | null;
}

export interface ForecastQuery {
  measure?: string;
  date_column?: string;
  aggregation?: Aggregation;
  granularity?: Granularity;
  horizon?: number;
  /** "auto" lets the walk-forward backtest pick. */
  method?: string;
  interval?: number;
}

export interface ForecastScore {
  method: string;
  label: string;
  detail: string;
  baseline: boolean;
  mae: number;
  rmse: number;
  mape: number | null;
  smape: number | null;
  /** Scaled against the naive baseline: 1.0 means "no better than doing nothing". */
  mase: number | null;
  points: number;
  bias: number;
  complete: boolean;
  by_step: { step: number; mae: number; points: number }[];
}

export interface ForecastPoint {
  period: string;
  value: number;
  lower: number;
  upper: number;
  step: number;
}

export interface ForecastResult {
  measure: string;
  date_column: string;
  aggregation: Aggregation;
  granularity: Granularity;
  period_noun: string;
  season_length: number;
  horizon: number;
  interval: number;
  method: string;
  method_label: string;
  method_detail: string;
  selection: string;
  history: { period: string; value: number }[];
  forecast: ForecastPoint[];
  accuracy: ForecastScore[];
  backtest: {
    folds: number;
    origins: number[];
    tested_points: number;
    interval_source: string;
  };
  verdict: {
    label: "useful" | "weak" | "no better" | "baseline";
    summary: string;
    baseline: string | null;
    baseline_label: string | null;
    baseline_mae: number | null;
    improvement: number | null;
    trustworthy: boolean;
  };
  totals: {
    projected: number;
    recent: number;
    recent_periods: number;
    change: number;
    change_pct: number | null;
    last_actual: number;
    next_period: number;
    direction: "up" | "down" | "flat";
  };
  coverage: {
    observations: number;
    first_period: string;
    last_period: string;
    rows_used: number;
    rows_dropped: number;
    empty_periods: number;
    trimmed_partial: string[];
  };
  options: ForecastOptions;
  headline: string;
  narrative: string[];
  caveats: string[];
  follow_up: string;
  charts: ChartOutput[];
  tables: TableOutput[];
}

/* --- Privacy guard -------------------------------------------------------- */

export type PrivacyAction = "keep" | "mask" | "hash" | "drop";
export type PrivacySeverity = "high" | "medium" | "low";
export type PrivacyStatus = "sensitive" | "review" | "clear" | "off";

export interface PrivacyFinding {
  column: string;
  kind: string;
  label: string;
  severity: PrivacySeverity;
  confidence: number;
  recommended: PrivacyAction;
  why: string;
  basis: string;
  match_rate: number | null;
  sampled: number;
  /** The structure of a value — letters as `a`, digits as `9`. Never a real value. */
  shape: string | null;
  missing_pct?: number;
  unique?: number;
}

export interface PrivacyScan {
  findings: PrivacyFinding[];
  counts: Record<PrivacySeverity, number>;
  sensitive_columns: string[];
  status: PrivacyStatus;
  headline: string;
  suggested_policy: Record<string, PrivacyAction>;
  scanned_columns: number;
}

export interface PrivacyState {
  policy: Record<string, PrivacyAction>;
  salt: string | null;
  applied_at: string | null;
  applied: {
    column: string;
    action: PrivacyAction;
    status: "applied" | "missing";
    affected?: number;
    detail: string;
  }[];
  raw_purged?: boolean;
}

export interface PrivacyReport {
  scan: PrivacyScan;
  state: PrivacyState;
  applied: boolean;
  suggested: boolean;
}

/* --- Significance testing ------------------------------------------------ */

export type SignificanceMode = "segments" | "periods";
export type MetricKind = "mean" | "proportion";
export type VerdictLabel = "real" | "noise" | "underpowered" | "inconclusive";

export interface SignificanceOptions {
  measures: { name: string; kind: MetricKind }[];
  dimensions: string[];
  date_columns: string[];
  defaults: {
    measure: string | null;
    dimension: string | null;
    date_column: string | null;
    mode: SignificanceMode;
  };
  available: boolean;
}

export interface SignificanceQuery {
  measure?: string;
  mode?: SignificanceMode;
  dimension?: string;
  group_a?: string;
  group_b?: string;
  date_column?: string;
  period?: PeriodMode;
  alpha?: number;
  scan?: boolean;
}

export interface TestGroup {
  label: string;
  n: number;
  value: number;
  sd: number;
  ci_low: number;
  ci_high: number;
  sum: number | null;
  small: boolean;
}

export interface StatisticalTest {
  id: string;
  name: string;
  statistic: number | null;
  df: number | null;
  p_value: number;
  detail: string;
  assumption: string;
}

export interface ScanRow {
  label: string;
  n: number;
  value: number;
  rest_value: number;
  difference: number;
  p_value: number;
  p_adjusted: number;
  significant: boolean;
  small: boolean;
}

export interface SignificanceResult {
  measure: string;
  metric_kind: MetricKind;
  mode: SignificanceMode;
  alpha: number;
  dimension: string | null;
  date_column: string | null;
  period: {
    mode: PeriodMode;
    baseline: { start: string; end: string; label: string };
    current: { start: string; end: string; label: string };
  } | null;
  groups: TestGroup[];
  difference: {
    absolute: number;
    relative: number | null;
    ci_low: number;
    ci_high: number;
    ci_method: string;
    crosses_zero: boolean | null;
    direction: "up" | "down" | "flat";
    kind: MetricKind;
  };
  tests: StatisticalTest[];
  primary_test: string;
  p_value: number;
  significant: boolean;
  effect: {
    cohens_d: number | null;
    hedges_g: number | null;
    cliffs_delta: number | null;
    pooled_sd: number | null;
    magnitude: "negligible" | "small" | "medium" | "large";
    scale: string;
  };
  power: {
    required_n_per_group: number | null;
    mde_standardised: number | null;
    mde_absolute: number | null;
    reference_effect: number;
    power_at_reference: number | null;
    target_power: number;
    adequate: boolean;
  };
  verdict: {
    label: VerdictLabel;
    tone: "good" | "warn" | "neutral";
    headline: string;
    detail: string;
  };
  scan: {
    dimension: string;
    measure: string;
    rows: ScanRow[];
    n_tests: number;
    n_significant: number;
    n_significant_uncorrected: number;
    method: string;
    truncated: boolean;
  } | null;
  narrative: string[];
  caveats: string[];
  charts: ChartOutput[];
  tables: TableOutput[];
  follow_up: string;
  options: SignificanceOptions;
}

/* --- Scenario planning --------------------------------------------------- */

export type LeverKind = "rate" | "volume";

export interface SegmentLever {
  volume_pct?: number;
  rate_pct?: number;
  share_points?: number;
}

export interface Levers {
  global?: SegmentLever;
  segments?: Record<string, SegmentLever>;
}

export interface ScenarioQuery {
  measure?: string;
  dimension?: string;
  date_column?: string;
  aggregation?: Aggregation;
  period?: PeriodMode;
  levers?: Levers;
}

export interface ScenarioSegment {
  label: string;
  baseline_value: number;
  scenario_value: number;
  change: number;
  change_pct: number | null;
  baseline_rows: number;
  scenario_rows: number;
  baseline_average: number;
  scenario_average: number;
  baseline_share: number;
  scenario_share: number;
  share_change: number;
}

export interface Sensitivity {
  label: string;
  share: number;
  rate_gain_per_pct: number;
  volume_gain_per_pct: number;
  leverage: number | null;
  rank: number;
}

export interface ScenarioResult {
  measure: string;
  dimension: string;
  date_column: string | null;
  aggregation: Aggregation;
  window: { mode: string; start: string; end: string; label: string } | null;
  levers: { global: SegmentLever; segments: Record<string, SegmentLever> };
  baseline: { value: number; rows: number };
  scenario: { value: number; rows: number };
  change: { absolute: number; pct: number | null; direction: "up" | "down" | "flat" };
  segments: ScenarioSegment[];
  shift_share: ShiftShare;
  sensitivity: Sensitivity[];
  headline: string;
  narrative: string[];
  caveats: string[];
  charts: ChartOutput[];
  tables: TableOutput[];
  follow_up: string;
  options: DriverOptions & { levers: LeverKind[]; max_segments: number };
}

export interface GoalSeekResult {
  achievable: boolean;
  target: number;
  lever: { kind: LeverKind; segment: string | null };
  required_pct?: number;
  achieved?: number;
  reachable_range?: { min: number; max: number };
  message: string;
  scenario: ScenarioResult;
}

/* --- SQL sources --------------------------------------------------------- */

export interface Source {
  id: string;
  name: string;
  kind: string;
  dsn_redacted: string;
  query: string;
  dataset_id: string | null;
  dataset_name?: string | null;
  dataset_rows?: number | null;
  version?: number | null;
  refresh_minutes: number;
  enabled: boolean;
  last_status: "ok" | "error" | null;
  last_error: string | null;
  last_synced_at: string | null;
  last_row_count: number | null;
  sync_count: number;
  created_at: string;
  updated_at: string;
}

export interface Dialect {
  kind: string;
  driver: string;
  package: string;
  label: string;
}

export interface SourceTest {
  ok: boolean;
  kind: string;
  columns: string[];
  row_sample: number;
  preview: { columns: string[]; rows: unknown[][] };
}

/* --- Collaboration ------------------------------------------------------- */

export type CommentSubject = "session" | "board" | "message" | "board_item";

export interface Comment {
  id: string;
  subject_kind: CommentSubject;
  subject_id: string;
  parent_id: string | null;
  author: string;
  body: string;
  resolved: boolean;
  created_at: string;
  updated_at: string;
}

export interface CommentThread extends Comment {
  replies: Comment[];
}

export interface CommentListing {
  subject_kind: CommentSubject;
  subject_id: string;
  threads: CommentThread[];
  open_count: number;
  total: number;
}

export interface ActivityEntry {
  id: string;
  actor: string;
  action: string;
  subject_kind: string | null;
  subject_id: string | null;
  subject_title: string | null;
  detail: string | null;
  created_at: string;
  icon: string;
  summary: string;
}

/* --- Scheduled briefings -------------------------------------------------- */

export interface Briefing {
  id: string;
  title: string;
  dataset_id: string;
  dataset_name?: string | null;
  question: string;
  schedule_hours: number;
  enabled: boolean;
  deliver: boolean;
  last_run_at: string | null;
  last_status: "ok" | "error" | null;
  last_session_id: string | null;
  last_message_id: string | null;
  last_headline: string | null;
  last_error: string | null;
  run_count: number;
  next_run_at: string | null;
  due: boolean;
  created_at: string;
  updated_at: string;
}

/* --- Analysis memory ------------------------------------------------------ */

export interface RecallMatch {
  message_id: string;
  session_id: string;
  session_title: string;
  dataset_id: string;
  dataset_name: string;
  created_at: string;
  question: string;
  headline: string;
  kpis: { label: string; value: unknown; format: KpiFormat }[];
  verification_score: number | null;
  similarity: number;
  matched_terms: string[];
  duplicate: boolean;
}

export interface RecallSummary {
  matches: RecallMatch[];
  duplicate: boolean;
  headline: string;
}

export interface RouteMatch {
  dataset_id: string;
  name: string;
  n_rows: number;
  score: number;
  matched_terms: string[];
  unmatched_terms: string[];
  confident?: boolean;
}

export interface RouteResult {
  question: string;
  matches: RouteMatch[];
  best: RouteMatch | null;
  confident: boolean;
}

/* --- Alert delivery ------------------------------------------------------ */

export type AlertKind = "slack" | "webhook" | "email";
export type AlertEvent = "breach" | "recovery" | "failure" | "contract" | "digest" | "briefing";

export interface AlertChannel {
  id: string;
  name: string;
  kind: AlertKind;
  target: string;
  events: AlertEvent[];
  enabled: boolean;
  last_status: "sent" | "failed" | null;
  last_error: string | null;
  last_sent_at: string | null;
  sent_count: number;
  created_at: string;
  updated_at: string;
}

export interface AlertDelivery {
  id: string;
  channel_id: string | null;
  channel_name?: string | null;
  channel_kind?: AlertKind | null;
  event: string;
  title: string;
  status: "sent" | "failed";
  detail: string | null;
  created_at: string;
}

export interface AlertSettings {
  enabled: boolean;
  events: AlertEvent[];
  email_configured: boolean;
  digest_hours: number;
  channels: AlertChannel[];
  deliveries: AlertDelivery[];
}

export type ColumnRole = "measure" | "dimension" | "identifier" | "datetime" | "boolean" | "text";

export interface ColumnProfile {
  name: string;
  dtype: "integer" | "decimal" | "text" | "datetime" | "boolean";
  role: ColumnRole;
  missing: number;
  missing_pct: number;
  unique: number;
  stats?: Record<string, number | string | null>;
  top_values?: { value: string | number | boolean | null; count: number }[];
  sample_values?: unknown[];
  avg_length?: number;
  /** Set by the privacy guard: example values are withheld from the profile and prompts. */
  sensitive?: boolean;
}

export interface DatasetProfile {
  n_rows: number;
  n_columns: number;
  memory_mb: number;
  columns: ColumnProfile[];
  roles: Record<string, string[]>;
  date_range: { column: string; start: string; end: string } | null;
  highlights: { label: string; value: number | string | null; format: KpiFormat }[];
  sample_rows: { columns: string[]; rows: unknown[][] };
  suggested_questions: string[];
  signals?: Signal[];
  /** Columns whose values are kept out of every prompt. */
  withheld_columns?: string[];
}

export interface Signal {
  id: string;
  kind: "trend" | "seasonality" | "anomaly" | "concentration" | "mix_shift" | "correlation" | "quality";
  severity: "positive" | "negative" | "neutral" | "warning";
  title: string;
  detail: string;
  question: string;
  sparkline?: number[] | null;
  breakdown?: { label: string; share: number }[] | null;
  /** Pre-aims the deterministic drill-down at whatever this signal spotted. */
  explain?: { measure: string; dimension?: string } | null;
}

export interface CleaningAction {
  step: string;
  detail: string;
  column: string | null;
  affected: number;
}

export interface CleaningReport {
  rows_before: number;
  rows_after: number;
  columns_before: number;
  columns_after: number;
  actions: CleaningAction[];
  type_conversions: Record<string, string>;
  duplicates_removed: number;
  missing_cells: number;
  missing_cells_pct: number;
  missing_by_column: Record<string, number>;
  invalid_values_coerced: number;
  outliers: { column: string; count: number; lower_fence: number; upper_fence: number }[];
  quality_score: number;
  ingestion?: {
    encoding: string | null;
    delimiter: string | null;
    sheet_name: string | null;
    sheet_names: string[];
    skipped_malformed_lines: number;
  };
}

export interface DatasetSummary {
  id: string;
  name: string;
  original_filename: string;
  file_type: string;
  sheet_name: string | null;
  n_rows: number;
  n_cols: number;
  size_bytes: number;
  created_at: string;
  version?: number;
  version_count?: number;
  root_dataset_id?: string | null;
  parent_dataset_id?: string | null;
  contract_status?: ContractStatus | null;
}

export interface Dataset extends DatasetSummary {
  profile: DatasetProfile;
  cleaning: CleaningReport;
  semantics?: Semantics | null;
  version_diff?: VersionDiff | null;
  contract?: DataContract | null;
  contract_result?: ContractResult | null;
  privacy?: PrivacyState | null;
  privacy_scan?: PrivacyScan | null;
}

export interface Preview {
  columns: string[];
  rows: unknown[][];
  total_rows: number;
  offset: number;
  limit: number;
}

export interface SessionSummary {
  id: string;
  title: string;
  dataset_id: string;
  dataset_name: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  share_token?: string | null;
}

export interface Kpi {
  label: string;
  value: number | string | boolean | null;
  format: KpiFormat;
  delta: number | null;
  delta_label: string | null;
  higher_is_better: boolean;
  description: string | null;
}

export interface ChartOutput {
  title: string;
  caption: string | null;
  figure: { data: unknown[]; layout: Record<string, unknown> };
}

export interface TableOutput {
  title: string;
  columns: { name: string; kind: "number" | "datetime" | "text" | "boolean" }[];
  rows: unknown[][];
  total_rows: number;
  truncated: boolean;
}

export interface Execution {
  ok: boolean;
  stdout: string;
  error: string | null;
  error_type: string | null;
  kpis: Kpi[];
  charts: ChartOutput[];
  tables: TableOutput[];
  warnings: string[];
  duration_ms: number;
  timed_out: boolean;
  memory_exceeded: boolean;
}

export interface Insight {
  title: string;
  detail: string;
  sentiment: "positive" | "negative" | "neutral";
  /** Set by an investigation brief: how well the computed evidence supports it. */
  confidence?: "high" | "medium" | "low";
}

export interface InvestigationStep {
  question: string;
  why: string;
  ok: boolean;
  headline: string;
  message_id: string;
  verification_score: number | null;
  error: string | null;
}

export interface Investigation {
  objective: string;
  out_of_scope: string[];
  steps: InvestigationStep[];
  completed: number;
  total: number;
  open_questions: string[];
}

export interface Report {
  headline: string;
  answer_markdown: string;
  insights: Insight[];
  recommendations: string[];
  caveats: string[];
  follow_up_questions: string[];
}

export type VerificationSeverity = "high" | "medium" | "low";

export interface VerificationFinding {
  id: string;
  check: string;
  severity: VerificationSeverity;
  category: "grounding" | "data" | "method" | "definition" | "process";
  title: string;
  detail: string;
}

/** Deterministic audit of an answer against what the sandbox actually computed. */
export interface Verification {
  score: number | null;
  confidence: "high" | "medium" | "low" | "unverified";
  summary: string;
  findings: VerificationFinding[];
  checks: number;
}

export type MonitorDirection = "above" | "below" | "change_pct";
export type MonitorStatus = "ok" | "breached" | "error" | null;

export interface MonitorRun {
  id: string;
  monitor_id: string;
  dataset_id: string | null;
  value: number | null;
  previous_value: number | null;
  change_pct: number | null;
  status: "ok" | "breached" | "error";
  breached: boolean;
  detail: string | null;
  duration_ms: number | null;
  root_cause?: RootCause | null;
  created_at: string;
}

/** The deterministic drill-down a breach carries with it. Computed, never generated. */
export interface RootCause {
  status: "ok" | "unavailable";
  reason?: string;
  measure?: string;
  matched_on?: string;
  dimension?: string | null;
  headline?: string;
  total?: DriverResult["total"];
  period?: DriverResult["period"];
  contributors: {
    label: string;
    change: number;
    contribution_pct: number | null;
    share_change: number | null;
    status: string;
  }[];
  largest_term?: {
    key: string;
    label: string;
    value: number;
    share: number | null;
    detail: string;
  } | null;
  summary: string | null;
  follow_up?: string;
  /** Everything needed to reopen the full drill-down pre-aimed at this finding. */
  params?: DriverQuery;
}

export interface Monitor {
  id: string;
  title: string;
  dataset_id: string;
  dataset_name?: string | null;
  source_session_id: string | null;
  question: string | null;
  kpi_label: string;
  kpi_index: number;
  kpi_format: KpiFormat;
  direction: MonitorDirection;
  threshold: number;
  enabled: boolean;
  baseline_value: number | null;
  last_value: number | null;
  last_status: MonitorStatus;
  last_run_at: string | null;
  last_detail: string | null;
  last_change_pct: number | null;
  /** The most recent explanation, kept after recovery so the breach stays readable. */
  root_cause?: RootCause | null;
  formatted_value: string;
  rule: string;
  history: number[];
  runs: MonitorRun[];
  created_at: string;
  updated_at: string;
}

export interface MonitorDigest {
  total: number;
  counts: { ok: number; breached: number; error: number; pending: number };
  breached: Monitor[];
  monitors: Monitor[];
  interval_minutes: number;
}

export type StepStatus = "running" | "done" | "error";

export interface Step {
  id: string;
  label: string;
  status: StepStatus;
  detail?: string;
  attempt?: number;
  items?: string[];
}

export interface AssistantPayload {
  intent?: string | null;
  plan?: { restated_question?: string; assumptions?: string[]; steps?: string[] };
  report?: Report | null;
  steps?: Step[];
  code?: string;
  approach?: string;
  attempts?: number;
  attempt_log?: { attempt: number; ok: boolean; error: string | null; error_type: string | null }[];
  execution?: Execution;
  verification?: Verification;
  report_error?: { code: string; message: string };
  error?: { code: string; message: string };
  usage?: UsageSummary;
  /** Present on an investigation brief; its sub-analyses are ordinary messages. */
  investigation?: Investigation;
  title?: string;
  degraded?: boolean;
  /** Prior analyses close to this question, found before the work started. */
  recall?: RecallSummary;
}

export interface UsageSummary {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  models: string[];
  cost_usd: number | null;
}

export interface UsageReport {
  days: number;
  analyses: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  by_day: { date: string; analyses: number; cost_usd: number }[];
  budget: {
    enabled: boolean;
    limit_usd: number | null;
    spent_usd: number;
    remaining_usd: number | null;
    used_pct: number | null;
    exhausted: boolean;
    period_start: string;
    reset_at: string;
  };
}

export type PinKind = "kpi" | "chart" | "table" | "insight";

interface BoardItemBase {
  id: string;
  board_id: string;
  title: string;
  source_session_id: string | null;
  source_question: string | null;
  dataset_name: string | null;
  wide: boolean;
  position: number;
  created_at: string;
}

export type BoardItem = BoardItemBase &
  (
    | { kind: "kpi"; content: Kpi }
    | { kind: "chart"; content: ChartOutput }
    | { kind: "table"; content: TableOutput }
    | { kind: "insight"; content: Insight }
    | { kind: "note"; content: { text: string } }
  );

export interface BoardSummary {
  id: string;
  title: string;
  description: string;
  share_token: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface BoardDetail extends BoardSummary {
  items: BoardItem[];
}

export type SharedDocument =
  | { type: "session"; title: string; dataset_name: string; updated_at: string; messages: Message[] }
  | { type: "board"; title: string; description: string; updated_at: string; items: BoardItem[] };

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  payload: AssistantPayload | null;
  status: "complete" | "failed" | "error" | "cancelled";
  created_at: string;
}

export interface SessionDetail extends SessionSummary {
  messages: Message[];
}

export type ChatEvent =
  | { event: "session"; data: { id: string; title: string } }
  | { event: "user_message"; data: Message }
  | { event: "step"; data: Step }
  | { event: "recall"; data: RecallSummary }
  | { event: "assistant_message"; data: Message }
  | { event: "error"; data: { code: string; message: string; message_record?: Message } }
  | { event: "done"; data: Record<string, never> };
