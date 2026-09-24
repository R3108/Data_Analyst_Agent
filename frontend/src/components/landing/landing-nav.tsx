"use client";

import { ArrowRight, Menu, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Wordmark } from "@/components/brand";
import { SECTIONS } from "@/components/landing/sections";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";

/**
 * The primary call to action, which depends on who is looking: a signed-in visitor goes
 * back to their workspace, and on an invite-only deployment "Get started" would lead to
 * a closed form, so it becomes "Sign in". Renders the signed-out default until the
 * session check lands, which keeps the page statically renderable.
 */
function usePrimaryAction(): { href: string; label: string } {
  const { status, config } = useSession();
  if (status === "authenticated") return { href: "/", label: "Open workspace" };
  if (config && !config.registration_enabled) return { href: "/login", label: "Sign in" };
  return { href: "/register", label: "Get started" };
}

const PRIMARY =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-accent font-medium text-white shadow-card transition hover:bg-accent-hover hover:shadow-pop active:scale-[0.98]";
const SECONDARY =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-line bg-panel font-medium text-ink shadow-card transition hover:bg-muted active:scale-[0.98]";

/** Hero and closing CTA pair. `inverted` sits on the accent-coloured band. */
export function PrimaryCta({ size = "lg", inverted = false }: { size?: "md" | "lg"; inverted?: boolean }) {
  const action = usePrimaryAction();
  const { status } = useSession();
  const dims = size === "lg" ? "h-11 px-5 text-[15px]" : "h-9 px-3.5 text-sm";
  return (
    <div className="flex flex-col items-stretch gap-2.5 sm:flex-row sm:items-center">
      <Link
        href={action.href}
        className={cn(
          "group",
          inverted
            ? "inline-flex items-center justify-center gap-2 rounded-lg bg-white font-medium text-[#1c5cab] shadow-card transition hover:bg-white/90 active:scale-[0.98]"
            : PRIMARY,
          dims,
        )}
      >
        {action.label}
        <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-1" />
      </Link>
      {status !== "authenticated" && action.href !== "/login" && (
        <Link
          href="/login"
          className={cn(
            inverted
              ? "inline-flex items-center justify-center gap-2 rounded-lg border border-white/30 font-medium text-white transition hover:bg-white/10"
              : SECONDARY,
            dims,
          )}
        >
          Sign in
        </Link>
      )}
    </div>
  );
}

export function LandingNav() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const action = usePrimaryAction();
  const { status } = useSession();

  // The bar sits flat on the hero, and lifts off the page once content scrolls under it.
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 border-b backdrop-blur-md transition-[background-color,border-color,box-shadow] duration-300",
        scrolled || open ? "border-line bg-canvas/80 shadow-card" : "border-transparent bg-canvas/0",
      )}
    >
      <nav className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-5 sm:px-8" aria-label="Main">
        <Link href="/home" aria-label="Numera home" className="shrink-0">
          <Wordmark />
        </Link>
        <div className="hidden flex-1 items-center gap-1 md:flex">
          {SECTIONS.map((section) => (
            <a
              key={section.href}
              href={section.href}
              className="relative rounded-lg px-3 py-2 text-[14px] text-ink-2 transition after:absolute after:inset-x-3 after:bottom-1 after:h-px after:origin-left after:scale-x-0 after:bg-accent after:transition-transform after:duration-300 hover:text-ink hover:after:scale-x-100"
            >
              {section.label}
            </a>
          ))}
        </div>
        <div className="ml-auto hidden items-center gap-2 md:flex">
          {status !== "authenticated" && action.href !== "/login" && (
            <Link
              href="/login"
              className="rounded-lg px-3 py-2 text-[14px] font-medium text-ink-2 transition hover:bg-muted hover:text-ink"
            >
              Sign in
            </Link>
          )}
          <Link href={action.href} className={cn(PRIMARY, "h-9 px-3.5 text-sm")}>
            {action.label}
          </Link>
        </div>
        <button
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls="landing-menu"
          aria-label={open ? "Close menu" : "Open menu"}
          className="ml-auto flex size-9 items-center justify-center rounded-lg text-ink-2 transition hover:bg-muted hover:text-ink md:hidden"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </nav>

      {open && (
        <div id="landing-menu" className="animate-fade border-t border-line bg-canvas px-5 pt-2 pb-5 md:hidden">
          {SECTIONS.map((section) => (
            <a
              key={section.href}
              href={section.href}
              onClick={() => setOpen(false)}
              className="block rounded-lg px-2 py-2.5 text-[15px] text-ink-2 transition hover:bg-muted hover:text-ink"
            >
              {section.label}
            </a>
          ))}
          <div className="mt-3 flex flex-col gap-2 border-t border-line pt-4">
            <Link href={action.href} className={cn(PRIMARY, "h-11 text-[15px]")}>
              {action.label}
            </Link>
            {status !== "authenticated" && action.href !== "/login" && (
              <Link href="/login" className={cn(SECONDARY, "h-11 text-[15px]")}>
                Sign in
              </Link>
            )}
          </div>
        </div>
      )}
    </header>
  );
}
