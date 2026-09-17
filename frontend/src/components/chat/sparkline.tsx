import { cn } from "@/lib/cn";

const WIDTH = 120;
const HEIGHT = 36;

/** Compact trend line: de-emphasised ink for history, the accent marks the latest period. */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const range = Math.max(...values) - min || 1;
  const points = values.map((value, i) => ({
    x: (i / (values.length - 1)) * WIDTH,
    y: HEIGHT - 3 - ((value - min) / range) * (HEIGHT - 6),
  }));
  const line = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("");
  const last = points[points.length - 1];

  return (
    <div className={cn("relative h-9 w-full", className)} aria-hidden="true">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" className="h-full w-full overflow-visible">
        <path d={`${line}L${WIDTH},${HEIGHT}L0,${HEIGHT}Z`} fill="var(--accent)" opacity={0.08} />
        <path
          d={line}
          fill="none"
          stroke="var(--ink-3)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <span
        className="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-panel"
        style={{ left: "100%", top: `${(last.y / HEIGHT) * 100}%` }}
      />
    </div>
  );
}
