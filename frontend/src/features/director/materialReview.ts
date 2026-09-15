import type { Shot } from "../../shared/api/types";

export function materialReviewMessage(
  shot: Shot,
  shotNumber: number,
  userMessage = "",
): string {
  const index = String(shotNumber).padStart(2, "0");
  const delta = shot.meta?.material_changes || {
    added: [],
    removed: [],
    reordered: [],
  };
  const note = userMessage.trim() || "(none provided)";
  return (
    `Shot ${index} references changed for “${shot.title || shot.id}” (${shot.id}). `
    + "Review every current Picture reference and decide the next step. "
    + `Saved reference delta: ${JSON.stringify(delta)}. `
    + `User note: ${note}\n`
    + "Use the saved delta and user note together. Preserve the brief and H3 prompt when they remain valid; "
    + "if a critical reference is missing or conflicting, ask one concrete question. "
    + "Do not infer unrequested production actions."
  );
}
