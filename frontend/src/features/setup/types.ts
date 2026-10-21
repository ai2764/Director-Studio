export type Lang = "zh" | "en";
export type Bilingual = { zh: string; en: string };

export type FieldSchema = {
  key: string;
  group: string;
  tier: "user" | "advanced";
  apply: "hot" | "comfy" | "restart";
  type: "str" | "int" | "float" | "bool" | "enum" | "multi" | "url" | "path";
  secret: boolean;
  label: Bilingual;
  help: Bilingual;
  options?: string[];
  min?: number;
  max?: number;
  multiline?: boolean;
};

export type SecretState = { set: boolean; hint?: string };

export type FieldValue = {
  key: string;
  source: "default" | "file" | "env" | "portable";
  locked: boolean;
  pending_restart: boolean;
  value?: unknown;
  default?: unknown;
  secret?: SecretState;
};

export type SecretAction = { secret: "keep" } | { secret: "clear" } | { secret: "set"; value: string };
export type Draft = Record<string, unknown>;

export type ComfyRuntimeStatus = {
  mode: string;
  effective_url: string;
  configured_url: string;
  pending_reconfigure: boolean;
  warnings: string[];
  error: string | null;
  managed?: {
    state: string;
    pid: number | null;
    restarts: number;
    last_error: string | null;
    uptime_sec: number | null;
    log_path: string;
    command: string;
  };
  proxy?: { url: string; remote: string; running: boolean; stats: Record<string, unknown> };
  models_dir?: string;
  log_tail?: string;
};

export type ConfigResponse = {
  revision: string;
  env_file: string;
  env_exists: boolean;
  fields: FieldValue[];
  restart_pending: string[];
  schema: { groups: Record<string, Bilingual>; fields: FieldSchema[] };
  comfy_runtime: ComfyRuntimeStatus;
};

export type PutResult = {
  revision: string;
  applied: string[];
  restart_required: string[];
  comfy: "applied" | "deferred" | null;
  warnings: string[];
};

export type DoctorCheck = {
  id: string;
  category: string;
  status: "ok" | "warn" | "fail" | "skip";
  title: Bilingual;
  detail?: string;
  fix?: Bilingual;
  optional?: boolean;
  missing_nodes?: { class_type: string; pack: string; fix: Bilingual; repo?: string | null; license?: string | null; bundled?: boolean | null; install_dir?: string }[];
  missing_models?: { file: string; folder: string; loader: string; expected_path: string }[];
};

export type DoctorReport = {
  generated_at: string;
  summary: Record<"ok" | "warn" | "fail" | "skip", number>;
  checks: DoctorCheck[];
  models_dir: string;
  comfy: { mode: string; url: string; reachable: boolean; version?: string };
};

export type SetupState = {
  completed: boolean;
  skipped?: boolean;
  legacy_env_detected: boolean;
  admin_allowed: boolean;
  supervisor_available: boolean;
  boot_id?: string;
  installed?: boolean;
  restart_pending?: string[];
};

export type CapabilityProbe = { supported: boolean | null; detail: string; latency_ms: number };
export type CapabilityResult = {
  ok: boolean;
  error?: string;
  model?: string;
  provider?: string;
  declared?: string[] | null;
  tools?: CapabilityProbe;
  vision?: CapabilityProbe;
  warnings?: string[];
  harness_ok?: boolean;
};

export type OllamaModel = {
  name: string;
  size?: number;
  modified_at?: string;
  family?: string;
  parameter_size?: string;
  quantization?: string;
  loaded?: boolean;
};

export type OllamaModels = {
  base_url: string;
  reachable?: boolean;
  error?: string;
  version?: string;
  models: OllamaModel[];
  suggested: { name: string; size: string; note: string }[];
  runtime: {
    mode: string;
    bundled_active: boolean;
    effective_url: string;
    available: boolean;
    models_dir: string;
    version: string | null;
    error: string | null;
    managed?: { state: string; pid: number | null; log_path: string };
  };
};

export type PullJob = {
  id: string;
  name: string;
  state: "running" | "success" | "error" | "cancelled";
  status: string;
  total: number;
  completed: number;
  percent: number | null;
  error: string | null;
  hint?: Bilingual | null;
};

export type TestResult = {
  ok: boolean;
  error?: string;
  models?: string[];
  latency_ms?: number;
  system?: { comfyui_version?: string; devices?: { name: string; type: string }[] };
  ws_ok?: boolean;
  note?: string;
  note_zh?: string;
  preview?: { ok: boolean; args?: string[]; error?: string; warnings?: string[] };
};
