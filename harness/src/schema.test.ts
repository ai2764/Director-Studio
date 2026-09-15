import { it, expect } from "vitest";
import { assertSupportedJsonSchema } from "@deepseek-ai/dsh-tools";
import { harnessSchema } from "./schema.js";
it("adapts Pydantic references and nullable unions for Harness", () => {
  const converted = harnessSchema({
    type: "object",
    properties: { item: { $ref: "#/$defs/Item" } },
    required: ["item"],
    $defs: {
      Item: { anyOf: [{ type: "string", minLength: 2 }, { type: "null" }] },
    },
  });
  expect(converted.properties.item).toEqual({
    oneOf: [{ type: "string" }, { type: "null" }],
  });
  expect(() => assertSupportedJsonSchema(converted)).not.toThrow();
});
