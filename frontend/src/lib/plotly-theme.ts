/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = Record<string, any>;

/** Light → dark steps of the categorical palette (same hues, stepped for the dark surface). */
const LIGHT_TO_DARK: Record<string, string> = {
  "#2a78d6": "#3987e5",
  "#eb6834": "#d95926",
  "#1baf7a": "#199e70",
  "#eda100": "#c98500",
  "#e87ba4": "#d55181",
  "#008300": "#008300",
  "#4a3aa7": "#9085e9",
  "#e34948": "#e66767",
};

const INK = {
  light: { text: "#52514e", strong: "#0b0b0b", grid: "#e1e0d9", axis: "#c3c2b7", hoverBg: "#ffffff", hoverLine: "#e1e0d9" },
  dark: { text: "#c3c2b7", strong: "#ffffff", grid: "#2c2c2a", axis: "#383835", hoverBg: "#222220", hoverLine: "#383835" },
};

const FONT = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
const AXIS_KEY = /^[xy]axis\d*$/;

function remapColors(value: any): any {
  if (typeof value === "string") return LIGHT_TO_DARK[value.toLowerCase()] ?? value;
  if (Array.isArray(value)) return value.map(remapColors);
  if (value && typeof value === "object") {
    const out: Json = {};
    for (const [key, inner] of Object.entries(value)) out[key] = key === "bdata" ? inner : remapColors(inner);
    return out;
  }
  return value;
}

function styleAxis(axis: Json | undefined, ink: (typeof INK)["light"]): Json {
  const title = axis?.title;
  return {
    ...axis,
    gridcolor: ink.grid,
    linecolor: ink.axis,
    zerolinecolor: ink.axis,
    tickfont: { ...axis?.tickfont, color: ink.text, family: FONT },
    title:
      title && typeof title === "object"
        ? { ...title, font: { ...title.font, color: ink.text, family: FONT } }
        : title,
  };
}

function styleLayout(layout: Json, mode: "light" | "dark"): Json {
  const ink = INK[mode];
  const styled: Json = { ...layout };
  const axisKeys = Object.keys(styled).filter((k) => AXIS_KEY.test(k));
  for (const key of new Set([...axisKeys, "xaxis", "yaxis"])) styled[key] = styleAxis(styled[key], ink);
  styled.font = { ...styled.font, family: FONT, color: ink.text };
  styled.paper_bgcolor = "rgba(0,0,0,0)";
  styled.plot_bgcolor = "rgba(0,0,0,0)";
  styled.legend = { ...styled.legend, font: { ...styled.legend?.font, color: ink.text } };
  styled.hoverlabel = {
    ...styled.hoverlabel,
    bgcolor: ink.hoverBg,
    bordercolor: ink.hoverLine,
    font: { family: FONT, color: ink.strong, size: 12 },
  };
  if (Array.isArray(styled.annotations)) {
    styled.annotations = styled.annotations.map((a: Json) => ({ ...a, font: { color: ink.text, ...a.font } }));
  }
  return styled;
}

export function themeFigure(
  figure: { data: unknown[]; layout?: Json },
  mode: "light" | "dark",
  height: number,
): { data: unknown[]; layout: Json } {
  const source = structuredClone(figure);
  const data = mode === "dark" ? remapColors(source.data) : source.data;
  let layout: Json = source.layout ?? {};
  if (mode === "dark") layout = remapColors(layout);

  if (layout.template?.layout) {
    layout.template = { ...layout.template, layout: styleLayout(layout.template.layout, mode) };
  }
  layout = styleLayout(layout, mode);
  layout.autosize = true;
  layout.height = height;
  delete layout.width;
  delete layout.title;
  layout.margin = { l: 56, r: 16, t: 28, b: 44, ...layout.margin };
  return { data, layout };
}

export const PLOTLY_CONFIG = {
  displaylogo: false,
  responsive: false,
  displayModeBar: "hover",
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
  toImageButtonOptions: { format: "png", scale: 2 },
};
