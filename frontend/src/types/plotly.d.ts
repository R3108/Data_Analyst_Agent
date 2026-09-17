declare module "plotly.js-dist-min" {
  type Root = HTMLElement;
  export function react(root: Root, data: unknown[], layout?: object, config?: object): Promise<Root>;
  export function purge(root: Root): void;
  export const Plots: { resize(root: Root): void };
  const Plotly: {
    react: typeof react;
    purge: typeof purge;
    Plots: typeof Plots;
  };
  export default Plotly;
}
