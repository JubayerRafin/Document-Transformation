import { it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import axe from "axe-core";
import { LazyMotion, domAnimation } from "motion/react";
import { Landing } from "../src/components/landing";
import { UploadPanel } from "../src/components/upload-panel";
import { ApiClient } from "../src/lib/api";
import { worker } from "./fixtures";
// jsdom has no rendered color/geometry. Contrast and visual responsive behavior
// require a real browser; these checks cover DOM semantics, labels and ARIA.
const options = {
  rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
};
it("landing page has accessible document structure and link/button names", async () => {
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  }));
  const { container } = render(<Landing />);
  const result = await axe.run(container, options);
  expect(
    result.violations.map((v) => ({ id: v.id, nodes: v.nodes.length })),
  ).toEqual([]);
});
it("upload form has associated labels, accessible tabs and named controls", async () => {
  const { container } = render(
    <LazyMotion features={domAnimation}>
      <UploadPanel
        api={new ApiClient()}
        workers={[worker]}
        onSubmitted={vi.fn()}
        onServers={vi.fn()}
        onError={vi.fn()}
      />
    </LazyMotion>,
  );
  const result = await axe.run(container, options);
  expect(
    result.violations.map((v) => ({ id: v.id, nodes: v.nodes.length })),
  ).toEqual([]);
});
