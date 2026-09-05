import type { Shot } from "../../shared/api/types";

export function materialReviewMessage(shot: Shot, shotNumber: number): string {
  const index = String(shotNumber).padStart(2, "0");
  return (
    `Shot ${index} references changed for “${shot.title || shot.id}” (${shot.id}). `
    + "Review the current materials and rewrite its H3 prompt. "
    + "If a critical reference is missing or conflicting, ask one concrete question instead."
  );
}
