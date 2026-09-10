import type { PromptSections } from "../../shared/api/types";
import type {
  JsonPictureRole,
  JsonProductionAudio,
  JsonProductionDocument,
  JsonProductionPicture,
  JsonProductionShot,
  ShotFileMaps,
} from "./types";

const PROMPT_FIELDS = [
  "subject_definitions",
  "summary",
  "retention_analysis",
  "detailed_description",
  "overall_soundscape",
  "non_diegetic_music",
] as const;

const PICTURE_ROLES = new Set<JsonPictureRole>([
  "actor",
  "costume",
  "scene",
  "prop",
  "layout",
  "other",
]);

const PICTURE_TAG_RE = /<Picture\s+(\d+)>/gi;
const AUDIO_TAG_RE = /<Audio\s+(\d+)>/gi;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parsePrompt(raw: unknown, shotId: string): PromptSections {
  if (!isRecord(raw)) {
    throw new Error(`shot ${shotId}: prompt must be an object`);
  }
  const prompt = {} as PromptSections;
  for (const field of PROMPT_FIELDS) {
    const value = raw[field];
    if (typeof value !== "string") {
      throw new Error(`shot ${shotId}: prompt.${field} must be a string`);
    }
    if (!value.trim()) {
      throw new Error(`shot ${shotId}: prompt.${field} must be non-empty`);
    }
    prompt[field] = value;
  }
  return prompt;
}

function parsePictures(raw: unknown, shotId: string): JsonProductionPicture[] {
  if (!Array.isArray(raw)) {
    throw new Error(`shot ${shotId}: pictures must be an array`);
  }
  if (raw.length < 1 || raw.length > 9) {
    throw new Error(`shot ${shotId}: pictures must have between 1 and 9 slots`);
  }
  const pictures: JsonProductionPicture[] = [];
  for (let i = 0; i < raw.length; i++) {
    const item = raw[i];
    if (!isRecord(item)) {
      throw new Error(`shot ${shotId}: picture slot ${i + 1} must be an object`);
    }
    const index = item.index;
    const role = item.role;
    const label = item.label;
    if (typeof index !== "number" || !Number.isInteger(index)) {
      throw new Error(`shot ${shotId}: picture slot index must be an integer`);
    }
    if (typeof role !== "string" || !PICTURE_ROLES.has(role as JsonPictureRole)) {
      throw new Error(`shot ${shotId}: picture ${index} has invalid role`);
    }
    if (typeof label !== "string" || !label.trim()) {
      throw new Error(`shot ${shotId}: picture ${index} label must be non-empty`);
    }
    pictures.push({
      index,
      role: role as JsonPictureRole,
      label,
    });
  }
  const indexes = pictures.map((p) => p.index);
  const expected = Array.from({ length: pictures.length }, (_, i) => i + 1);
  if (indexes.join(",") !== expected.join(",")) {
    throw new Error(
      `shot ${shotId}: picture indexes must be contiguous and ordered from 1`,
    );
  }
  return pictures;
}

function parseAudio(raw: unknown, shotId: string): JsonProductionAudio[] {
  if (raw === undefined) return [];
  if (!Array.isArray(raw)) {
    throw new Error(`shot ${shotId}: audio must be an array`);
  }
  if (raw.length > 3) {
    throw new Error(`shot ${shotId}: audio must have at most 3 slots`);
  }
  const audio: JsonProductionAudio[] = [];
  for (let i = 0; i < raw.length; i++) {
    const item = raw[i];
    if (!isRecord(item)) {
      throw new Error(`shot ${shotId}: audio slot ${i + 1} must be an object`);
    }
    const index = item.index;
    const label = item.label;
    if (typeof index !== "number" || !Number.isInteger(index)) {
      throw new Error(`shot ${shotId}: audio slot index must be an integer`);
    }
    if (typeof label !== "string" || !label.trim()) {
      throw new Error(`shot ${shotId}: audio ${index} label must be non-empty`);
    }
    audio.push({ index, label });
  }
  const indexes = audio.map((a) => a.index);
  const expected = Array.from({ length: audio.length }, (_, i) => i + 1);
  if (indexes.join(",") !== expected.join(",")) {
    throw new Error(
      `shot ${shotId}: audio indexes must be contiguous and ordered from 1`,
    );
  }
  return audio;
}

