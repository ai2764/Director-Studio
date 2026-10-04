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

function mobileDirector() {
  const style = document.createElement("style");
  style.textContent = styles;
  document.head.append(style);
  document.body.innerHTML = `<div class="mobile-app">
    <header class="mobile-topbar"></header>
    <div class="mobile-page mobile-director-page">
      <main class="workspace director-chat-layout chat-only mobile-director-layout">
        <section class="mv-song-transport"></section>
        <section class="panel director-chat-panel">
          <div class="chat-log"></div><form class="chat-composer"></form>
        </section>
      </main>
    </div>
  </div>`;
  return {
    page: document.querySelector(".mobile-director-page"),
    layout: document.querySelector(".mobile-director-layout"),
    panel: document.querySelector(".director-chat-panel"),
    log: document.querySelector(".chat-log"),
  };
}

it("allows vertical swipes outside the chat log to scroll the mobile Director page", () => {
  const { page, layout, log } = mobileDirector();
  expect(getComputedStyle(page).overflowY).toBe("auto");
  expect(getComputedStyle(layout).overflow).toBe("visible");
  // At the ends of the chat history, swipes can continue into the outer page.
  expect(getComputedStyle(log).overscrollBehavior).toBe("auto");
});

it("keeps mobile chat usable when the song player makes the page taller than the viewport", () => {
  const { layout, panel } = mobileDirector();
  expect(getComputedStyle(layout).height).toBe("auto");
  expect(getComputedStyle(layout).flexShrink).toBe("0");
  expect(getComputedStyle(panel).flexShrink).toBe("0");
  expect(getComputedStyle(panel).minHeight).toBe("20rem");
  expect(getComputedStyle(panel).height).not.toBe("0px");
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
