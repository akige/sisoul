import { describe, it, expect } from "vitest";
import { render } from "@solidjs/testing-library";
import ChartSimple from "../../src/components/ChartSimple";

describe("ChartSimple", () => {
  it("renders SVG element", () => {
    const { container } = render(() => (
      <ChartSimple data={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} />
    ));
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("renders polyline for 2+ data points", () => {
    const { container } = render(() => (
      <ChartSimple
        data={[
          { x: 0, y: 10 },
          { x: 1, y: 20 },
          { x: 2, y: 15 },
        ]}
      />
    ));
    expect(container.querySelector("polyline")).toBeTruthy();
  });

  it("shows 暂无数据 for empty data", () => {
    const { container } = render(() => <ChartSimple data={[]} />);
    expect(container.textContent).toContain("暂无数据");
  });

  it("shows 暂无数据 for single point", () => {
    const { container } = render(() => <ChartSimple data={[{ x: 0, y: 5 }]} />);
    expect(container.textContent).toContain("暂无数据");
  });

  it("respects custom width/height", () => {
    const { container } = render(() => (
      <ChartSimple
        data={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
        width={400}
        height={200}
      />
    ));
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("400");
    expect(svg?.getAttribute("height")).toBe("200");
  });

  it("sets aria-label", () => {
    const { container } = render(() => (
      <ChartSimple
        data={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
        label="goal progress chart"
      />
    ));
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("aria-label")).toBe("goal progress chart");
  });

  it("has data-testid", () => {
    const { container } = render(() => (
      <ChartSimple data={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} />
    ));
    expect(container.querySelector("[data-testid='chart-simple']")).toBeTruthy();
  });

  it("polyline has sisoul accent stroke color", () => {
    const { container } = render(() => (
      <ChartSimple data={[{ x: 0, y: 0 }, { x: 1, y: 5 }]} />
    ));
    const polyline = container.querySelector("polyline");
    expect(polyline?.getAttribute("stroke")).toBe("#7c9cff");
  });
});
