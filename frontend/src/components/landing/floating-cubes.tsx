import { cn } from "@/lib/cn";

/**
 * A few wireframe cubes adrift at the edges of the hero, drawn with CSS 3D transforms
 * alone (see `.cube` in globals.css). Each tumbles and bobs at its own pace; with reduced
 * motion they are still. They sit in the margins beside the headline, so they only
 * appear where there is a margin to sit in, and they are hidden from assistive technology.
 */

const CUBES = [
  { position: "top-28 left-[4%]", size: 56, turn: 34, bob: 9, opacity: 0.8 },
  { position: "top-80 left-[9%]", size: 28, turn: 22, bob: 7, opacity: 0.55 },
  { position: "top-20 right-[6%]", size: 36, turn: 26, bob: 8, opacity: 0.6 },
  { position: "top-72 right-[3%]", size: 64, turn: 40, bob: 11, opacity: 0.8 },
];

export function FloatingCubes({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cn("pointer-events-none absolute inset-0 hidden lg:block", className)}>
      {CUBES.map(({ position, size, turn, bob, opacity }) => (
        <div
          key={position}
          className={cn("cube-float", position)}
          // Negative delays start each cube part-way through, so they never move in step.
          style={{ "--bob": `${bob}s`, animationDelay: `-${bob / 2}s`, opacity } as React.CSSProperties}
        >
          <div
            className="cube"
            style={{ "--s": `${size}px`, "--dur": `${turn}s`, animationDelay: `-${turn / 3}s` } as React.CSSProperties}
          >
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
        </div>
      ))}
    </div>
  );
}
