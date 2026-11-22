import { Code2, Save } from "lucide-react";
import { EMPTY_PROMPT_SECTIONS, PROMPT_SECTION_KEYS } from "../../shared/api/types";
import type { PromptSections } from "../../shared/api/types";
import { Badge, Button, Panel, PanelBody, PanelHeader, Textarea } from "../../shared/ui";
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
    <Panel className="json-prompt-panel flex h-full flex-col overflow-hidden">
      <PanelHeader className="shot-editor-header items-start px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="shot-editor-title truncate text-sm font-semibold tracking-[-0.015em] text-ink">
            {shot.title || shot.id}
          </h2>
          <p className="shot-beat mt-0.5 text-xs leading-relaxed text-ink-secondary">
            {shot.script_beat}
          </p>
        </div>
        <Badge tone="neutral" className="shrink-0 font-mono tabular-nums">
          {shot.duration_s}s
        </Badge>
      </PanelHeader>

      <PanelBody className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
        {jsonEditing ? (
          <>
            <label className="json-shot-editor flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
              <span>Shot JSON</span>
              <Textarea
                rows={28}
                value={shotJson}
                disabled={busy}
                spellCheck={false}
                className="font-mono text-xs leading-relaxed"
                onChange={(e) => onChangeShotJson(e.target.value)}
              />
            </label>
            <p className="text-[11px] leading-relaxed text-ink-muted">
              Edit the complete shot object. Keep its id unchanged. Add references in pictures, then
              use matching &lt;Picture N&gt; tags in the prompt.
            </p>
            <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
              <Button type="button" variant="primary" size="sm" disabled={busy} onClick={onSaveShotJson}>
                Parse &amp; save
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={onCancelShotJson}
              >
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <>
            <div className="json-dialogue-block rounded-lg border border-border-subtle bg-elevated/40 px-3.5 py-2.5">
              <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-ink-muted">
                Dialogue
              </div>
              {shot.dialogue.length ? (
                <ul className="json-dialogue-list mt-1.5 flex flex-col gap-1 text-xs text-ink">
                  {shot.dialogue.map((line, index) => (
                    <li key={`${index}-${line}`}>{line}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-ink-muted">No dialogue.</p>
              )}
            </div>

            <div className="flex flex-col gap-3.5">
              {PROMPT_SECTION_KEYS.map(({ key, label }) => {
                const inputId = `json-prompt-${key}`;
                return (
                  <div key={key} className="flex flex-col gap-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <label
                        htmlFor={inputId}
                        className="text-xs font-medium text-ink-secondary"
                      >
                        {label}
                      </label>
                      <span className="font-mono text-[10px] text-ink-muted">{key}</span>
                    </div>
                    <Textarea
                      id={inputId}
                      rows={key === "detailed_description" ? 6 : 3}
                      value={draftPrompt[key] ?? EMPTY_PROMPT_SECTIONS[key]}
                      disabled={busy}
                      className="font-mono text-xs leading-relaxed"
                      onChange={(e) => onChangePrompt({ ...draftPrompt, [key]: e.target.value })}
                    />
                  </div>
                );
              })}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={busy || !promptDirty}
                onClick={onSave}
              >
                <Save className="h-3.5 w-3.5" aria-hidden="true" />
                Save prompt changes
              </Button>
              <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onEditJson}>
                <Code2 className="h-3.5 w-3.5" aria-hidden="true" />
                Edit shot JSON
              </Button>
            </div>
          </>
        )}
      </PanelBody>
    </Panel>
  );
}
