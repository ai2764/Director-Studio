import { readFileSync } from "node:fs";
import postcss from "postcss";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function resolvedGlobalProperty(selector, property) {
  let value;
  postcss.parse(styles).walkRules((rule) => {
    if (!rule.selectors.includes(selector)) return;
    rule.walkDecls(property, (declaration) => {
      value = declaration.value;
    });
  });
  return value;
}

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16) / 255);
  const linear = channels.map((channel) => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const brighter = Math.max(relativeLuminance(first), relativeLuminance(second));
  const darker = Math.min(relativeLuminance(first), relativeLuminance(second));
  return (brighter + 0.05) / (darker + 0.05);
}

describe("error-state colors", () => {
  it.each([
    ".banner.error",
    ".status-chip.status-failed",
    ".status-chip.status-blocked",
    ".btn.danger",
    ".mode-chip.danger",
    ".gate-badge.bad",
  ])("uses readable dark text for %s on the light theme", (selector) => {
    expect(resolvedGlobalProperty(selector, "color")).toBe("var(--danger)");
  });

  it("keeps danger text at WCAG AA contrast against the light panel", () => {
    const danger = resolvedGlobalProperty(":root", "--danger");
    const panel = resolvedGlobalProperty(":root", "--bg-panel");

    expect(danger).toBeDefined();
    expect(panel).toBeDefined();
    expect(contrastRatio(danger, panel)).toBeGreaterThanOrEqual(4.5);
  });
});
