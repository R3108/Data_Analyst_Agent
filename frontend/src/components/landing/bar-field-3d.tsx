/**
 * A small isometric 3D bar chart drawn with CSS 3D transforms alone (see `.bar3d` in
 * globals.css): a 5×5 field of bars on a gridded floor, rising in a wave toward the
 * tallest one when it scrolls into view, while the whole scene turns slowly. It is
 * decoration, so it is hidden from assistive technology.
 */

const N = 5;
const CELL = 38;
const BAR = 26;

// A deterministic landscape that climbs toward the far corner, with a little texture.
const BARS = Array.from({ length: N * N }, (_, index) => {
  const i = index % N;
  const j = Math.floor(index / N);
  const h = Math.round(22 + (i + j) * 13 + 12 * Math.sin(i * 1.7 + j * 0.9) + 6 * Math.cos(j * 2.3));
  return { i, j, h: Math.max(14, h) };
});
const PEAK = Math.max(...BARS.map((bar) => bar.h));

export function BarField3D({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={className}>
      <div className="bar-field flex items-center justify-center">
        <div className="bar-field-scene" style={{ width: N * CELL, height: N * CELL }}>
          <div className="bar-field-floor" />
          {BARS.map(({ i, j, h }) => (
            <div
              key={`${i}-${j}`}
              className="bar3d"
              data-peak={h === PEAK ? "" : undefined}
              style={
                {
                  left: i * CELL + (CELL - BAR) / 2,
                  top: j * CELL + (CELL - BAR) / 2,
                  width: BAR,
                  height: BAR,
                  "--h": `${h}px`,
                  "--d": `${(i + j) * 75}ms`,
                } as React.CSSProperties
              }
            >
              <span className="face-n" />
              <span className="face-w" />
              <span className="face-s" />
              <span className="face-e" />
              <span className="face-top" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
