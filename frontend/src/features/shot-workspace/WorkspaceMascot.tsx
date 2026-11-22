import { cn } from "../../shared/ui";

const portraits = {
  assistant: "/mode-ai-assistant.png",
  voice: "/mode-music-video.png",
  director: "/director-agent-shot-board.png",
};

/** Decorative portraits from the same Director Studio character family. */
export function WorkspaceMascot({
  role = "assistant",
  className,
}: {
  role?: keyof typeof portraits;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "workspace-mascot inline-flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border-subtle bg-elevated align-middle",
        className,
      )}
      aria-hidden="true"
    >
      <img
        src={portraits[role]}
        alt=""
        width={40}
        height={40}
        decoding="async"
        className="block h-full w-full scale-[1.35] origin-[50%_20%] object-contain"
      />
    </span>
  );
}
