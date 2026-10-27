// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssetWorkspace } from "./AssetWorkspace";
const generators = vi.hoisted(() => ({ actor: vi.fn(), scene: vi.fn(), prop: vi.fn() }));
vi.mock("../casting/CastingPage", () => ({ CastingPage: generators.actor }));
vi.mock("../set/SetDesignPage", () => ({ SetDesignPage: generators.scene }));
vi.mock("../props/PropsPage", () => ({ PropsPage: generators.prop }));
vi.mock("../library/LibraryPage", () => ({ LibraryPage: ({ mobile }: { mobile?: boolean }) => <section aria-label="Reusable Library">{mobile ? "Mobile Library" : "Desktop Library"}</section> }));
afterEach(cleanup);
it("shows only the reusable Library and never mounts image generation workflows", () => {
  render(<AssetWorkspace />);
  expect(screen.getByRole("region", { name: "Reusable Library" })).toBeTruthy();
  expect(screen.getByText("Desktop Library")).toBeTruthy();
  expect(generators.actor).not.toHaveBeenCalled();
  expect(generators.scene).not.toHaveBeenCalled();
  expect(generators.prop).not.toHaveBeenCalled();
});
