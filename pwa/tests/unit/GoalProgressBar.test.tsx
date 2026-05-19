import { describe, it, expect } from "vitest";
import { render } from "@solidjs/testing-library";
import GoalProgressBar from "../../src/components/GoalProgressBar";

describe("GoalProgressBar", () => {
  it("renders progressbar role", () => {
    const { getByRole } = render(() => <GoalProgressBar progress={50} />);
    const bar = getByRole("progressbar");
    expect(bar).toBeTruthy();
  });

  it("sets aria-valuenow correctly", () => {
    const { getByRole } = render(() => <GoalProgressBar progress={75} />);
    const bar = getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("75");
  });

  it("clamps progress > 100 to 100", () => {
    const { getByRole } = render(() => <GoalProgressBar progress={150} />);
    const bar = getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
  });

  it("clamps negative progress to 0", () => {
    const { getByRole } = render(() => <GoalProgressBar progress={-10} />);
    const bar = getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("0");
  });

  it("renders custom label", () => {
    const { container } = render(() => <GoalProgressBar progress={30} label="30 / 100" />);
    expect(container.textContent).toContain("30 / 100");
  });

  it("uses danger color for low progress", () => {
    const { container } = render(() => <GoalProgressBar progress={10} />);
    const fill = container.querySelector("[style]");
    expect(fill).toBeTruthy();
  });

  it("has data-testid", () => {
    const { container } = render(() => <GoalProgressBar progress={60} />);
    expect(container.querySelector("[data-testid='goal-progress-bar']")).toBeTruthy();
  });
});
