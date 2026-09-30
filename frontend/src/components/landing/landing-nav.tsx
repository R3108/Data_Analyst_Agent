"use client";

import { ArrowRight, Menu, Moon, Sun, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Wordmark } from "@/components/brand";
import { SECTIONS } from "@/components/landing/sections";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";
import { useTheme } from "@/lib/theme";

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

/**
 * On phones, the primary action pinned to the bottom edge once the hero's own button has
 * scrolled away. It steps aside wherever the page already offers the action or ends:
 * any element marked `data-cta-zone`.
 */
export function StickyCta() {
  const action = usePrimaryAction();
  const [past, setPast] = useState(false);
  const [blocked, setBlocked] = useState(false);

  useEffect(() => {
    const onScroll = () => setPast(window.scrollY > window.innerHeight * 0.9);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    const visible = new Set<Element>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) visible.add(entry.target);
        else visible.delete(entry.target);
      }
      setBlocked(visible.size > 0);
    });
    document.querySelectorAll("[data-cta-zone]").forEach((zone) => observer.observe(zone));

    return () => {
      window.removeEventListener("scroll", onScroll);
      observer.disconnect();
    };
  }, []);

  const shown = past && !blocked;
  return (
    <div
      // `inert` while hidden: an off-screen link must not be reachable by keyboard.
      inert={!shown}
      className={cn(
        "fixed inset-x-0 bottom-0 z-40 border-t border-line bg-canvas/90 px-5 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-md transition-transform duration-300 sm:hidden",
        shown ? "translate-y-0" : "translate-y-full",
      )}
    >
      <Link href={action.href} className={cn(PRIMARY, "group h-11 w-full text-[15px]")}>
        {action.label}
        <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-1" />
      </Link>
    </div>
  );
}

/**
 * Light/dark switch. Both icons are rendered and the theme attribute picks one in CSS, so
 * the right icon is there from first paint rather than after the stored theme is read.
 */
function ThemeToggle({ className }: { className?: string }) {
  const { resolved, setMode } = useTheme();
  return (
    <button
      onClick={() => setMode(resolved === "dark" ? "light" : "dark")}
      aria-label="Toggle dark theme"
      aria-pressed={resolved === "dark"}
      className={cn(
        "group flex size-9 shrink-0 items-center justify-center rounded-lg text-ink-2 transition hover:bg-muted hover:text-ink",
        className,
      )}
    >
      <Moon className="size-[18px] transition-transform duration-500 group-hover:-rotate-12 dark:hidden" />
      <Sun className="hidden size-[18px] transition-transform duration-500 group-hover:rotate-45 dark:block" />
    </button>
  );
}

/** The in-page section currently crossing the middle of the viewport, as its `#href`. */
function useActiveSection(): string | null {
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        // Leaving first, then entering: when one section hands over to the next in the
        // same batch, the one arriving must win.
        for (const entry of entries) {
          if (!entry.isIntersecting) setActive((current) => (current === `#${entry.target.id}` ? null : current));
        }
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(`#${entry.target.id}`);
        }
      },
      // A thin band just above the middle of the screen: only one section can be in it.
      { rootMargin: "-40% 0px -55% 0px" },
    );
    for (const section of SECTIONS) {
      const el = document.querySelector(section.href);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, []);

  return active;
}

export function LandingNav() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const progress = useRef<HTMLDivElement>(null);
  const active = useActiveSection();
  const action = usePrimaryAction();
  const { status } = useSession();

  // The bar sits flat on the hero, and lifts off the page once content scrolls under it.
  // The reading-progress line is written straight to the element: a state update on
  // every scroll frame would re-render the whole bar.
  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 8);
      const range = document.documentElement.scrollHeight - window.innerHeight;
      const ratio = range > 0 ? Math.min(window.scrollY / range, 1) : 0;
      // `scale`, not `transform`: it has to replace the resting `scale-x-0`, not multiply it.
      if (progress.current) progress.current.style.scale = `${ratio.toFixed(4)} 1`;
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    // The menu only exists below `lg`; close it if the window grows past that.
    const wide = window.matchMedia("(width >= 64rem)");
    const onWide = () => wide.matches && setOpen(false);
    window.addEventListener("keydown", onKey);
    wide.addEventListener("change", onWide);
    return () => {
      window.removeEventListener("keydown", onKey);
      wide.removeEventListener("change", onWide);
    };
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
        <div className="hidden flex-1 items-center gap-1 lg:flex">
          {SECTIONS.map((section) => (
            <a
              key={section.href}
              href={section.href}
              aria-current={active === section.href ? "location" : undefined}
              className="relative rounded-lg px-3 py-2 text-[14px] text-ink-2 transition after:absolute after:inset-x-3 after:bottom-1 after:h-px after:origin-left after:scale-x-0 after:bg-accent after:transition-transform after:duration-300 hover:text-ink hover:after:scale-x-100 aria-[current]:text-ink aria-[current]:after:scale-x-100"
            >
              {section.label}
            </a>
          ))}
        </div>
        <ThemeToggle className="ml-auto" />
        <div className="hidden items-center gap-2 lg:flex">
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
          className="flex size-9 items-center justify-center rounded-lg text-ink-2 transition hover:bg-muted hover:text-ink lg:hidden"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </nav>
      {/* Reading progress, drawn along the bar's lower edge. */}
      <div
        ref={progress}
        aria-hidden="true"
        className="absolute inset-x-0 -bottom-px h-0.5 origin-left scale-x-0 bg-accent"
      />

      {open && (
        <div id="landing-menu" className="animate-fade border-t border-line bg-canvas px-5 pt-2 pb-5 lg:hidden">
          {SECTIONS.map((section) => (
            <a
              key={section.href}
              href={section.href}
              onClick={() => setOpen(false)}
              aria-current={active === section.href ? "location" : undefined}
              className="block rounded-lg px-2 py-2.5 text-[15px] text-ink-2 transition hover:bg-muted hover:text-ink aria-[current]:font-medium aria-[current]:text-ink"
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
