"use client";

import {
  Activity,
  Bell,
  CalendarClock,
  Database,
  FileSpreadsheet,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
  Search,
  SlidersHorizontal,
  Split,
  Sun,
  Trash,
  X,
} from "lucide-react";
import { useState } from "react";

import { ActivityFeed } from "@/components/activity-feed";
import { Wordmark } from "@/components/brand";
import { Button, IconButton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { formatCost, modelLabel, relativeTime } from "@/lib/format";
import { useTheme, type ThemeMode } from "@/lib/theme";
import type {
  BoardSummary,
  DatasetSummary,
  Health,
  Identity,
  MonitorDigest,
  SessionSummary,
  UsageReport,
} from "@/lib/types";
import type { WorkspaceView } from "@/components/workspace";

/** Navigation entries for the full-width views, in the order they are worked through. */
const VIEWS: {
  kind: WorkspaceView;
  label: string;
  icon: React.ReactNode;
  hint: string;
  needsDataset?: boolean;
}[] = [
  {
    kind: "drivers",
    label: "Drivers",
    icon: <Split className="size-3.5" />,
    hint: "Break a change into segment contributions, volume, mix and rate",
    needsDataset: true,
  },
  {
    kind: "significance",
    label: "Significance",
    icon: <FlaskConical className="size-3.5" />,
    hint: "Test whether a difference is real or just noise",
    needsDataset: true,
  },
  {
    kind: "scenarios",
    label: "Scenarios",
    icon: <SlidersHorizontal className="size-3.5" />,
    hint: "Model what would move the number, or solve for a target",
    needsDataset: true,
  },
  {
    kind: "sources",
    label: "Sources",
    icon: <Database className="size-3.5" />,
    hint: "Connect a SQL database and refresh it on a schedule",
  },
  {
    kind: "briefings",
    label: "Briefings",
    icon: <CalendarClock className="size-3.5" />,
    hint: "Save a question and have it answer itself on a cadence",
  },
];

export function Sidebar({
  open,
  onClose,
  health,
  healthError,
  usage,
  sessions,
  boards,
  datasets,
  activeSessionId,
  activeBoardId,
  activeView,
  identity,
  monitorDigest,
  onSelectSession,
  onSelectBoard,
  onOpenView,
  onNewAnalysis,
  onCreateBoard,
  onOpenPalette,
  onStartWithDataset,
  onDeleteSession,
  onDeleteBoard,
  onDeleteDataset,
}: {
  open: boolean;
  onClose: () => void;
  health: Health | null;
  healthError: string | null;
  usage: UsageReport | null;
  sessions: SessionSummary[];
  boards: BoardSummary[];
  datasets: DatasetSummary[];
  activeSessionId: string | null;
  activeBoardId: string | null;
  activeView: WorkspaceView | null;
  identity: Identity | null;
  monitorDigest: MonitorDigest | null;
  onSelectSession: (id: string) => void;
  onSelectBoard: (id: string) => void;
  onOpenView: (kind: WorkspaceView) => void;
  onNewAnalysis: () => void;
  onCreateBoard: () => void;
  onOpenPalette: () => void;
  onStartWithDataset: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onDeleteBoard: (id: string) => void;
  onDeleteDataset: (id: string) => void;
}) {
  const breaches = monitorDigest?.counts.breached ?? 0;
  const [showActivity, setShowActivity] = useState(false);
  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/30 lg:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        className={cn(
          "no-print fixed inset-y-0 left-0 z-40 flex w-[272px] shrink-0 flex-col border-r border-line bg-panel transition-transform duration-200 lg:static lg:translate-x-0",
          open ? "translate-x-0 shadow-pop" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between px-4">
          <Wordmark />
          <IconButton className="lg:hidden" label="Close menu" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>

        <div className="space-y-1.5 px-3 pt-1 pb-2">
          <Button className="w-full justify-start" onClick={onNewAnalysis}>
            <Plus className="size-4" />
            New analysis
          </Button>
          <button
            onClick={onOpenPalette}
            className="flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] text-ink-3 transition hover:bg-muted hover:text-ink"
          >
            <Search className="size-3.5" />
            <span className="flex-1 text-left">Search</span>
            <kbd className="rounded border border-line px-1.5 text-[10px]">Ctrl K</kbd>
          </button>
          {VIEWS.map((entry) => (
            <button
              key={entry.kind}
              onClick={() => onOpenView(entry.kind)}
              disabled={entry.needsDataset && datasets.length === 0}
              title={entry.hint}
              className={cn(
                "flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] transition disabled:opacity-40",
                activeView === entry.kind
                  ? "bg-muted font-medium text-ink"
                  : "text-ink-3 hover:bg-muted hover:text-ink",
              )}
            >
              {entry.icon}
              <span className="flex-1 text-left">{entry.label}</span>
            </button>
          ))}
          <button
            onClick={() => onOpenView("monitors")}
            title="Watch a KPI and get told when it moves — costs no model tokens"
            className={cn(
              "flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] transition",
              activeView === "monitors" ? "bg-muted font-medium text-ink" : "text-ink-3 hover:bg-muted hover:text-ink",
            )}
          >
            <Bell className={cn("size-3.5", breaches > 0 && "text-bad")} />
            <span className="flex-1 text-left">Monitors</span>
            {breaches > 0 ? (
              <span className="rounded-full bg-bad-soft px-1.5 text-[10.5px] font-semibold text-bad tabular-nums">
                {breaches}
              </span>
            ) : (
              monitorDigest !== null &&
              monitorDigest.total > 0 && (
                <span className="text-[10.5px] text-ink-3 tabular-nums">{monitorDigest.total}</span>
              )
            )}
          </button>
        </div>

        <nav className="min-h-0 flex-1 space-y-6 overflow-y-auto px-3 py-3" aria-label="History">
          <Section title="Analyses">
            {sessions.length === 0 ? (
              <p className="px-2 text-[13px] text-ink-3">Your analyses will appear here.</p>
            ) : (
              sessions.map((session) => (
                <Item
                  key={session.id}
                  active={session.id === activeSessionId}
                  icon={<MessageSquare className="size-3.5" />}
                  title={session.title}
                  subtitle={`${session.dataset_name} · ${relativeTime(session.updated_at)}`}
                  onClick={() => onSelectSession(session.id)}
                  onDelete={() => onDeleteSession(session.id)}
                  deleteLabel="Delete analysis"
                />
              ))
            )}
          </Section>

          <Section
            title="Boards"
            action={
              <IconButton label="New board" size="xs" onClick={onCreateBoard}>
                <Plus className="size-3.5" />
              </IconButton>
            }
          >
            {boards.length === 0 ? (
              <p className="px-2 text-[13px] leading-snug text-ink-3">Pin charts and KPIs to build a dashboard.</p>
            ) : (
              boards.map((board) => (
                <Item
                  key={board.id}
                  active={board.id === activeBoardId}
                  icon={<LayoutDashboard className="size-3.5" />}
                  title={board.title}
                  subtitle={`${board.item_count} item${board.item_count === 1 ? "" : "s"}${board.share_token ? " · shared" : ""}`}
                  onClick={() => onSelectBoard(board.id)}
                  onDelete={() => onDeleteBoard(board.id)}
                  deleteLabel="Delete board"
                />
              ))
            )}
          </Section>

          <Section title="Datasets">
            {datasets.length === 0 ? (
              <p className="px-2 text-[13px] text-ink-3">No datasets uploaded yet.</p>
            ) : (
              datasets.map((dataset) => (
                <Item
                  key={dataset.id}
                  icon={<FileSpreadsheet className="size-3.5" />}
                  title={dataset.name}
                  subtitle={`${dataset.n_rows.toLocaleString()} rows · ${dataset.n_cols} columns`}
                  onClick={() => onStartWithDataset(dataset.id)}
                  onDelete={() => onDeleteDataset(dataset.id)}
                  deleteLabel="Delete dataset"
                  tooltip="Start a new analysis with this dataset"
                />
              ))
            )}
          </Section>
        </nav>

        <div className="space-y-3 border-t border-line p-3">
          <button
            onClick={() => setShowActivity((value) => !value)}
            aria-expanded={showActivity}
            className="flex h-7 w-full items-center gap-2 rounded-lg px-1 text-[12px] text-ink-3 transition hover:bg-muted hover:text-ink"
          >
            <Activity className="size-3.5" />
            <span className="flex-1 text-left">{showActivity ? "Hide" : "Show"} workspace activity</span>
            {identity?.protected && (
              <span className="truncate text-[11px] text-ink-3" title="Signed in to this workspace">
                {identity.name}
              </span>
            )}
          </button>
          {showActivity && (
            <div className="max-h-60 overflow-y-auto rounded-lg border border-line bg-canvas/60 p-2">
              <ActivityFeed limit={30} />
            </div>
          )}
          <Status health={health} healthError={healthError} />
          {usage && <UsageMeter usage={usage} />}
          <ThemeSwitch />
        </div>
      </aside>
    </>
  );
}

function UsageMeter({ usage }: { usage: UsageReport }) {
  const budget = usage.budget;
  if (!budget.enabled || budget.limit_usd === null) {
    return (
      <div className="flex items-center justify-between px-1 text-[12px] text-ink-3">
        <span>Last {usage.days} days</span>
        <span className="tabular-nums">{usage.analyses} analyses · {formatCost(usage.cost_usd)}</span>
      </div>
    );
  }
  const percent = Math.min(budget.used_pct ?? 0, 100);
  return (
    <div
      className="rounded-lg border border-line bg-canvas/60 p-2.5"
      title={`${usage.input_tokens.toLocaleString()} input · ${usage.output_tokens.toLocaleString()} output tokens this reporting window`}
    >
      <div className="flex items-center gap-1.5 text-[11.5px] text-ink-2">
        <Gauge className="size-3.5 text-accent" />
        <span className="font-medium">Monthly AI budget</span>
        <span className="ml-auto tabular-nums">{Math.round(percent)}%</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            budget.exhausted ? "bg-bad" : percent >= 80 ? "bg-warn" : "bg-accent",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] text-ink-3">
        <span>{usage.analyses} analyses</span>
        <span className="tabular-nums">{formatCost(budget.spent_usd)} of ${budget.limit_usd.toFixed(2)}</span>
      </div>
    </div>
  );
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 flex h-6 items-center justify-between px-2">
        <p className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">{title}</p>
        {action}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function Item({
  active,
  icon,
  title,
  subtitle,
  onClick,
  onDelete,
  deleteLabel,
  tooltip,
}: {
  active?: boolean;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  onClick: () => void;
  onDelete: () => void;
  deleteLabel: string;
  tooltip?: string;
}) {
  return (
    <div className={cn("group relative rounded-lg transition-colors", active ? "bg-muted" : "hover:bg-muted/60")}>
      <button onClick={onClick} title={tooltip} className="flex w-full items-start gap-2.5 px-2 py-2 pr-9 text-left">
        <span className={cn("mt-0.5 shrink-0", active ? "text-accent" : "text-ink-3")}>{icon}</span>
        <span className="min-w-0 flex-1">
          <span className={cn("block truncate text-[13px]", active ? "font-medium text-ink" : "text-ink")}>{title}</span>
          <span className="block truncate text-[11.5px] text-ink-3">{subtitle}</span>
        </span>
      </button>
      <button
        onClick={onDelete}
        aria-label={deleteLabel}
        title={deleteLabel}
        className="absolute top-1/2 right-1.5 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-ink-3 opacity-0 transition group-hover:opacity-100 hover:bg-bad-soft hover:text-bad focus-visible:opacity-100"
      >
        <Trash className="size-3.5" />
      </button>
    </div>
  );
}

function Status({ health, healthError }: { health: Health | null; healthError: string | null }) {
  let dot = "bg-ink-3";
  let label = "Connecting…";
  if (healthError) {
    dot = "bg-bad";
    label = "Backend offline";
  } else if (health) {
    dot = health.llm_credentials_detected ? "bg-good" : "bg-warn";
    label = health.llm_credentials_detected ? modelLabel(health.model) : "API key missing";
  }
  return (
    <div className="flex items-center gap-2 px-1 text-[12px] text-ink-2">
      <span className={cn("size-2 rounded-full", dot)} />
      <span className="truncate">{label}</span>
      {health && <span className="ml-auto text-ink-3">v{health.version}</span>}
    </div>
  );
}

function ThemeSwitch() {
  const { mode, setMode } = useTheme();
  const options: { id: ThemeMode; icon: React.ReactNode; label: string }[] = [
    { id: "system", icon: <Monitor className="size-3.5" />, label: "System theme" },
    { id: "light", icon: <Sun className="size-3.5" />, label: "Light theme" },
    { id: "dark", icon: <Moon className="size-3.5" />, label: "Dark theme" },
  ];
  return (
    <div className="grid grid-cols-3 gap-1 rounded-lg bg-muted p-1" role="radiogroup" aria-label="Theme">
      {options.map((option) => (
        <button
          key={option.id}
          role="radio"
          aria-checked={mode === option.id}
          aria-label={option.label}
          title={option.label}
          onClick={() => setMode(option.id)}
          className={cn(
            "flex h-7 items-center justify-center rounded-md transition",
            mode === option.id ? "bg-panel text-ink shadow-card" : "text-ink-3 hover:text-ink",
          )}
        >
          {option.icon}
        </button>
      ))}
    </div>
  );
}
