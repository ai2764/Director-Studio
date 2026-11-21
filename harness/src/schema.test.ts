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

it("strips redundant JSON Schema title metadata while preserving object properties named title", () => {
  const converted = harnessSchema({
    type: "object",
    title: "ShotDraft",
    properties: {
      title: { type: "string", title: "Title", description: "Shot title" },
      duration_s: { type: "number", title: "Duration S" },
    },
    required: ["title"],
  });
  expect(converted).not.toHaveProperty("title");
  expect(converted.properties).toHaveProperty("title");
  expect(converted.properties.title).toEqual({
    type: "string",
    description: "Shot title",
  });
  expect(converted.properties.duration_s).toEqual({ type: "number" });
  expect(() => assertSupportedJsonSchema(converted)).not.toThrow();
});
