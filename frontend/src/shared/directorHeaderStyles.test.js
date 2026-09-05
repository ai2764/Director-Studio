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

describe("Director header layout", () => {
  it("allows a wrapped model picker to increase the header height", () => {
    expect(
      resolvedGlobalProperty(
        ".director-chat-header.workspace-panel-header",
        "height",
      ),
    ).toBe("auto");
    expect(
      resolvedGlobalProperty(
        ".director-chat-header.workspace-panel-header",
        "min-height",
      ),
    ).toBe("72px");
    expect(
      resolvedGlobalProperty(
        ".director-chat-header.workspace-panel-header .director-chat-header-row",
        "height",
      ),
    ).toBe("auto");
    expect(
      resolvedGlobalProperty(
        ".director-chat-header.workspace-panel-header .director-chat-header-row",
        "min-height",
      ),
    ).toBe("72px");
  });
});
