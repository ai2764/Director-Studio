import type { DirectorExecutionProgress } from "./api";

export function acceptExecutionProgress(
  previous: DirectorExecutionProgress | null, incoming: DirectorExecutionProgress,
): DirectorExecutionProgress {
  if (!previous) return incoming;
  if (previous.run_id === incoming.run_id) {
    if (previous.revision != null && incoming.revision != null) {
      return incoming.revision > previous.revision ? incoming : previous;
    }
    return Date.parse(incoming.updated_at) >= Date.parse(previous.updated_at) ? incoming : previous;
  }
  if (previous.run_sequence != null && incoming.run_sequence != null) {
    return incoming.run_sequence > previous.run_sequence ? incoming : previous;
  }
  return Date.parse(incoming.started_at) > Date.parse(previous.started_at) ? incoming : previous;
}
