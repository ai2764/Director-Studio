import { describe, expect, it } from "vitest";
import { NAV_ITEMS, navigationForMode } from "./navigation";

describe("NAV_ITEMS", () => {
  it("places Music between Assets and Director for MV projects", () => {
    expect(NAV_ITEMS).toEqual([
      { id: "assets", label: "Assets", step: "01" },
      { id: "music", label: "Music", step: "02" },
      { id: "director", label: "Director", step: "03" },
      { id: "production", label: "Production", step: "04" },
    ]);
    expect(navigationForMode("director").map((item) => item.step)).toEqual(["01", "02", "03"]);
    expect(navigationForMode("mv").map((item) => item.step)).toEqual(["01", "02", "03", "04"]);
  });
});
