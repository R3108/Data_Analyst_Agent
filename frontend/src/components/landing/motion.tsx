"use client";

import { useEffect, useLayoutEffect, useRef } from "react";

import { cn } from "@/lib/cn";

const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * An element that leans toward the pointer in 3D, with a soft sheen where the light hits
 * (styles in globals.css). Only on devices with a real hover pointer and when motion is
 * welcome; everywhere else it renders as the plain element it wraps.
 */
export function Tilt({
  max = 6,
  glare = true,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { max?: number; glare?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion() || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      const box = el.getBoundingClientRect();
      const px = (event.clientX - box.left) / box.width;
      const py = (event.clientY - box.top) / box.height;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        el.dataset.tilting = "";
        el.style.setProperty("--rx", `${((0.5 - py) * max * 2).toFixed(2)}deg`);
        el.style.setProperty("--ry", `${((px - 0.5) * max * 2).toFixed(2)}deg`);
        el.style.setProperty("--gx", `${(px * 100).toFixed(1)}%`);
        el.style.setProperty("--gy", `${(py * 100).toFixed(1)}%`);
      });
    };
    const onLeave = () => {
      cancelAnimationFrame(frame);
      delete el.dataset.tilting;
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
    };
  }, [max]);

  return (
    <div ref={ref} {...props} className={cn("tilt", className)}>
      {children}
      {glare && <span aria-hidden="true" className="tilt-glare" />}
    </div>
  );
}

/**
 * Fades `[data-reveal-item]` elements up as they scroll into view (see globals.css).
 *
 * Nothing is hidden until this runs, so a page whose script never loads is simply static.
 * Items already on screen at that moment are marked "instant" rather than hidden and
 * re-shown, which would read as a flicker.
 */
export function RevealObserver() {
  useLayoutEffect(() => {
    if (prefersReducedMotion()) return;
    const root = document.documentElement;
    const items = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal-item]:not([data-shown])"));
    const fold = window.innerHeight;
    for (const item of items) {
      if (item.getBoundingClientRect().top < fold) item.dataset.shown = "instant";
    }
    root.dataset.reveal = "";

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          (entry.target as HTMLElement).dataset.shown = "";
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.15 },
    );
    items.filter((item) => item.dataset.shown === undefined).forEach((item) => observer.observe(item));

    return () => {
      observer.disconnect();
      delete root.dataset.reveal;
    };
  }, []);
  return null;
}

/**
 * A number that counts up to its value once it is on screen.
 *
 * The final value is what renders on the server and what a screen reader hears; only the
 * decorative copy animates. Inside an element marked `data-count-sync`, the count waits
 * for that element's own CSS entrance animation, so the two land together.
 */
export function CountUp({
  to,
  decimals = 0,
  prefix = "",
  suffix = "",
  delay = 0,
  duration = 1200,
}: {
  to: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  delay?: number;
  duration?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const final = `${prefix}${to.toFixed(decimals)}${suffix}`;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) return;
    const render = (value: number) => {
      el.textContent = `${prefix}${value.toFixed(decimals)}${suffix}`;
    };
    let frame = 0;
    let timer = 0;
    const run = (wait: number) => {
      timer = window.setTimeout(() => {
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min((now - start) / duration, 1);
          render(to * (1 - Math.pow(1 - t, 3)));
          if (t < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      }, wait);
    };

    render(0);
    let observer: IntersectionObserver | null = null;
    const host = el.closest<HTMLElement>("[data-count-sync]");
    const entrance = host?.getAnimations()[0];
    if (entrance && typeof entrance.currentTime === "number") {
      const entranceDelay = Number(entrance.effect?.getTiming().delay ?? 0);
      run(Math.max(0, entranceDelay - entrance.currentTime) + delay);
    } else {
      observer = new IntersectionObserver(
        ([entry]) => {
          if (!entry.isIntersecting) return;
          observer?.disconnect();
          run(delay);
        },
        { threshold: 0.6 },
      );
      observer.observe(el);
    }

    return () => {
      observer?.disconnect();
      window.clearTimeout(timer);
      cancelAnimationFrame(frame);
      el.textContent = final;
    };
  }, [to, decimals, prefix, suffix, delay, duration, final]);

  return (
    <>
      <span className="sr-only">{final}</span>
      <span ref={ref} aria-hidden="true">
        {final}
      </span>
    </>
  );
}
