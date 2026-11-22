import { useState } from "react";
import { Plus, RotateCcw, Send, Square, X } from "lucide-react";
import { ResizableWorkspace } from "../../shared/components/ResizableWorkspace";
import { useProject } from "../../shared/project/ProjectContext";
import { Banner, Button, EmptyState, Select, StatusDot, Textarea, cn } from "../../shared/ui";
import type { SegmentSelection } from "../music/api";
import { SongTransport } from "../music/SongTransport";
import { ShotWorkspacePage } from "../shot-workspace/ShotWorkspacePage";
import { ContextCompaction } from "./ContextCompaction";
import { ContextUsagePanel } from "./ContextUsage";
import { MobileShotDrawer } from "./MobileShotDrawer";
import { ShotWorkspace } from "./ShotWorkspace";
import { generationStatusText } from "./generationStatus";
import {
  MAX_CHAT_IMAGES,
  useDirectorController,
  visibleChatImages,
} from "./useDirectorController";

function ChatImageCard({
  url,
  caption,
  onOpen,
}: {
  url: string;
  caption?: string;
  onOpen: (url: string) => void;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <button
      type="button"
      className="chat-image-btn group flex flex-col overflow-hidden rounded-lg border border-border-subtle bg-panel text-left transition-colors hover:border-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent cursor-pointer"
      onClick={() => onOpen(url)}
      title={caption || "preview"}
    >
      <img
        src={url}
        alt={caption || "layout"}
        onError={() => setFailed(true)}
        className="aspect-video w-full bg-canvas object-cover transition-transform duration-200 group-hover:scale-[1.02]"
      />
      {caption ? (
        <span className="chat-image-cap block w-full truncate border-t border-border-subtle px-2 py-1 text-[11px] text-ink-secondary">
          {caption}
        </span>
      ) : null}
    </button>
  );
}

function JsonModeDirectorNotice() {
  return (
    <main className="workspace flex flex-1 items-center justify-center p-6">
      <section className="panel w-full max-w-md rounded-xl border border-border-subtle bg-panel p-6 text-ink shadow-[var(--shadow-card)]">
        <EmptyState
          title="Director"
          description={
            <>
              <span className="block font-medium text-ink">
                This project bypasses the local Director Agent
              </span>
              <span className="muted mt-1 block text-ink-muted">
                Use the Production tab to import storyboard JSON and submit shots directly to H3.
              </span>
            </>
          }
          className="border-0 bg-transparent p-0"
        />
      </section>
    </main>
  );
}

/**
 * Chat-first Director: natural language + optional short command chips.
 * Shot cards on the right show layout previews and refresh with project state.
 */
export interface DirectorChatRequest {
  id: string;
  projectId: string;
  message: string;
}

export function DirectorPage({
  active = true,
  chatOnly = false,
  mobile = false,
  requestedMessage = null,
  segmentSelection,
}: {
  active?: boolean;
  chatOnly?: boolean;
  mobile?: boolean;
  requestedMessage?: DirectorChatRequest | null;
  segmentSelection?: SegmentSelection;
} = {}) {
  const { project } = useProject();
  if (project?.authoring_mode === "shot_workspace") {
    return <ShotWorkspacePage desktopOutput={!mobile} />;
  }
  if (project?.mode === "json_production") {
    return <JsonModeDirectorNotice />;
  }
  return (
    <DirectorAgentWorkspace
      active={active}
      chatOnly={chatOnly}
      mobile={mobile}
      requestedMessage={requestedMessage}
      segmentSelection={segmentSelection}
    />
  );
}

const SHORTCUT_CHIPS = [
  { label: "Project status", text: "status" },
  { label: "Plan shots", text: "plan" },
  {
    label: "Discuss Layouts",
    text: "I want to discuss whether any shots would benefit from optional Layout studies. Ask what visual states I want before proposing sources. Do not queue generation yet.",
  },
];

