import { useCallback, useId, useRef, useState, type DragEvent, type ChangeEvent } from "react";

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
    <div className={`upload-slot ${error ? "has-error" : ""} ${disabled ? "disabled" : ""}`}>
      <div className="upload-slot-header">
        <label htmlFor={inputId}>
          {label}
          {required ? <span className="req"> *</span> : null}
        </label>
        {value ? (
          <div className="upload-actions">
            <button type="button" className="btn ghost sm" disabled={disabled} onClick={() => inputRef.current?.click()}>
              Replace
            </button>
            <button type="button" className="btn ghost sm" disabled={disabled} onClick={() => assignFile(null)}>
              Clear
            </button>
          </div>
        ) : null}
      </div>

      <div
        className={`upload-drop ${dragOver ? "drag-over" : ""} ${value ? "has-file" : ""}`}
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
          <div className="upload-preview">
            <img src={value.previewUrl} alt={label} />
            <div className="upload-meta">
              <div className="filename" title={value.file.name}>
                {value.file.name}
              </div>
              <div className="filesize">{formatBytes(value.file.size)}</div>
            </div>
          </div>
        ) : (
          <div className="upload-empty">
            <div className="upload-icon">↑</div>
            <div>Drop image here or click to choose</div>
            <div className="muted">JPG / PNG / WEBP</div>
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

      {hint ? <p className="field-hint">{hint}</p> : null}
      {error ? <p className="field-error">{error}</p> : null}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
