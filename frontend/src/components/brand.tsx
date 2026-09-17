import { cn } from "@/lib/cn";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-8", className)} aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <rect x="8" y="17" width="3.5" height="7" rx="1.75" fill="white" opacity="0.55" />
      <rect x="14.25" y="12" width="3.5" height="12" rx="1.75" fill="white" opacity="0.8" />
      <rect x="20.5" y="8" width="3.5" height="16" rx="1.75" fill="white" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark />
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-ink">Numera</div>
        <div className="text-[11px] text-ink-3">AI Data Analyst</div>
      </div>
    </div>
  );
}
