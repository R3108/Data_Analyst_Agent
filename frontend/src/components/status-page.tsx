import { LogoMark } from "@/components/brand";

/**
 * The full-screen page shown when there is nothing else to render: a missing route or
 * an unexpected crash. Plain markup and theme tokens only, so it renders even when the
 * app around it could not.
 */
export function StatusPage({
  code,
  title,
  body,
  actions,
  detail,
}: {
  code: string;
  title: string;
  body: string;
  actions: React.ReactNode;
  detail?: React.ReactNode;
}) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6 py-10">
      <div className="w-full max-w-md text-center">
        <LogoMark className="mx-auto size-10" />
        <p className="mt-6 text-xs font-medium tracking-wide text-ink-3 uppercase">{code}</p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-balance text-ink">{title}</h1>
        <p className="mx-auto mt-2 max-w-sm text-[14px] leading-relaxed text-pretty text-ink-2">{body}</p>
        <div className="mt-6 flex flex-col items-center justify-center gap-2 sm:flex-row">{actions}</div>
        {detail && <div className="mt-6 text-[12px] text-ink-3">{detail}</div>}
      </div>
    </main>
  );
}

export const primaryLinkClass =
  "inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-accent px-3.5 text-sm font-medium text-white shadow-card transition hover:bg-accent-hover";
export const secondaryLinkClass =
  "inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-line bg-panel px-3.5 text-sm font-medium text-ink shadow-card transition hover:bg-muted";
