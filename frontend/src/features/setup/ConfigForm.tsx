import { useState } from "react";
import { pick, useI18n } from "./i18n";
import type { ConfigResponse, Draft, FieldSchema, FieldValue, SecretAction } from "./types";

export type ConfigModel = {
  config: ConfigResponse;
  draft: Draft;
  setField: (key: string, value: unknown) => void;
  errors: Record<string, string>;
};

export function schemaOf(config: ConfigResponse, key: string): FieldSchema | undefined {
  return config.schema.fields.find((f) => f.key === key);
}

export function valueOf(config: ConfigResponse, key: string): FieldValue | undefined {
  return config.fields.find((f) => f.key === key);
}

/** Effective (draft-or-saved) value for non-secret fields. */
export function effective(model: Pick<ConfigModel, "config" | "draft">, key: string): unknown {
  if (key in model.draft) return model.draft[key];
  return valueOf(model.config, key)?.value;
}

function ApplyBadge({ apply }: { apply: FieldSchema["apply"] }) {
  const { t } = useI18n();
  const label = apply === "hot" ? t("hot") : apply === "comfy" ? t("comfyApply") : t("restart");
  return <span className={`ds-badge ds-badge-${apply}`}>{label}</span>;
}

function SecretInput({ field, value, draft, onChange }: {
  field: FieldSchema;
  value: FieldValue;
  draft: SecretAction | undefined;
  onChange: (action: SecretAction | undefined) => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const state = value.secret ?? { set: false };
  if (draft && draft.secret === "clear") {
    return (
      <div className="ds-secret">
        <span className="ds-muted">{t("secretWillClear")}</span>
        <button type="button" className="btn ghost sm" onClick={() => onChange(undefined)}>{t("secretKeep")}</button>
      </div>
    );
  }
  if (!editing && !(draft && draft.secret === "set")) {
    return (
      <div className="ds-secret">
        <span className={state.set ? "ds-secret-set" : "ds-muted"}>
          {state.set ? `${t("secretSaved")} ${state.hint ?? ""}` : t("secretNotSet")}
        </span>
        <button type="button" className="btn secondary sm" disabled={value.locked} onClick={() => setEditing(true)}>
          {t("secretReplace")}
        </button>
        {state.set ? (
          <button type="button" className="btn ghost sm" disabled={value.locked} onClick={() => onChange({ secret: "clear" })}>
            {t("secretClear")}
          </button>
        ) : null}
      </div>
    );
  }
  const text = draft && draft.secret === "set" ? draft.value : "";
  const common = {
    "aria-label": field.key,
    value: text,
    autoComplete: "off",
    spellCheck: false,
    onChange: (e: { target: { value: string } }) =>
      onChange(e.target.value ? { secret: "set", value: e.target.value } : undefined),
  };
  return (
    <div className="ds-secret">
      {field.multiline ? <textarea rows={3} {...common} /> : <input type="password" {...common} />}
      <button type="button" className="btn ghost sm" onClick={() => { setEditing(false); onChange(undefined); }}>
        {t("secretKeep")}
      </button>
    </div>
  );
}

export function FieldControl({ model, fieldKey }: { model: ConfigModel; fieldKey: string }) {
  const { lang, t } = useI18n();
  const field = schemaOf(model.config, fieldKey);
  const value = valueOf(model.config, fieldKey);
  if (!field || !value) return null;
  const current = effective(model, fieldKey);
  const dirty = fieldKey in model.draft;
  const disabled = value.locked;
  const set = (v: unknown) => model.setField(fieldKey, v);
  let control;
  if (field.secret) {
    control = (
      <SecretInput field={field} value={value} draft={model.draft[fieldKey] as SecretAction | undefined}
        onChange={(action) => set(action)} />
    );
  } else if (field.type === "bool") {
    control = (
      <input type="checkbox" aria-label={fieldKey} checked={Boolean(current)} disabled={disabled}
        onChange={(e) => set(e.target.checked)} />
    );
  } else if (field.type === "enum") {
    control = (
      <select aria-label={fieldKey} value={String(current ?? "")} disabled={disabled} onChange={(e) => set(e.target.value)}>
        {(field.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (field.type === "multi") {
    const selected = new Set(String(current ?? "").split(",").map((s) => s.trim()).filter(Boolean));
    control = (
      <div className="ds-multi">
        {(field.options ?? []).map((o) => (
          <label key={o} className="ds-check">
            <input type="checkbox" checked={selected.has(o)} disabled={disabled} onChange={(e) => {
              const next = new Set(selected);
              if (e.target.checked) next.add(o); else next.delete(o);
              set(Array.from(next).join(","));
            }} />
            {o}
          </label>
        ))}
      </div>
    );
  } else if (field.type === "int" || field.type === "float") {
    control = (
      <input type="number" aria-label={fieldKey} value={current === null || current === undefined ? "" : String(current)}
        min={field.min} max={field.max} step={field.type === "int" ? 1 : "any"} disabled={disabled}
        onChange={(e) => set(e.target.value === "" ? null : Number(e.target.value))} />
    );
  } else {
    control = (
      <input type="text" aria-label={fieldKey} value={String(current ?? "")} disabled={disabled} spellCheck={false}
        placeholder={value.default ? String(value.default) : ""} onChange={(e) => set(e.target.value)} />
    );
  }
  return (
    <div className={`ds-field${dirty ? " ds-field-dirty" : ""}${disabled ? " ds-field-locked" : ""}`} data-key={fieldKey}>
      <div className="ds-field-label">
        <label>{pick(field.label, lang)}</label>
        <ApplyBadge apply={field.apply} />
        {value.locked ? <span className="ds-badge ds-badge-locked" title={fieldKey}>🔒 {t("lockedEnv")}</span> : null}
        {value.pending_restart ? <span className="ds-badge ds-badge-restart">⟳ {t("restart")}</span> : null}
      </div>
      <div className="ds-field-control">{control}</div>
      {field.help ? <div className="ds-field-help">{pick(field.help, lang)}</div> : null}
      {model.errors[fieldKey] ? <div className="ds-field-error">{model.errors[fieldKey]}</div> : null}
    </div>
  );
}

export function FieldList({ model, keys }: { model: ConfigModel; keys: string[] }) {
  return <div className="ds-fields">{keys.map((k) => <FieldControl key={k} model={model} fieldKey={k} />)}</div>;
}

export function GroupForm({ model, group, tier }: { model: ConfigModel; group: string; tier?: "user" | "advanced" }) {
  const keys = model.config.schema.fields.filter((f) => f.group === group && (!tier || f.tier === tier)).map((f) => f.key);
  return <FieldList model={model} keys={keys} />;
}
