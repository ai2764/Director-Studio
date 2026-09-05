import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "./navigation";

describe("NAV_ITEMS", () => {
  it("exposes the three desktop workflow stages in production order", () => {
    expect(NAV_ITEMS).toEqual([
      { id: "assets", label: "Assets", step: "01" },
      { id: "director", label: "Director", step: "02" },
      { id: "production", label: "Production", step: "03" },
    ]);
  });
});
