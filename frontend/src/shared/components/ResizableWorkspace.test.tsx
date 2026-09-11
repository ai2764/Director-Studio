// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ResizableWorkspace } from "./ResizableWorkspace";

describe("ResizableWorkspace", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  function renderSplit(storageKey = "test.split") {
    return render(
      <ResizableWorkspace
        storageKey={storageKey}
        primary={<div>Chat</div>}
        secondary={<div>Shots</div>}
        separatorLabel="Resize chat and Shots"
      />,
    );
  }

  it("starts at 55 percent and supports keyboard resizing", () => {
    renderSplit();
    const separator = screen.getByRole("separator", { name: "Resize chat and Shots" });

    expect(separator.getAttribute("aria-valuenow")).toBe("55");
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator.getAttribute("aria-valuenow")).toBe("57");
    expect(localStorage.getItem("test.split")).toBe("57");
  });

  it("clamps the primary pane between 34 and 66 percent", () => {
    renderSplit();
    const separator = screen.getByRole("separator");

    for (let i = 0; i < 30; i += 1) fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(separator.getAttribute("aria-valuenow")).toBe("34");
    for (let i = 0; i < 30; i += 1) fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator.getAttribute("aria-valuenow")).toBe("66");
  });

  it("restores persisted width and resets to 55 on double click", () => {
    localStorage.setItem("saved.split", "62");
    renderSplit("saved.split");
    const separator = screen.getByRole("separator");

    expect(separator.getAttribute("aria-valuenow")).toBe("62");
    fireEvent.doubleClick(separator);
    expect(separator.getAttribute("aria-valuenow")).toBe("55");
  });

  it("supports workspace-specific defaults and bounds", () => {
    render(
      <ResizableWorkspace
        storageKey="custom.split"
        primary={<div>Prompt</div>}
        secondary={<div>Inspector</div>}
        separatorLabel="Resize prompt and inspector"
        defaultSize={65}
        minSize={42}
        maxSize={76}
      />,
    );
    const separator = screen.getByRole("separator", { name: "Resize prompt and inspector" });

    expect(separator.getAttribute("aria-valuemin")).toBe("42");
    expect(separator.getAttribute("aria-valuemax")).toBe("76");
    expect(separator.getAttribute("aria-valuenow")).toBe("65");
    fireEvent.keyDown(separator, { key: "End" });
    expect(separator.getAttribute("aria-valuenow")).toBe("76");
    fireEvent.doubleClick(separator);
    expect(separator.getAttribute("aria-valuenow")).toBe("65");
  });
});
