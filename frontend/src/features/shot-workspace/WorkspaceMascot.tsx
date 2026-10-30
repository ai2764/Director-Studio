const portraits = {
  assistant: "/mode-ai-assistant.png",
  voice: "/mode-music-video.png",
  director: "/director-agent-shot-board.png",
};

/** Decorative portraits from the same Director Studio character family. */
export function WorkspaceMascot({role = "assistant"}: {role?: keyof typeof portraits}) {
  return <span className="workspace-mascot" aria-hidden="true"><img src={portraits[role]} alt=""
    width={40} height={40} decoding="async" /></span>;
}
