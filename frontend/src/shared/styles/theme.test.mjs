import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";
import { describe, expect, it } from "vitest";

const themeCss = readFileSync(
  resolve(process.cwd(), "src/shared/styles/theme.css"),
  "utf8",
);

function ruleDeclarations(selector) {
  const result = {};
  postcss.parse(themeCss).walkRules((rule) => {
    if (!rule.selectors.includes(selector)) return;
    rule.walkDecls((decl) => {
      result[decl.prop] = decl.value;
    });
  });
  return result;
}

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16) / 255);
  const linear = channels.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const a = relativeLuminance(first);
  const b = relativeLuminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

describe("Linear theme tokens", () => {
  it("does not import legacy CSS or bridge Oat & Walnut variables", () => {
    expect(themeCss).not.toContain("styles.css");
    expect(themeCss).not.toContain("@layer legacy");
    expect(themeCss).toContain('@import "tailwindcss"');
  });

  it("defines decoupled light and dark Linear surface, border, and status tokens", () => {
    const light = ruleDeclarations(":root");
    const dark = ruleDeclarations(".dark");

    for (const token of [
      "--ds-surface-canvas",
      "--ds-surface-panel",
      "--ds-surface-elevated",
      "--ds-surface-hover",
      "--ds-border-subtle",
      "--ds-border-strong",
      "--ds-text-primary",
      "--ds-text-secondary",
      "--ds-text-muted",
      "--ds-accent",
      "--ds-status-ok",
      "--ds-status-warn",
      "--ds-status-danger",
      "--ds-status-info",
    ]) {
      expect(light[token]).toBeDefined();
      expect(dark[token]).toBeDefined();
    }
  });

  it("maintains WCAG AA text contrast for primary, muted, and status colors in both themes", () => {
    const light = ruleDeclarations(":root");
    const dark = ruleDeclarations(".dark");

    expect(contrastRatio(light["--ds-text-primary"], light["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light["--ds-text-muted"], light["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light["--ds-status-ok"], light["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light["--ds-status-warn"], light["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light["--ds-status-danger"], light["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);

    expect(contrastRatio(dark["--ds-text-primary"], dark["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(dark["--ds-text-muted"], dark["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(dark["--ds-text-muted"], dark["--ds-surface-elevated"])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(dark["--ds-status-danger"], dark["--ds-surface-panel"])).toBeGreaterThanOrEqual(4.5);
  });
});
