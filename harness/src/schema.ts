/** Translate Pydantic's schema dialect into Harness's supported presentation subset.
 * Python retains the original schema and owns authoritative argument validation.
 */
export function harnessSchema(
  schema: Record<string, any>,
): Record<string, any> {
  const visit = (node: any, seen: Set<string>): any => {
    if (!node || typeof node !== "object") return {};
    if (node.$ref) {
      const ref = String(node.$ref);
      if (!ref.startsWith("#/$defs/") || seen.has(ref))
        throw new Error(`Unsupported tool schema reference: ${ref}`);
      const target = schema.$defs?.[ref.slice(8)];
      if (!target) throw new Error(`Missing tool schema reference: ${ref}`);
      return visit(target, new Set([...seen, ref]));
    }
    if (node.anyOf || node.oneOf)
      return {
        oneOf: (node.anyOf ?? node.oneOf).map((item: any) => visit(item, seen)),
      };
    if (Array.isArray(node.type))
      return {
        oneOf: node.type.map((type: string) => visit({ ...node, type }, seen)),
      };
    const result: Record<string, any> = {};
    for (const key of [
      "type",
      "description",
      "title",
      "default",
      "enum",
      "const",
      "examples",
    ])
      if (Object.hasOwn(node, key)) result[key] = node[key];
    if (node.type === "object") {
      result.properties = Object.fromEntries(
        Object.entries(node.properties ?? {}).map(([key, value]) => [
          key,
          visit(value, seen),
        ]),
      );
      if (node.required) result.required = node.required;
      if (typeof node.additionalProperties === "boolean")
        result.additionalProperties = node.additionalProperties;
    }
    if (node.type === "array" && node.items)
      result.items = visit(node.items, seen);
    return result;
  };
  return visit(schema, new Set());
}
