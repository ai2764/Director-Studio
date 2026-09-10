// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowSettingsPage } from "./WorkflowSettingsPage";

vi.mock("./H3WorkflowSetup", () => ({
  H3WorkflowSetup: () => <div>H3 setup</div>,
}));

describe("WorkflowSettingsPage", () => {
  afterEach(cleanup);

  it("exposes a labeled close action in the page header", () => {
    const onClose = vi.fn();
    render(<WorkflowSettingsPage onClose={onClose} />);

    const close = screen.getByRole("button", { name: "Close settings" });
    expect(close.closest(".page-shell-actions")).toBeTruthy();

    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
