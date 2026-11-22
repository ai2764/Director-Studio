import { createPortal } from "react-dom";
import { FileJson, Play, Square, Upload } from "lucide-react";
import { PageShell } from "../../shared/components/PageShell";
import { ResizableWorkspace } from "../../shared/components/ResizableWorkspace";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  Panel,
  PanelBody,
  Select,
  Textarea,
  buttonVariants,
  cn,
} from "../../shared/ui";
import type { H3Provider } from "../production/api";
import { JsonAssetSlots } from "./JsonAssetSlots";
import { JsonPromptPanel } from "./JsonPromptPanel";
import { JsonShotList } from "./JsonShotList";
import { useJsonProductionController } from "./useJsonProductionController";

type JsonProductionPageProps = {
  active?: boolean;
  mobile?: boolean;
  toolbarTarget?: HTMLElement | null;
};

export function JsonProductionPage({
  active = true,
  mobile = false,
  toolbarTarget,
}: JsonProductionPageProps = {}) {
  const ctrl = useJsonProductionController({ active });
  const {
    projectId,
    storyboard,
    selectedId,
    setSelectedId,
    selected,
    draftPrompt,
    setDraftPrompt,
    promptDirty,
    setPromptDirty,
    shotJsonEditing,
    setShotJsonEditing,
    shotJsonText,
    setShotJsonText,
    pasteText,
    setPasteText,
    error,
    setError,
    busy,
    picturePreviews,
    mobileSection,
    setMobileSection,
    desktopInspector,
    setDesktopInspector,
    h3ProviderStatus,
    h3Provider,
    setH3Provider,
    selectedFiles,
    readinessErrors,
    selectedGeneration,
    selectedJob,
    selectedJobActive,
    canGenerate,
    statusByShotId,
    hasShots,
    mobileReadinessText,
    importJsonText,
    onJsonFile,
    onPasteJson,
    onSavePrompt,
    onEditShotJson,
    onSaveShotJson,
    onPictureFile,
    onAudioFile,
    onGenerate,
    onCancel,
  } = ctrl;

  const providerPicker = (
    <label
      className={cn(
        "h3-provider-picker json-h3-provider-picker",
        mobile
          ? "flex flex-col gap-1 text-xs font-medium text-ink-secondary"
          : "flex items-center gap-2 text-xs text-ink-secondary",
      )}
    >
      <span className="shrink-0 text-[11px] font-medium text-ink-muted">H3 Runner</span>
      <Select
        aria-label="H3 provider"
        size={mobile ? "md" : "sm"}
        value={h3Provider}
        disabled={busy || selectedJobActive}
        wrapperClassName={mobile ? "w-full" : "w-44"}
        onChange={(event) => setH3Provider(event.target.value as H3Provider)}
      >
        <option value="local">Local · ComfyUI</option>
        <option value="minimax" disabled={!h3ProviderStatus?.minimax_configured}>
          MiniMax · Official API
          {h3ProviderStatus && !h3ProviderStatus.minimax_configured ? " · not configured" : ""}
        </option>
      </Select>
      <small
        className={cn(
          "text-[11px] text-ink-muted",
          !mobile && "hidden xl:inline",
        )}
      >
        {h3Provider === "minimax"
          ? `Official API · ${h3ProviderStatus?.minimax_resolution || "768P"}`
          : "Active local workflow"}
      </small>
    </label>
  );

  const jsonFilePicker = (
    <label
      className={cn(
        mobile
          ? "json-file-action"
          : "json-upload-control json-storyboard-upload",
        buttonVariants({ variant: "secondary", size: "sm" }),
      )}
    >
      <Upload className="h-3.5 w-3.5" aria-hidden="true" />
      <span>{mobile ? "Replace JSON" : "Import JSON"}</span>
      <input
        type="file"
        className="sr-only"
        aria-label="JSON file"
        accept=".json,application/json"
        disabled={!projectId || busy}
        onChange={(e) => {
          const file = e.target.files?.[0] || null;
          e.target.value = "";
          onJsonFile(file);
        }}
      />
    </label>
  );

  const desktopToolbar = !mobile ? (
    <div className="json-production-toolbar flex items-center gap-2.5">
      {providerPicker}
      {jsonFilePicker}
    </div>
  ) : null;

  const promptPanel = selected ? (
    <JsonPromptPanel
      shot={selected}
      draftPrompt={draftPrompt}
      promptDirty={promptDirty}
      busy={busy}
      jsonEditing={shotJsonEditing}
      shotJson={shotJsonText}
      onChangePrompt={(next) => {
        setDraftPrompt(next);
        setPromptDirty(true);
      }}
      onSave={() => void onSavePrompt()}
      onEditJson={onEditShotJson}
      onChangeShotJson={setShotJsonText}
      onSaveShotJson={() => void onSaveShotJson()}
      onCancelShotJson={() => {
        setShotJsonEditing(false);
        setShotJsonText("");
        setError(null);
      }}
    />
  ) : null;

  const assetPanel = selected ? (
    <JsonAssetSlots
      shot={selected}
      files={selectedFiles}
      picturePreviews={picturePreviews.get(selected.id) || new Map()}
      readinessErrors={readinessErrors}
      promptDirty={promptDirty}
      job={selectedJob}
      outputVersion={selectedGeneration?.version ?? null}
      busy={busy}
      onPictureFile={onPictureFile}
      onAudioFile={onAudioFile}
      onGenerate={() => {
        if (!mobile) setDesktopInspector("output");
        void onGenerate();
      }}
      onCancel={() => void onCancel()}
      view={mobile ? (mobileSection === "output" ? "output" : "references") : desktopInspector}
      showActions={!mobile}
    />
  ) : null;

  return (
    <>
      {!mobile && toolbarTarget ? (
        createPortal(desktopToolbar, toolbarTarget)
      ) : !mobile && toolbarTarget === undefined ? (
        <div className="json-production-toolbar-fallback flex items-center justify-end border-b border-border-subtle bg-panel/60 px-5 py-2">
          {desktopToolbar}
        </div>
      ) : null}

      <PageShell
        title={mobile ? "JSON Production" : "Production"}
        className="json-production-page"
        hideHeader={!mobile}
        subtitle={
          projectId ? (
            <>Import a storyboard JSON, attach Picture and Audio files, then Generate each shot on H3.</>
          ) : (
            "Select a project in the header."
          )
        }
        actions={
          mobile ? <div className="json-production-header-actions">{jsonFilePicker}</div> : null
        }
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {error ? <Banner tone="danger">{error}</Banner> : null}

          {!projectId ? (
            <EmptyState
              icon={<FileJson className="h-4 w-4" aria-hidden="true" />}
              title="No project selected"
              description="Select a project in the header."
              className="min-h-[280px]"
            />
          ) : !hasShots ? (
            <Panel className="json-empty-import mx-auto w-full max-w-2xl">
              <PanelBody className="flex flex-col gap-3.5 p-5">
                <p className="text-xs text-ink-secondary">
                  Empty storyboard. Choose a .json file or paste JSON to import shots.
                </p>
                <label className="flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                  <span>Paste JSON</span>
                  <Textarea
                    rows={12}
                    value={pasteText}
                    onChange={(e) => setPasteText(e.target.value)}
                    onPaste={onPasteJson}
                    spellCheck={false}
                    className="font-mono text-xs leading-relaxed"
                  />
                </label>
                <div className="flex items-center justify-between gap-2">
                  <span
                    className="json-paste-count font-mono text-[11px] text-ink-muted tabular-nums"
                    aria-live="polite"
                  >
                    {pasteText.length} characters
                  </span>
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={busy || !pasteText.trim()}
                    onClick={() => void importJsonText(pasteText)}
                  >
                    Import JSON
                  </Button>
                </div>
              </PanelBody>
            </Panel>
          ) : mobile ? (
            <div className="json-mobile-workspace flex flex-col gap-3">
              <div className="json-mobile-control-deck flex flex-col gap-2.5 rounded-lg border border-border-subtle bg-panel p-3">
                <div className="json-mobile-shot-bar flex items-end justify-between gap-2">
                  <label className="json-mobile-shot-picker flex min-w-0 flex-1 flex-col gap-1 text-xs font-medium text-ink-secondary">
                    <span className="text-[11px] text-ink-muted">Shot</span>
                    <Select
                      aria-label="Current shot"
                      value={selectedId || ""}
                      wrapperClassName="w-full"
                      onChange={(event) => setSelectedId(event.target.value)}
                    >
                      {storyboard!.shots.map((shot, index) => (
                        <option key={shot.id} value={shot.id}>
                          {`${index + 1}/${storyboard!.shots.length} · ${shot.title || shot.id} · ${shot.duration_s}s`}
                        </option>
                      ))}
                    </Select>
                  </label>
                  <Badge
                    tone={
                      promptDirty || readinessErrors.length
                        ? "warn"
                        : selectedJob?.status === "succeeded"
                          ? "ok"
                          : selectedJobActive
                            ? "info"
                            : "neutral"
                    }
                    className={cn(
                      "json-mobile-status shrink-0",
                      `json-mobile-status-${
                        promptDirty
                          ? "dirty"
                          : selectedJob?.status || (readinessErrors.length ? "missing" : "ready")
                      }`,
                    )}
                  >
                    {promptDirty
                      ? "Save prompt"
                      : selectedJobActive
                        ? selectedJob!.status
                        : readinessErrors.length
                          ? `${readinessErrors.length} missing`
                          : "Ready"}
                  </Badge>
                </div>

                {providerPicker}

                <nav
                  className="json-mobile-workflow-nav grid grid-cols-3 gap-1 rounded-md border border-border-subtle bg-elevated p-0.5"
                  aria-label="Shot workflow"
                >
                  {(["prompt", "references", "output"] as const).map((section) => {
                    const isActive = mobileSection === section;
                    return (
                      <button
                        key={section}
                        type="button"
                        className={cn(
                          "h-7 rounded px-2 text-xs font-medium transition-colors cursor-pointer",
                          isActive
                            ? "active bg-panel text-ink shadow-[var(--shadow-card)]"
                            : "text-ink-secondary hover:text-ink",
                        )}
                        aria-selected={isActive}
                        onClick={() => setMobileSection(section)}
                      >
                        {section.charAt(0).toUpperCase() + section.slice(1)}
                      </button>
                    );
                  })}
                </nav>
              </div>

              <div className="json-mobile-stage">
                {mobileSection === "prompt" ? promptPanel : assetPanel}
              </div>

              <div className="json-mobile-generate-bar sticky bottom-0 flex items-center justify-between gap-2 rounded-lg border border-border-subtle bg-panel/95 p-3 backdrop-blur">
                <div className="min-w-0">
                  <strong className="block truncate text-xs font-semibold text-ink">
                    {mobileReadinessText}
                  </strong>
                  <span className="block truncate text-[11px] text-ink-muted">
                    {selected?.title || selected?.id}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={!canGenerate}
                    onClick={() => {
                      setMobileSection("output");
                      void onGenerate();
                    }}
                  >
                    <Play className="h-3.5 w-3.5" aria-hidden="true" />
                    {selectedJobActive ? "Running…" : "Generate"}
                  </Button>
                  {selectedJobActive ? (
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      disabled={busy}
                      onClick={() => void onCancel()}
                    >
                      <Square className="h-3 w-3" aria-hidden="true" />
                      Cancel
                    </Button>
                  ) : null}
                </div>
              </div>
            </div>
          ) : (
            <div className="json-desktop-workspace grid min-h-0 flex-1 grid-cols-[240px_minmax(0,1fr)] gap-3">
              <JsonShotList
                shots={storyboard!.shots}
                selectedId={selectedId}
                statusByShotId={statusByShotId}
                onSelect={(id) => {
                  setSelectedId(id);
                  setDesktopInspector("references");
                }}
              />
              {selected ? (
                <ResizableWorkspace
                  className="json-desktop-workbench json-resizable-workbench min-h-0"
                  storageKey="ds.jsonProductionPromptWidth"
                  separatorLabel="Resize prompt and References or Output"
                  defaultSize={65}
                  minSize={42}
                  maxSize={76}
                  primary={promptPanel}
                  secondary={
                    <section className="json-desktop-inspector flex min-h-0 flex-col gap-2.5">
                      <nav
                        className="json-desktop-inspector-nav grid grid-cols-2 gap-0.5 rounded-md border border-border-subtle bg-elevated p-0.5"
                        aria-label="Shot inspector"
                      >
                        {(["references", "output"] as const).map((section) => {
                          const isActive = desktopInspector === section;
                          return (
                            <button
                              key={section}
                              type="button"
                              className={cn(
                                "h-7 rounded px-3 text-xs font-medium transition-colors cursor-pointer",
                                isActive
                                  ? "active bg-panel text-ink shadow-[var(--shadow-card)]"
                                  : "text-ink-secondary hover:text-ink",
                              )}
                              aria-selected={isActive}
                              onClick={() => setDesktopInspector(section)}
                            >
                              {section.charAt(0).toUpperCase() + section.slice(1)}
                            </button>
                          );
                        })}
                      </nav>
                      {assetPanel}
                    </section>
                  }
                />
              ) : (
                <EmptyState
                  icon={<FileJson className="h-4 w-4" aria-hidden="true" />}
                  title="No shot selected"
                  description="Select a shot from the list."
                  className="json-prompt-panel min-h-[320px]"
                />
              )}
            </div>
          )}
        </div>
      </PageShell>
    </>
  );
}
