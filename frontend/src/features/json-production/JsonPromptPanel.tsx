import { EMPTY_PROMPT_SECTIONS, PROMPT_SECTION_KEYS } from "../../shared/api/types";
import type { PromptSections } from "../../shared/api/types";
import type { JsonProductionShot } from "./types";

type Props = {
  shot: JsonProductionShot;
  draftPrompt: PromptSections;
  promptDirty: boolean;
  busy: boolean;
  jsonEditing: boolean;
  shotJson: string;
  onChangePrompt: (next: PromptSections) => void;
  onSave: () => void;
  onEditJson: () => void;
  onChangeShotJson: (next: string) => void;
  onSaveShotJson: () => void;
  onCancelShotJson: () => void;
};

export function JsonPromptPanel({
  shot,
  draftPrompt,
  promptDirty,
  busy,
  jsonEditing,
  shotJson,
  onChangePrompt,
  onSave,
  onEditJson,
  onChangeShotJson,
  onSaveShotJson,
  onCancelShotJson,
}: Props) {
  return (
    <section className="section-card compact-card json-prompt-panel">
      <div className="shot-editor-header">
        <div>
          <h2 className="shot-editor-title">{shot.title || shot.id}</h2>
          <p className="shot-beat muted">{shot.script_beat}</p>
        </div>
        <div className="muted tiny">{shot.duration_s}s</div>
      </div>

      {jsonEditing ? (
        <>
          <label className="field json-shot-editor">
            <span>Shot JSON</span>
            <textarea
              rows={28}
              value={shotJson}
              disabled={busy}
              spellCheck={false}
              onChange={(e) => onChangeShotJson(e.target.value)}
            />
          </label>
          <p className="field-hint">
            Edit the complete shot object. Keep its id unchanged. Add references in pictures,
            then use matching &lt;Picture N&gt; tags in the prompt.
          </p>
          <div className="actions">
            <button type="button" className="btn primary" disabled={busy} onClick={onSaveShotJson}>
              Parse &amp; save
            </button>
            <button type="button" className="btn ghost" disabled={busy} onClick={onCancelShotJson}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
      <div className="json-dialogue-block">
        <div className="muted tiny">Dialogue</div>
        {shot.dialogue.length ? (
          <ul className="json-dialogue-list">
            {shot.dialogue.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="empty-copy">No dialogue.</p>
        )}
      </div>

      {PROMPT_SECTION_KEYS.map(({ key, label }) => (
        <label key={key} className="field">
          <span>{label}</span>
          <textarea
            rows={key === "detailed_description" ? 6 : 3}
            value={draftPrompt[key] ?? EMPTY_PROMPT_SECTIONS[key]}
            disabled={busy}
            onChange={(e) => onChangePrompt({ ...draftPrompt, [key]: e.target.value })}
          />
        </label>
      ))}

      <div className="actions">
        <button
          type="button"
          className="btn secondary"
          disabled={busy || !promptDirty}
          onClick={onSave}
        >
          Save prompt changes
        </button>
        <button type="button" className="btn ghost" disabled={busy} onClick={onEditJson}>
          Edit shot JSON
        </button>
      </div>
        </>
      )}
    </section>
  );
}
