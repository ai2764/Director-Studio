import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { Button, Select } from "../../shared/ui";
import { describeUploadImage, type LibraryAsset, type LibraryKind } from "./api";

export function LibraryDescriptionWand({
  asset,
  name,
  description,
  projectId,
  disabled,
  onDescription,
}: {
  asset: LibraryAsset;
  name: string;
  description: string;
  projectId: string | null;
  disabled: boolean;
  onDescription: (text: string) => void;
}) {
  const images = Object.keys(asset.files).filter(
    (key) => /\.(png|jpe?g|webp|gif)$/i.test(asset.files[key] || "") && asset.urls[key],
  );
  const [key, setKey] = useState(images[0] || "");
  const [reading, setReading] = useState(false);
  const [message, setMessage] = useState("");
  const request = useRef<AbortController | null>(null);
  const latest = useRef({ name, description, key });
  latest.current = { name, description, key };
  useEffect(() => () => request.current?.abort(), []);
  if (!images.length || !projectId || asset.kind === "voices") return null;

  async function read() {
    const captured = { name, description, key };
    const controller = new AbortController();
    request.current = controller;
    setReading(true);
    setMessage("");
    try {
      const response = await fetch(asset.urls[key], { signal: controller.signal });
      if (!response.ok) throw new Error("The selected image is unavailable.");
      const blob = await response.blob();
      const result = await describeUploadImage({
        file: new File([blob], asset.files[key] || "reference.png", { type: blob.type }),
        name,
        existingDescription: description,
        kind: asset.kind as LibraryKind,
        projectId: projectId!,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (JSON.stringify(latest.current) === JSON.stringify(captured)) {
        onDescription(result.description);
        setMessage("Description refined. Review it before saving.");
      } else {
        setMessage("Your edits were kept. Retry to refine the current text.");
      }
    } catch (cause) {
      if (!controller.signal.aborted) setMessage(String(cause));
    } finally {
      if (request.current === controller) {
        request.current = null;
        setReading(false);
      }
    }
  }

  return (
    <div className="library-description-wand flex flex-wrap items-center gap-2 rounded-md border border-border-subtle bg-elevated/40 p-2.5">
      {images.length > 1 && (
        <label className="flex items-center gap-2 text-xs text-ink-secondary">
          <span>Image</span>
          <Select
            size="sm"
            aria-label="Image to describe"
            value={key}
            disabled={disabled || reading}
            onChange={(event) => setKey(event.target.value)}
          >
            {images.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
        </label>
      )}
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={disabled || reading}
        onClick={() => void read()}
      >
        <Sparkles className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
        {reading
          ? "Reading image…"
          : description.trim()
            ? "✦ Refine description"
            : "✦ Describe image"}
      </Button>
      {reading && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={() => {
            request.current?.abort();
            request.current = null;
            setReading(false);
          }}
        >
          Cancel
        </Button>
      )}
      {message && (
        <small className="w-full text-[11px] text-ink-secondary" role="status">
          {message}
        </small>
      )}
    </div>
  );
}
