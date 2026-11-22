import { useCallback, useId, useRef, useState, type DragEvent, type ChangeEvent } from "react";
import { RefreshCw, Upload, X } from "lucide-react";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";

export interface LocalImage {
  file: File;
  previewUrl: string;
}

interface Props {
  label: string;
  required?: boolean;
  hint?: string;
  value: LocalImage | null;
  onChange: (value: LocalImage | null) => void;
  disabled?: boolean;
  error?: string | null;
  accept?: string;
}

export function ImageUploadSlot({
  label,
  required,
  hint,
  value,
  onChange,
  disabled,
  error,
  accept = "image/jpeg,image/png,image/webp",
}: Props) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const assignFile = useCallback(
    (file: File | null) => {
      if (!file) {
        if (value?.previewUrl) URL.revokeObjectURL(value.previewUrl);
        onChange(null);
        return;
      }
      if (!file.type.startsWith("image/")) {
        return;
      }
      if (value?.previewUrl) URL.revokeObjectURL(value.previewUrl);
      onChange({ file, previewUrl: URL.createObjectURL(file) });
    },
    [onChange, value],
  );

  const onFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    assignFile(file);
    e.target.value = "";
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const file = e.dataTransfer.files?.[0];
    if (file) assignFile(file);
  };

  return (
    <div
      className={cn(
        "upload-slot flex flex-col gap-1.5",
        error && "has-error",
        disabled && "disabled opacity-60",
      )}
    >
      <div className="upload-slot-header flex items-center justify-between gap-2">
        <label htmlFor={inputId} className="text-xs font-medium text-ink">
          {label}
          {required ? <span className="req ml-0.5 text-status-danger">*</span> : null}
        </label>
        {value ? (
          <div className="upload-actions flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              disabled={disabled}
              onClick={() => inputRef.current?.click()}
            >
              <RefreshCw className="h-3 w-3" aria-hidden="true" />
              Replace
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              disabled={disabled}
              onClick={() => assignFile(null)}
            >
              <X className="h-3 w-3" aria-hidden="true" />
              Clear
            </Button>
          </div>
        ) : null}
      </div>

      <div
        className={cn(
          "upload-drop rounded-lg border border-dashed transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          dragOver
            ? "drag-over border-accent bg-accent-soft"
            : error
              ? "border-status-danger/60 bg-status-danger-soft/40"
              : "border-border-strong bg-elevated/50 hover:border-ink-muted hover:bg-hover/60",
          value ? "has-file border-solid border-border-subtle bg-panel p-2" : "cursor-pointer px-4 py-5",
        )}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => !disabled && !value && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !value && !disabled) {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
      >
        {value ? (
          <div className="upload-preview flex items-center gap-3">
            <img
              src={value.previewUrl}
              alt={label}
              className="h-14 w-14 shrink-0 rounded border border-border-subtle bg-canvas object-cover"
            />
            <div className="upload-meta min-w-0 flex-1">
              <div className="filename truncate text-xs font-medium text-ink" title={value.file.name}>
                {value.file.name}
              </div>
              <div className="filesize font-mono text-[11px] text-ink-muted">
                {formatBytes(value.file.size)}
              </div>
            </div>
          </div>
        ) : (
          <div className="upload-empty flex flex-col items-center justify-center gap-1 text-center text-xs text-ink-secondary">
            <div className="upload-icon flex h-7 w-7 items-center justify-center rounded-md border border-border-subtle bg-panel text-ink-muted">
              <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            </div>
            <div>Drop image here or click to choose</div>
            <div className="muted font-mono text-[10px] text-ink-muted">JPG / PNG / WEBP</div>
          </div>
        )}
      </div>

      <input
        id={inputId}
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        disabled={disabled}
        onChange={onFileChange}
      />

      {hint ? <p className="field-hint text-[11px] text-ink-muted">{hint}</p> : null}
      {error ? <p className="field-error text-[11px] text-status-danger">{error}</p> : null}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
