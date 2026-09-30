/**
 * The monthly-revenue chart drawn on the landing page: 30 months of history and a 6-month
 * forecast with a widening interval. Computed once at module load and shared by the hero
 * preview and the answer explorer, so both draw the same line.
 */

const HISTORY = 30;
const HORIZON = 6;
const W = 600;
const H = 170;
const PAD = 8;

// A deterministic trend-plus-seasonality series shaped like the sample's monthly revenue.
const series = Array.from({ length: HISTORY + HORIZON }, (_, i) => {
  const seasonal = 26 * Math.sin((2 * Math.PI * (i - 2)) / 12);
  const noise = i < HISTORY ? 7 * Math.sin(i * 1.7) + 4 * Math.cos(i * 2.9) : 0;
  return 205 + 1.3 * i + seasonal + noise;
});
const lo = Math.min(...series) - 30;
const hi = Math.max(...series) + 30;
const x = (i: number) => PAD + (i / (series.length - 1)) * (W - PAD * 2);
const y = (v: number) => H - PAD - ((v - lo) / (hi - lo)) * (H - PAD * 2);
const line = (points: [number, number][]) =>
  points.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");

const past = series.slice(0, HISTORY).map((v, i) => [x(i), y(v)] as [number, number]);
const future = series.slice(HISTORY - 1).map((v, i) => [x(HISTORY - 1 + i), y(v)] as [number, number]);
const band = series.slice(HISTORY - 1).map((v, i) => ({ i: HISTORY - 1 + i, spread: 4 + i * 5, v }));
const pastPath = line(past);
const [lastX, lastY] = past[past.length - 1];

export const FORECAST_CHART = {
  W,
  H,
  PAD,
  pastPath,
  areaPath: `${pastPath} L${x(HISTORY - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`,
  futurePath: line(future),
  bandPath:
    line(band.map(({ i, v, spread }) => [x(i), y(v + spread)])) +
    " " +
    band
      .slice()
      .reverse()
      .map(({ i, v, spread }) => `L${x(i).toFixed(1)},${y(v - spread).toFixed(1)}`)
      .join(" ") +
    " Z",
  /** The last actual point, where the forecast begins. */
  lastX,
  lastY,
};
