// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, it } from "vitest";

const styles = readFileSync(
  resolve(process.cwd(), "src/shared/styles.css"),
  "utf8",
);

afterEach(() => {
  document.head.replaceChildren();
  document.body.replaceChildren();
});

it("renders Director errors with readable dark-red text on the light canvas", () => {
  const style = document.createElement("style");
  style.textContent = styles;
  document.head.append(style);
  expect(style.sheet?.cssRules.length).toBeGreaterThan(0);
  const app = document.createElement("main");
  app.className = "director-shots-panel";
  const banner = document.createElement("div");
  banner.className = "banner error";
  const status = document.createElement("span");
  status.className = "status-chip status-blocked";
  app.append(banner, status);
  document.body.append(app);

  expect(getComputedStyle(banner).color).toBe("rgb(116, 58, 52)");
  expect(getComputedStyle(status).color).toBe("rgb(116, 58, 52)");
});

it("renders the clapperboard frame with muted, side-first stripe layers", () => {
  const style = document.createElement("style");
  style.textContent = styles;
  document.head.append(style);
  const panel = document.createElement("div");
  panel.className = "shot-detail-panel shot-detail-panel-clapperboard";
  document.body.append(panel);

  const rule = Array.from(style.sheet?.cssRules ?? []).find(
    (candidate) => candidate.selectorText === ".shot-detail-panel-clapperboard",
  );
  const background = rule?.style.background ?? "";
  expect(background.match(/repeating-linear-gradient/g)).toHaveLength(3);
  expect(background).toContain("#5b524a");
  expect(background).toContain("#d8cfc3");
  expect(background).toContain(
    "right / calc(var(--shot-document-gutter) + 15px) 100% no-repeat",
  );
  expect(background.indexOf(" left / ")).toBeLessThan(background.indexOf(" top / "));
  expect(background.indexOf(" right / ")).toBeLessThan(background.indexOf(" top / "));
});
