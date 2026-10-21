// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { currentLang, pick, setLang } from "./i18n";

describe("UI language", () => {
  afterEach(() => window.localStorage.clear());

  it("defaults to English when nothing was chosen", () => {
    window.localStorage.clear();
    expect(currentLang()).toBe("en");
  });

  it("keeps Chinese only when the user picked it", () => {
    setLang("zh");
    expect(currentLang()).toBe("zh");
    setLang("en");
    expect(currentLang()).toBe("en");
  });

  it("falls back to English, not Chinese, for a missing translation", () => {
    expect(pick({ zh: "缺少", en: "" }, "en")).toBe("缺少");
    expect(pick({ zh: "缺少", en: "Missing" }, "en")).toBe("Missing");
    expect(pick({ zh: "", en: "Missing" }, "zh")).toBe("Missing");
  });
});
