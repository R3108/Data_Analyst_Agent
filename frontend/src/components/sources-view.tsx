"use client";

import {
  CircleCheck,
  Database,
  KeyRound,
  Pause,
  Play,
  RefreshCw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { DataTable } from "@/components/chat/data-table";
import { useConfirm } from "@/components/ui/confirm";
import {
  Badge,
  Button,
  EmptyState,
  IconButton,
  SectionLabel,
  Select,
  TextInput,
  ViewHeader,
} from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { Dialect, Source, SourceTest } from "@/lib/types";

const REFRESH_CHOICES = [
  { value: "0", label: "Manual only" },
  { value: "60", label: "Hourly" },
  { value: "360", label: "Every 6 hours" },
  { value: "1440", label: "Daily" },
  { value: "10080", label: "Weekly" },
];

const PLACEHOLDERS: Record<string, string> = {
  postgresql: "postgresql+psycopg://user:password@host:5432/database",
  mysql: "mysql+pymysql://user:password@host:3306/database",
  mssql: "mssql+pyodbc://user:password@host/database?driver=ODBC+Driver+18+for+SQL+Server",
  duckdb: "duckdb:///path/to/warehouse.duckdb",
  sqlite: "sqlite:///path/to/warehouse.db",
};

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

/**
 * Connected databases. A source is one read-only query; syncing it files the result as
 * the next version of a dataset, so contracts, monitors and version diffs all apply to it
 * exactly as they do to an uploaded file.
 */
export function SourcesView({ onOpenDataset }: { onOpenDataset: (datasetId: string) => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [sources, setSources] = useState<Source[]>([]);
  const [dialects, setDialects] = useState<Dialect[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const payload = await api.listSources();
      setSources(payload.sources);
      setDialects(payload.dialects);
    } catch (error) {
      toast.error("Couldn't load sources", describe(error));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const sync = async (source: Source) => {
    setSyncing(source.id);
    try {
      const { dataset } = await api.syncSource(source.id);
      toast.success(
        `${dataset.name} v${dataset.version ?? 1} is ready`,
        `${dataset.n_rows.toLocaleString()} rows pulled and cleaned.`,
        { label: "Analyse it", onClick: () => onOpenDataset(dataset.id) },
      );
      await refresh();
    } catch (error) {
      toast.error("Sync failed", describe(error));
      await refresh();
    } finally {
      setSyncing(null);
    }
  };

  const remove = async (source: Source) => {
    const ok = await confirm({
      title: `Delete the connection “${source.name}”?`,
      body: "Scheduled refreshes stop. Datasets it already created are kept.",
      confirmLabel: "Delete connection",
    });
    if (!ok) return;
    try {
      await api.deleteSource(source.id);
      await refresh();
    } catch (error) {
      toast.error("Couldn't delete the source", describe(error));
    }
  };

  const toggle = async (source: Source) => {
    try {
      await api.updateSource(source.id, { enabled: !source.enabled });
      await refresh();
    } catch (error) {
      toast.error("Couldn't update the source", describe(error));
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 pt-8 pb-16 sm:px-6">
        <ViewHeader
          eyebrow="Sources"
          icon={<Database className="size-3.5" />}
          title="Connect a database"
          body="One read-only query becomes a dataset that refreshes itself. Each sync files the result as the next version, so the data contract is checked on arrival, every monitor re-runs, and the version diff tells you what changed. Put the join in the query — the database is better at it than we are."
        />

        <NewSource dialects={dialects} onCreated={refresh} />

        {loading ? (
          <div className="mt-6 h-32 animate-pulse rounded-xl bg-muted" />
        ) : sources.length === 0 ? (
          <div className="mt-6">
            <EmptyState
              icon={<Database className="size-5" />}
              title="No connections yet"
              body="Add one above. Credentials stay on the server and are never sent back to the browser — the connection string is always shown redacted."
            />
          </div>
        ) : (
          <div className="mt-6 space-y-3">
            {sources.map((source) => (
              <article
                key={source.id}
                className="rounded-xl border border-line bg-panel p-4 shadow-card"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-[15px] font-semibold text-ink">{source.name}</h3>
                      <Badge tone="neutral">{source.kind}</Badge>
                      {!source.enabled && <Badge tone="warn">Paused</Badge>}
                      {source.last_status === "ok" && (
                        <Badge tone="good">
                          <CircleCheck className="size-3" />
                          Synced
                        </Badge>
                      )}
                      {source.last_status === "error" && (
                        <Badge tone="bad">
                          <TriangleAlert className="size-3" />
                          Failed
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 truncate font-mono text-[11.5px] text-ink-3">
                      {source.dsn_redacted}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      onClick={() => void sync(source)}
                      loading={syncing === source.id}
                    >
                      <RefreshCw className="size-3.5" />
                      Sync now
                    </Button>
                    <IconButton
                      label={source.enabled ? "Pause scheduled refresh" : "Resume scheduled refresh"}
                      size="sm"
                      onClick={() => void toggle(source)}
                    >
                      {source.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                    </IconButton>
                    <IconButton
                      label="Delete this connection"
                      size="sm"
                      className="hover:bg-bad-soft hover:text-bad"
                      onClick={() => void remove(source)}
                    >
                      <Trash2 className="size-3.5" />
                    </IconButton>
                  </div>
                </div>

                <pre className="mt-2.5 max-h-28 overflow-auto rounded-lg bg-subtle p-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-ink-2">
                  {source.query}
                </pre>

                <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-ink-3">
                  <span>
                    Refresh:{" "}
                    {source.refresh_minutes
                      ? REFRESH_CHOICES.find((c) => c.value === String(source.refresh_minutes))
                          ?.label ?? `every ${source.refresh_minutes} min`
                      : "manual only"}
                  </span>
                  {source.last_synced_at && <span>Last sync {relativeTime(source.last_synced_at)}</span>}
                  {source.last_row_count !== null && (
                    <span className="tabular-nums">{source.last_row_count.toLocaleString()} rows</span>
                  )}
                  {source.sync_count > 0 && (
                    <span className="tabular-nums">{source.sync_count} syncs</span>
                  )}
                  {source.dataset_id && (
                    <button
                      onClick={() => onOpenDataset(source.dataset_id!)}
                      className="text-accent underline-offset-2 hover:underline"
                    >
                      Analyse {source.dataset_name}
                      {source.version ? ` v${source.version}` : ""}
                    </button>
                  )}
                </div>

                {source.last_error && (
                  <p className="mt-2.5 rounded-lg border border-bad/30 bg-bad-soft p-2.5 text-[12.5px] leading-relaxed text-ink">
                    {source.last_error}
                  </p>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Add a connection: test it first, then save. */
function NewSource({ dialects, onCreated }: { dialects: Dialect[]; onCreated: () => Promise<void> }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("postgresql");
  const [name, setName] = useState("");
  const [dsn, setDsn] = useState("");
  const [query, setQuery] = useState("SELECT * FROM ");
  const [refresh, setRefresh] = useState("0");
  const [tested, setTested] = useState<SourceTest | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);

  const reset = () => {
    setName("");
    setDsn("");
    setQuery("SELECT * FROM ");
    setTested(null);
    setOpen(false);
  };

  const test = async () => {
    setBusy("test");
    setTested(null);
    try {
      setTested(await api.testSource({ name: name || undefined, dsn, query }));
    } catch (error) {
      toast.error("Connection test failed", describe(error));
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy("save");
    try {
      await api.createSource({
        name: name || undefined,
        dsn,
        query,
        refresh_minutes: Number(refresh),
      });
      toast.success("Source connected", "Sync it to create the dataset.");
      await onCreated();
      reset();
    } catch (error) {
      toast.error("Couldn't save the source", describe(error));
    } finally {
      setBusy(null);
    }
  };

  if (!open) {
    return (
      <Button variant="primary" className="mt-6" onClick={() => setOpen(true)}>
        <Database className="size-4" />
        Connect a database
      </Button>
    );
  }

  const dialect = dialects.find((d) => d.kind === kind);

  return (
    <section className="mt-6 rounded-xl border border-line bg-panel p-4 shadow-card">
      <SectionLabel icon={<KeyRound className="size-3.5" />}>New connection</SectionLabel>
      <div className="grid gap-3 sm:grid-cols-2">
        <Select
          label="Database"
          value={kind}
          onChange={setKind}
          options={dialects.map((d) => ({ value: d.kind, label: d.label }))}
        />
        <TextInput
          label="Name"
          value={name}
          placeholder={dialect ? `${dialect.label} query` : "My warehouse"}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="mt-3">
        <TextInput
          label="Connection string"
          value={dsn}
          placeholder={PLACEHOLDERS[kind] ?? PLACEHOLDERS.postgresql}
          onChange={(event) => setDsn(event.target.value)}
          className="font-mono text-[12px]"
          hint={
            dialect?.package
              ? `Stored on the server and never returned to the browser. Needs the ${dialect.package} package installed in the backend.`
              : "Stored on the server and never returned to the browser."
          }
        />
      </div>
      <label className="mt-3 flex flex-col gap-1">
        <span className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">
          Query — one read-only SELECT
        </span>
        <textarea
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          rows={5}
          spellCheck={false}
          className="w-full rounded-lg border border-line bg-panel p-2.5 font-mono text-[12.5px] leading-relaxed text-ink transition focus:border-accent focus:outline-none"
        />
        <span className="text-[11.5px] leading-snug text-ink-3">
          Joins, CTEs and aggregates are all fine — anything that writes is rejected before a
          connection is opened.
        </span>
      </label>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Select
          label="Auto-refresh"
          value={refresh}
          onChange={setRefresh}
          options={REFRESH_CHOICES}
        />
        <Button onClick={() => void test()} loading={busy === "test"} disabled={!dsn || !query}>
          Test connection
        </Button>
        <Button
          variant="primary"
          onClick={() => void save()}
          loading={busy === "save"}
          disabled={!dsn || !query}
        >
          Save source
        </Button>
        <Button variant="ghost" onClick={reset}>
          Cancel
        </Button>
      </div>

      {tested && (
        <div className="mt-4 rounded-lg border border-good/30 bg-good-soft p-3">
          <p className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
            <CircleCheck className="size-4 text-good" />
            Connected — {tested.columns.length} columns, {tested.row_sample} sample rows
          </p>
          <DataTable
            className="mt-2.5"
            columns={tested.preview.columns.map((name) => ({ name }))}
            rows={tested.preview.rows.slice(0, 5)}
            maxHeight={200}
          />
        </div>
      )}
    </section>
  );
}