function parseShot(raw: unknown, position: number): JsonProductionShot {
  if (!isRecord(raw)) {
    throw new Error(`shot at position ${position}: must be an object`);
  }
  const id = raw.id;
  if (typeof id !== "string" || !id.trim()) {
    throw new Error(`shot at position ${position}: id must be a non-empty string`);
  }
  if (typeof raw.title !== "string" || !raw.title.trim()) {
    throw new Error(`shot ${id}: title must be a non-empty string`);
  }
  if (typeof raw.duration_s !== "number" || !(raw.duration_s > 0) || raw.duration_s > 15) {
    throw new Error(`shot ${id}: duration_s must be > 0 and <= 15`);
  }
  const scriptBeat =
    raw.script_beat === undefined
      ? ""
      : typeof raw.script_beat === "string"
        ? raw.script_beat
        : (() => {
            throw new Error(`shot ${id}: script_beat must be a string`);
          })();
  let dialogue: string[] = [];
  if (raw.dialogue !== undefined) {
    if (
      !Array.isArray(raw.dialogue) ||
      raw.dialogue.some((line) => typeof line !== "string")
    ) {
      throw new Error(`shot ${id}: dialogue must be an array of strings`);
    }
    dialogue = raw.dialogue as string[];
  }
  return {
    id,
    title: raw.title,
    script_beat: scriptBeat,
    duration_s: raw.duration_s,
    dialogue,
    pictures: parsePictures(raw.pictures, id),
    audio: parseAudio(raw.audio, id),
    prompt: parsePrompt(raw.prompt, id),
  };
}

function parseDocument(raw: unknown): JsonProductionDocument {
  if (!isRecord(raw)) {
    throw new Error("storyboard must be a JSON object");
  }
  if (raw.version !== 1) {
    throw new Error("storyboard version must be 1");
  }
  if (typeof raw.revision !== "number" || !Number.isInteger(raw.revision) || raw.revision < 0) {
    throw new Error("storyboard revision must be an integer >= 0");
  }
  if (raw.aspect_ratio !== "16:9" && raw.aspect_ratio !== "9:16") {
    throw new Error('storyboard aspect_ratio must be "16:9" or "9:16"');
  }
  if (!Array.isArray(raw.shots)) {
    throw new Error("storyboard shots must be an array");
  }
  const shots = raw.shots.map((shot, index) => parseShot(shot, index));
  const ids = shots.map((s) => s.id);
  const seen = new Set<string>();
  for (const id of ids) {
    if (seen.has(id)) {
      throw new Error(`duplicate shot id: ${id}`);
    }
    seen.add(id);
  }
  return {
    version: 1,
    revision: raw.revision,
    aspect_ratio: raw.aspect_ratio,
    shots,
  };
}

/** Parse imported storyboard text into a typed document. Backend remains authoritative. */
export function parseStoryboardJson(text: string): JsonProductionDocument {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("malformed JSON");
  }
  return parseDocument(parsed);
}

/** Parse one complete shot object for in-place replacement in a storyboard. */
export function parseShotJson(text: string, expectedId?: string): JsonProductionShot {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("malformed JSON");
  }
  const shot = parseShot(parsed, 0);
  if (expectedId !== undefined && shot.id !== expectedId) {
    throw new Error(`shot id cannot be changed (expected ${expectedId})`);
  }
  return shot;
}

function collectPromptText(prompt: PromptSections): string {
  return PROMPT_FIELDS.map((field) => prompt[field]).join("\n");
}

function findTagIndexes(text: string, pattern: RegExp): number[] {
  const indexes: number[] = [];
  pattern.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    indexes.push(Number(match[1]));
  }
  return indexes;
}

/** Client-side readiness checks before Generate. Returns human-readable errors. */
export function validateShotReadiness(
  shot: JsonProductionShot,
  files: ShotFileMaps,
): string[] {
  const errors: string[] = [];

  for (const field of PROMPT_FIELDS) {
    const value = shot.prompt[field];
    if (typeof value !== "string" || !value.trim()) {
      errors.push(`shot ${shot.id}: prompt.${field} is blank`);
    }
  }

  const promptText = collectPromptText(shot.prompt);
  const pictureTags = findTagIndexes(promptText, PICTURE_TAG_RE);
  const audioTags = findTagIndexes(promptText, AUDIO_TAG_RE);
  const declaredPictures = new Set(shot.pictures.map((p) => p.index));
  const declaredAudio = new Set(shot.audio.map((a) => a.index));

  for (const index of declaredPictures) {
    if (!pictureTags.includes(index)) {
      errors.push(`shot ${shot.id}: missing <Picture ${index}> tag in prompt`);
    }
  }
  const extraPictures = [...new Set(pictureTags)].filter((i) => !declaredPictures.has(i));
  for (const index of extraPictures.sort((a, b) => a - b)) {
    errors.push(`shot ${shot.id}: undeclared extra <Picture ${index}> tag in prompt`);
  }

  for (const index of declaredAudio) {
    if (!audioTags.includes(index)) {
      errors.push(`shot ${shot.id}: missing <Audio ${index}> tag in prompt`);
    }
  }
  const extraAudio = [...new Set(audioTags)].filter((i) => !declaredAudio.has(i));
  for (const index of extraAudio.sort((a, b) => a - b)) {
    errors.push(`shot ${shot.id}: undeclared extra <Audio ${index}> tag in prompt`);
  }

  for (const picture of shot.pictures) {
    if (!files.pictures.has(picture.index)) {
      errors.push(`shot ${shot.id}: missing file for Picture ${picture.index}`);
    }
  }
  for (const audio of shot.audio) {
    if (!files.audio.has(audio.index)) {
      errors.push(`shot ${shot.id}: missing file for Audio ${audio.index}`);
    }
  }

  return errors;
}
