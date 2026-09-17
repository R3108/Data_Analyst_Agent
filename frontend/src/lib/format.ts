import type { KpiFormat } from "./types";

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function formatValue(value: unknown, format: KpiFormat = "auto"): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value !== "number") return String(value);
  if (!Number.isFinite(value)) return "—";

  switch (format) {
    case "percent":
      return `${(value * 100).toFixed(Math.abs(value) < 0.1 ? 2 : 1)}%`;
    case "currency":
      return Math.abs(value) >= 100_000 ? `$${compact.format(value)}` : `$${decimal.format(value)}`;
    case "integer":
      return Math.abs(value) >= 1_000_000 ? compact.format(value) : integer.format(value);
    case "text":
      return String(value);
    default:
      if (Math.abs(value) >= 100_000) return compact.format(value);
      return Number.isInteger(value) ? integer.format(value) : decimal.format(value);
  }
}

export function formatDelta(delta: number): string {
  const pct = delta * 100;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${Math.abs(pct).toFixed(Math.abs(pct) < 10 ? 1 : 0)}%`;
}

export function formatCell(value: unknown, kind?: string): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isInteger(value) ? integer.format(value) : decimal.format(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string" && (kind === "datetime" || /^\d{4}-\d{2}-\d{2}T/.test(value))) {
    return value.endsWith("T00:00:00") ? value.slice(0, 10) : value.replace("T", " ").slice(0, 19);
  }
  return String(value);
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "";
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return "<$0.01";
  return usd < 100 ? `$${usd.toFixed(2)}` : `$${Math.round(usd).toLocaleString()}`;
}

export function formatTokens(tokens: number): string {
  return tokens >= 1000 ? `${compact.format(tokens)} tokens` : `${tokens} tokens`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function relativeTime(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function modelLabel(model: string): string {
  return model
    .replace(/^gpt-/, "GPT ")
    .replace(/-(\d+)-(\d+)$/, " $1.$2")
    .replace(/-(\d+)$/, " $1")
    .replace(/(^|\s)([a-z])/g, (_, s, c) => s + c.toUpperCase());
}