function DirectorAgentWorkspace({
  active,
  chatOnly,
  mobile,
  requestedMessage,
  segmentSelection: providedSelection,
}: {
  active: boolean;
  chatOnly: boolean;
  mobile: boolean;
  requestedMessage: DirectorChatRequest | null;
  segmentSelection?: SegmentSelection;
}) {
  const {
    projectId,
    project,
    shots,
    messages,
    draft,
    setDraft,
    pendingImages,
    selectedShotIndex,
    selectedShot,
    setSelectedShotId,
    videoContextFocusId,
    error,
    pollError,
    vramPollError,
    vramStatus,
    clockNow,
    busy,
    lightbox,
    setLightbox,
    liveStatus,
    liveRuntime,
    liveThink,
    liveTokens,
    contextUsage,
    setCompactingContext,
    llmModel,
    llmOptions,
    llmProvider,
    llmReachable,
    llmBusy,
    chatActive,
    chatDisabled,
    generationLocked,
    chatLogRef,
    setMusicSelection,
    addChatImages,
    removeChatImage,
    onChangeLlm,
    send,
    cancelChat,
    updateShot,
  } = useDirectorController({
    requestedMessage,
    segmentSelection: providedSelection,
  });

  const chatPanel = (
    <section className="panel director-chat-panel flex h-full max-h-none min-h-0 flex-col overflow-hidden rounded-lg border border-border-subtle bg-panel p-0 text-ink shadow-[var(--shadow-card)]">
      <div className="director-chat-header workspace-panel-header flex min-h-[54px] shrink-0 items-center border-b border-border-subtle bg-panel/90 px-4 py-2">
        <div className="director-chat-header-row flex w-full flex-wrap items-center justify-between gap-2 xl:flex-nowrap">
          <div className="director-identity flex min-w-0 flex-1 items-center gap-2.5">
            <img
              className="director-agent-mascot h-7 w-7 shrink-0 rounded-md border border-border-subtle bg-elevated object-cover"
              src="/director-agent-shot-board.png"
              alt="Director Agent holding a shot board"
            />
            <div className="min-w-0">
              <h2 className="text-[13px] font-semibold leading-tight tracking-[-0.01em] text-ink">
                Director
              </h2>
              {!projectId ? (
                <p className="project-scope-hint truncate text-[11px] leading-tight text-ink-muted">
                  Select a project, or create one with your first message
                </p>
              ) : null}
            </div>
          </div>
          <div className="director-runtime-controls flex shrink-0 items-center justify-end gap-2">
            <label className="director-model-picker inline-model-picker flex min-w-0 items-center gap-1.5">
              <span className="muted tiny text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                LLM
              </span>
              <Select
                size="sm"
                wrapperClassName="w-[138px] min-w-[116px] max-w-[170px]"
                value={llmOptions.length ? llmModel : ""}
                disabled={chatDisabled || llmBusy || llmOptions.length === 0}
                onChange={(e) => void onChangeLlm(e.target.value)}
                title={`${llmProvider} model used for Director chat and shot planning`}
              >
                {llmOptions.length === 0 ? (
                  <option value="">
                    {llmReachable ? "No models available" : `${llmProvider} unavailable`}
                  </option>
                ) : (
                  <>
                    {llmModel && !llmOptions.includes(llmModel) ? (
                      <option value={llmModel} disabled>
                        {llmModel} (not available)
                      </option>
                    ) : null}
                    {llmOptions.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </>
                )}
              </Select>
            </label>
            <ContextUsagePanel calls={contextUsage} compact={mobile}>
              {projectId ? (
                <ContextCompaction
                  key={projectId}
                  projectId={projectId}
                  disabled={busy || generationLocked || chatActive || llmBusy || !llmModel}
                  onBusyChange={setCompactingContext}
                />
              ) : null}
            </ContextUsagePanel>
          </div>
        </div>
      </div>

      {mobile && !chatOnly ? (
        <MobileShotDrawer
          shots={shots}
          onOpenImage={setLightbox}
          focusShotId={videoContextFocusId}
          onShotUpdated={updateShot}
        />
      ) : null}

      {error ? (
        <Banner tone="danger" className="banner error mx-4 mt-2.5 shrink-0">
          {error}
        </Banner>
      ) : null}
      {pollError ? (
        <Banner
          tone="danger"
          role="alert"
          aria-live="polite"
          className="banner error mx-4 mt-2.5 shrink-0"
        >
          {pollError}
        </Banner>
      ) : null}
      {vramPollError ? (
        <Banner
          tone="danger"
          role="alert"
          aria-live="polite"
          className="banner error mx-4 mt-2.5 shrink-0"
        >
          {vramPollError}
        </Banner>
      ) : null}

      {generationLocked && vramStatus ? (
        <div
          className="director-generation-status mx-4 mt-2.5 flex shrink-0 items-center gap-2 rounded-md border border-status-warn/30 bg-status-warn-soft px-3 py-1.5 text-xs font-medium text-status-warn"
          role="status"
          aria-live="polite"
        >
          <StatusDot tone="warn" pulse className="director-generation-dot" />
          <span>{generationStatusText(vramStatus, clockNow)}</span>
        </div>
      ) : null}

      <div className="chat-log min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4" ref={chatLogRef}>
        {messages.map((m, i) => {
          const images = visibleChatImages(m.images, shots);
          const canRetryPrompt =
            m.role === "assistant" && i === messages.length - 1 && !!m.prompt_retry;
          const isUser = m.role === "user";
          return (
            <div
              key={m.id || i}
              className={cn(
                "chat-bubble rounded-lg border px-3.5 py-2.5 text-ink",
                isUser
                  ? "user ml-auto max-w-[85%] border-accent/25 bg-accent-soft"
                  : "assistant mr-auto max-w-[94%] border-border-subtle bg-elevated/55",
              )}
            >
              <div className="chat-role mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                {isUser ? "You" : "Director"}
              </div>
              {m.steps?.length ? (
                <details
                  className="chat-trace my-2 overflow-hidden rounded-md border border-border-subtle bg-panel/80 text-xs"
                  open={false}
                >
                  <summary className="cursor-pointer select-none px-2.5 py-1.5 font-medium text-ink-secondary hover:text-ink">
                    Process ({m.steps.length})
                  </summary>
                  <ol className="chat-steps list-decimal space-y-1 border-t border-border-subtle py-2 pl-6 pr-3 text-ink-secondary">
                    {m.steps.map((s, j) => (
                      <li key={j}>{s}</li>
                    ))}
                  </ol>
                </details>
              ) : null}
              {m.thinking ? (
                <details
                  className="chat-think my-2 overflow-hidden rounded-md border border-border-subtle bg-panel/80 text-xs"
                  open={false}
                >
                  <summary className="cursor-pointer select-none px-2.5 py-1.5 font-medium text-ink-secondary hover:text-ink">
                    Reasoning
                  </summary>
                  <pre className="chat-think-body max-h-60 overflow-auto whitespace-pre-wrap break-words border-t border-border-subtle px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-secondary">
                    {m.thinking}
                  </pre>
                </details>
              ) : null}
              <div className="chat-content whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink">
                {m.content}
              </div>
              {canRetryPrompt ? (
                <div className="chat-message-actions mt-2.5 flex items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="xs"
                    className="prompt-retry-button"
                    disabled={chatDisabled}
                    onClick={() =>
                      void send("Retry prompt", {
                        preserveComposer: true,
                        promptRetry: m.prompt_retry ?? undefined,
                      })
                    }
                  >
                    <RotateCcw className="h-3 w-3" aria-hidden="true" />
                    Retry prompt
                  </Button>
                </div>
              ) : null}
              {images.length ? (
                <div className="chat-images mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {images.map((img, j) => (
                    <ChatImageCard
                      key={j}
                      url={img.url}
                      caption={img.caption}
                      onOpen={setLightbox}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}

        {busy || chatActive ? (
          <div className="chat-bubble assistant live mr-auto max-w-[94%] space-y-2 rounded-lg border border-accent/25 bg-elevated/70 px-3.5 py-2.5 text-ink">
            <div className="chat-role flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-accent">
              <StatusDot tone="accent" pulse />
              Director · Working
            </div>
            {liveStatus.length ? (
              <div className="chat-trace open rounded-md border border-border-subtle bg-panel/80 p-2.5 text-xs">
                <div className="chat-trace-label mb-1 font-medium text-ink-secondary">Process</div>
                <ol className="chat-steps list-decimal space-y-1 pl-5 text-ink-secondary">
                  {liveStatus.map((s, j) => (
                    <li key={j}>{s}</li>
                  ))}
                </ol>
              </div>
            ) : !liveRuntime ? (
              <div className="chat-content muted text-xs text-ink-muted">Connecting…</div>
            ) : null}
            {liveRuntime ? (
              <div
                className="chat-runtime rounded-md border border-border-subtle bg-panel/70 px-2.5 py-1.5 text-xs text-ink-secondary"
                aria-live="polite"
              >
                <div className="chat-trace-label text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                  Runtime
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-ink">{liveRuntime}</div>
              </div>
            ) : null}
            {liveThink ? (
              <div className="chat-think open rounded-md border border-border-subtle bg-panel/80 p-2.5 text-xs">
                <div className="chat-trace-label mb-1 font-medium text-ink-secondary">
                  Reasoning
                </div>
                <pre className="chat-think-body max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-secondary">
                  {liveThink}
                </pre>
              </div>
            ) : null}
            {liveTokens ? (
              <div className="chat-content draft-tokens whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink">
                {liveTokens}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="shrink-0 border-t border-border-subtle bg-panel">
        {!chatOnly ? (
          <div className="chat-chips m-0 flex flex-wrap items-center gap-1.5 px-4 pt-2.5">
            {SHORTCUT_CHIPS.map((c) => (
              <button
                key={c.label}
                type="button"
                className="mode-chip h-6 cursor-pointer rounded-md border border-border-subtle bg-elevated px-2.5 text-[11px] font-medium text-ink-secondary transition-colors hover:border-border-strong hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-45"
                disabled={chatDisabled}
                onClick={() => void send(c.text)}
              >
                {c.label}
              </button>
            ))}
            {selectedShot ? (
              <button
                type="button"
                className="mode-chip prompt-shortcut h-6 cursor-pointer rounded-md border border-accent/25 bg-accent-soft px-2.5 text-[11px] font-medium text-accent transition-colors hover:border-accent/50 disabled:pointer-events-none disabled:opacity-45"
                disabled={chatDisabled}
                onClick={() => void send(`Write the H3 prompt for shot ${selectedShotIndex + 1}`)}
              >
                Write H3 prompt · Shot {String(selectedShotIndex + 1).padStart(2, "0")}
              </button>
            ) : null}
          </div>
        ) : null}

        {pendingImages.length ? (
          <div
            className="chat-attachment-tray flex flex-wrap gap-2 px-4 pt-2.5"
            aria-label="Attached images"
          >
            {pendingImages.map((image, index) => (
              <div
                className="chat-attachment flex items-center gap-2 rounded-md border border-border-subtle bg-elevated/60 py-1 pl-1 pr-1.5 text-xs"
                key={`${image.file.name}-${index}`}
              >
                <img
                  src={image.previewUrl}
                  alt={image.file.name}
                  className="h-7 w-7 rounded object-cover"
                />
                <span title={image.file.name} className="max-w-[140px] truncate text-[11px] text-ink">
                  {image.file.name}
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${image.file.name}`}
                  disabled={chatDisabled}
                  onClick={() => removeChatImage(index)}
                  className="inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded text-ink-muted hover:bg-hover hover:text-ink disabled:opacity-45"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <div className="chat-composer px-4 pb-3.5 pt-2.5">
          <div className="flex flex-col overflow-hidden rounded-xl border border-border-subtle bg-elevated/55 transition-colors focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/15">
            <Textarea
              rows={mobile ? 2 : 3}
              value={draft}
              disabled={chatDisabled}
              className="min-h-[64px] w-full resize-none border-0 bg-transparent px-3.5 pb-1.5 pt-2.5 text-[13px] leading-relaxed shadow-none focus-visible:border-0 focus-visible:ring-0"
              placeholder={
                projectId
                  ? "Talk to the Director about the story, a weak shot, or what you want to change…"
                  : "Enter a project name, or paste a script to create a project…"
              }
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="flex items-center justify-between gap-2 px-2.5 pb-2 pt-1">
              <div className="flex items-center gap-2">
                <label
                  className={cn(
                    "chat-upload-btn relative inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md border border-border-subtle bg-panel/80 text-ink-secondary transition-colors hover:border-border-strong hover:bg-hover hover:text-ink",
                    (!projectId || chatDisabled || pendingImages.length >= MAX_CHAT_IMAGES) &&
                      "disabled pointer-events-none opacity-45",
                  )}
                  title={projectId ? "Add up to 4 images" : "Select a project before adding images"}
                >
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    multiple
                    aria-label="Add images"
                    className="sr-only"
                    disabled={!projectId || chatDisabled || pendingImages.length >= MAX_CHAT_IMAGES}
                    onChange={(event) => {
                      addChatImages(event.target.files);
                      event.target.value = "";
                    }}
                  />
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                </label>
                {!mobile ? (
                  <span className="select-none text-[11px] text-ink-muted">
                    Enter to send · Shift+Enter for new line
                  </span>
                ) : null}
              </div>
              {chatActive ? (
                <Button
                  type="button"
                  variant="danger"
                  size="sm"
                  disabled={llmBusy}
                  onClick={() => void cancelChat()}
                >
                  <Square className="h-3 w-3" aria-hidden="true" />
                  Cancel
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  disabled={chatDisabled || (!draft.trim() && pendingImages.length === 0)}
                  onClick={() => void send()}
                >
                  <Send className="h-3 w-3" aria-hidden="true" />
                  Send
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );

  const shotPanel = (
    <ShotWorkspace
      shots={shots}
      busy={busy}
      onSend={(message) => void send(message)}
      onSelectShot={(shot) => setSelectedShotId(shot.id)}
      onShotUpdated={updateShot}
      onOpenImage={setLightbox}
      focusShotId={videoContextFocusId}
    />
  );

  return (
    <main
      className={cn(
        "workspace director-chat-layout flex min-h-0 flex-1 flex-col gap-3 p-3 sm:p-4",
        !chatOnly && !mobile && "director-fill-viewport h-full",
        chatOnly && "chat-only",
        mobile && "mobile-director-layout",
      )}
    >
      {project?.mode === "mv" && (
        <SongTransport key={projectId} active={active} onSelectionChange={setMusicSelection} />
      )}
      {chatOnly || mobile ? (
        chatPanel
      ) : (
        <ResizableWorkspace
          className="director-resizable-workspace min-h-0 flex-1"
          storageKey="ds.directorChatWidth"
          separatorLabel="Resize Director chat and Shots"
          primary={chatPanel}
          secondary={shotPanel}
        />
      )}

      {lightbox ? (
        <div
          className="lightbox-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          role="dialog"
          onClick={() => setLightbox(null)}
          onKeyDown={(e) => e.key === "Escape" && setLightbox(null)}
        >
          <img
            src={lightbox}
            alt="full preview"
            className="lightbox-img max-h-[90vh] max-w-[92vw] rounded-xl border border-border-subtle object-contain shadow-[var(--shadow-popover)]"
          />
        </div>
      ) : null}
    </main>
  );
}
