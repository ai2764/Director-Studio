export interface H3Mapping {
  h3_node_id: string;
  prompt_input: string;
  width_input: string;
  height_input: string;
  frames_input: string;
  picture_input_pattern: string;
  audio_input_pattern: string | null;
  seed_node_id: string;
  seed_input: string;
  saver_node_id: string;
  output_prefix_input: string;
  output_fields: string[];
}
export interface H3Issue {
  code: string;
  message: string;
  node_id?: string | null;
  input_name?: string | null;
}
export interface H3Candidate {
  node_id: string;
  class_type: string;
  title: string;
}
export interface H3Dependency {
  node_id: string;
  class_type: "LoadImage" | "LoadAudio";
  input_name: string;
  value: string;
}
export type H3LifecycleStatus =
  | "draft"
  | "mapped"
  | "validated"
  | "tested"
  | "active";
export interface H3Lifecycle {
  status: H3LifecycleStatus;
  workflow_sha256: string;
  mapping_sha256: string | null;
  validated_at: string | null;
  test_job_id: string | null;
}
export interface H3Analysis {
  import_id: string;
  workflow_sha256: string;
  compatibility: "auto_compatible" | "needs_confirmation" | "unsupported";
  mapping: H3Mapping | null;
  seed_candidates: H3Candidate[];
  saver_candidates: H3Candidate[];
  fixed_dependencies: H3Dependency[];
  issues: H3Issue[];
  agent_manifest: {
    nodes: (H3Candidate & {
      candidate_roles: string[];
      input_names: string[];
    })[];
  };
  lifecycle: H3Lifecycle;
}
export interface H3Proposal {
  import_id: string;
  mapping: H3Mapping;
  explanations: string[];
}
export interface H3Import {
  import_id: string;
  workflow_sha256: string;
  filename: string;
}
export interface H3Validation {
  import_id: string;
  workflow_sha256: string;
  valid: boolean;
  issues: H3Issue[];
  fixed_dependencies: H3Dependency[];
  validated_at: string;
}
export interface H3TestRun {
  import_id: string;
  job_id: string;
  job_url: string;
  workflow_sha256: string;
  mapping_sha256: string;
  status: "queued";
}
