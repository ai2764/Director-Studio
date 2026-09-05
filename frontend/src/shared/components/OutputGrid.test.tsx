// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OutputSlot } from "../api/types";
import { OutputGrid } from "./OutputGrid";

const outputs: Record<string, OutputSlot> = {
  master: { key: "master", label: "Actor Master", url: "/master.png" },
  fullbody_threeview: {
    key: "fullbody_threeview",
    label: "Full-body Three-view",
    url: "/fullbody.png",
  },
  bust_threeview: {
    key: "bust_threeview",
    label: "Bust Three-view",
    url: "/bust.png",
  },
  asset_sheet: { key: "asset_sheet", label: "Asset Sheet", url: "/sheet.png" },
  wardrobe_ref: { key: "wardrobe_ref", label: "Wardrobe Reference", url: "/wardrobe.png" },
};

describe("OutputGrid hierarchy", () => {
  afterEach(cleanup);

  it("separates the final asset sheet from supporting and reference outputs", () => {
    render(
      <OutputGrid
        status="succeeded"
        outputs={outputs}
        mainKeys={["master", "fullbody_threeview", "bust_threeview", "asset_sheet"]}
        featuredKey="asset_sheet"
        showSecondary
        onOpen={vi.fn()}
      />,
    );

    expect(within(screen.getByRole("group", { name: "Final output" })).getByText("Asset Sheet")).toBeTruthy();
    expect(
      within(screen.getByRole("group", { name: "Supporting outputs" })).getAllByRole("article"),
    ).toHaveLength(3);
    expect(
      within(screen.getByRole("group", { name: "Reference input" })).getByText("Wardrobe Reference"),
    ).toBeTruthy();
  });

  it("opens an image from its thumbnail and keeps download as the only text action", () => {
    const onOpen = vi.fn();
    render(
      <OutputGrid
        status="succeeded"
        outputs={outputs}
        featuredKey="asset_sheet"
        onOpen={onOpen}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Asset Sheet" }));

    expect(onOpen).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Enlarge" })).toBeNull();
    expect(screen.getAllByRole("link", { name: "Download" })).toHaveLength(4);
  });
});
