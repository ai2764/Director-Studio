// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { parseShotJson, parseStoryboardJson, validateShotReadiness } from "./validation";
import type { JsonProductionDocument, JsonProductionShot } from "./types";

const VALID_DOCUMENT: JsonProductionDocument = {
  version: 1,
  revision: 1,
  aspect_ratio: "16:9",
  shots: [
    {
      id: "shot_001",
      title: "Corridor entry",
      script_beat: "Lu enters the municipal archive corridor.",
      duration_s: 6,
      dialogue: [],
      pictures: [
        {
          index: 1,
          role: "actor",
          label: "Lu identity and navy wardrobe",
        },
        {
          index: 2,
          role: "layout",
          label: "Post-entry blocking and corridor geography",
        },
      ],
      audio: [],
      prompt: {
        subject_definitions: "<Picture 1> defines Lu's identity and wardrobe.",
        summary: "<Picture 2> establishes the corridor composition.",
        retention_analysis: "Hold attention through the doorway reveal.",
        detailed_description: "0–6 seconds: Lu enters and stops at the desk.",
        overall_soundscape: "Quiet rain and fluorescent hum.",
        non_diegetic_music: "No non-diegetic music.",
      },
    },
  ],
};

function cloneShot(overrides: Record<string, unknown> = {}): JsonProductionShot {
  return {
    ...(structuredClone(VALID_DOCUMENT.shots[0]) as JsonProductionShot),
    ...overrides,
  };
}

function makeFile(name: string, type = "image/png"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type });
}

describe("parseStoryboardJson", () => {
  it("parses a valid document and keeps Layout as Picture 2", () => {
    const doc = parseStoryboardJson(JSON.stringify(VALID_DOCUMENT));
    expect(doc.revision).toBe(1);
    expect(doc.shots).toHaveLength(1);
    expect(doc.shots[0].pictures[1]).toMatchObject({
      index: 2,
      role: "layout",
    });
  });

  it("rejects malformed JSON", () => {
    expect(() => parseStoryboardJson("{not-json")).toThrow(/malformed|JSON/i);
  });

  it("rejects duplicate shot ids", () => {
    const dup = structuredClone(VALID_DOCUMENT);
    dup.shots.push({
      ...structuredClone(VALID_DOCUMENT.shots[0]),
      title: "Other",
    });
    expect(() => parseStoryboardJson(JSON.stringify(dup))).toThrow(
      /shot.*duplicate|duplicate.*shot/i,
    );
  });

  it("rejects non-contiguous picture slots", () => {
    const bad = structuredClone(VALID_DOCUMENT);
    bad.shots[0].pictures[1].index = 3;
    expect(() => parseStoryboardJson(JSON.stringify(bad))).toThrow(
      /shot_001.*picture|picture.*shot_001|contiguous/i,
    );
  });

  it("rejects non-contiguous audio slots", () => {
    const bad = structuredClone(VALID_DOCUMENT);
    bad.shots[0].audio = [
      { index: 1, label: "voice" },
      { index: 3, label: "fx" },
    ];
    bad.shots[0].prompt.overall_soundscape =
      "Use <Audio 1> and <Audio 3> under quiet rain.";
    expect(() => parseStoryboardJson(JSON.stringify(bad))).toThrow(
      /shot_001.*audio|audio.*shot_001|contiguous/i,
    );
  });

  it("rejects blank prompt sections", () => {
    const bad = structuredClone(VALID_DOCUMENT);
    bad.shots[0].prompt.summary = "   ";
    expect(() => parseStoryboardJson(JSON.stringify(bad))).toThrow(
      /shot_001.*summary|summary.*shot_001|prompt\.summary/i,
    );
  });
});

describe("parseShotJson", () => {
  it("parses a complete single shot with an added reference slot", () => {
    const raw = structuredClone(VALID_DOCUMENT.shots[0]);
    raw.pictures.push({
      index: 3,
      role: "prop",
      label: "New evidence envelope",
    });
    raw.prompt.detailed_description += " <Picture 3> appears on the desk.";

    const shot = parseShotJson(JSON.stringify(raw));

    expect(shot.id).toBe("shot_001");
    expect(shot.pictures).toHaveLength(3);
    expect(shot.pictures[2]).toEqual({
      index: 3,
      role: "prop",
      label: "New evidence envelope",
    });
  });

  it("rejects a replacement whose shot id changed", () => {
    const raw = structuredClone(VALID_DOCUMENT.shots[0]);
    raw.id = "shot_renamed";

    expect(() => parseShotJson(JSON.stringify(raw), "shot_001")).toThrow(
      /shot id.*cannot be changed|expected.*shot_001/i,
    );
  });
});

describe("validateShotReadiness", () => {
  const actorFile = makeFile("actor.png");
  const layoutFile = makeFile("layout.png");

  it("returns no errors for a ready shot with Layout as Picture 2", () => {
    const shot = cloneShot();
    expect(shot.pictures[1].role).toBe("layout");
    expect(shot.pictures[1].index).toBe(2);
    expect(
      validateShotReadiness(shot, {
        pictures: new Map([
          [1, actorFile],
          [2, layoutFile],
        ]),
        audio: new Map(),
      }),
    ).toEqual([]);
  });

  it("reports missing Picture tags in the prompt", () => {
    const shot = cloneShot({
      prompt: {
        ...VALID_DOCUMENT.shots[0].prompt,
        summary: "Corridor composition without the layout tag.",
      },
    });
    const errors = validateShotReadiness(shot, {
      pictures: new Map([
        [1, actorFile],
        [2, layoutFile],
      ]),
      audio: new Map(),
    });
    expect(errors.some((e) => /Picture 2|missing.*Picture/i.test(e))).toBe(true);
  });

  it("reports extra undeclared Picture tags", () => {
    const shot = cloneShot({
      prompt: {
        ...VALID_DOCUMENT.shots[0].prompt,
        detailed_description:
          "0–6 seconds: Lu enters. Ignore <Picture 3> entirely.",
      },
    });
    const errors = validateShotReadiness(shot, {
      pictures: new Map([
        [1, actorFile],
        [2, layoutFile],
      ]),
      audio: new Map(),
    });
    expect(errors.some((e) => /Picture 3|undeclared|extra/i.test(e))).toBe(true);
  });

  it("reports missing Audio tags when audio slots are declared", () => {
    const shot = cloneShot({
      audio: [{ index: 1, label: "rain bed" }],
    });
    const errors = validateShotReadiness(shot, {
      pictures: new Map([
        [1, actorFile],
        [2, layoutFile],
      ]),
      audio: new Map([[1, makeFile("rain.wav", "audio/wav")]]),
    });
    expect(errors.some((e) => /Audio 1|missing.*Audio/i.test(e))).toBe(true);
  });

  it("reports extra undeclared Audio tags", () => {
    const shot = cloneShot({
      prompt: {
        ...VALID_DOCUMENT.shots[0].prompt,
        overall_soundscape: "Quiet rain and <Audio 1> hum.",
      },
    });
    const errors = validateShotReadiness(shot, {
      pictures: new Map([
        [1, actorFile],
        [2, layoutFile],
      ]),
      audio: new Map(),
    });
    expect(errors.some((e) => /Audio 1|undeclared|extra/i.test(e))).toBe(true);
  });

  it("reports missing File objects for declared picture slots", () => {
    const shot = cloneShot();
    const errors = validateShotReadiness(shot, {
      pictures: new Map([[1, actorFile]]),
      audio: new Map(),
    });
    expect(errors.some((e) => /Picture 2|missing.*file|file.*Picture 2/i.test(e))).toBe(
      true,
    );
  });

  it("reports blank prompt sections", () => {
    const shot = cloneShot({
      prompt: {
        ...VALID_DOCUMENT.shots[0].prompt,
        retention_analysis: "  ",
      },
    });
    const errors = validateShotReadiness(shot, {
      pictures: new Map([
        [1, actorFile],
        [2, layoutFile],
      ]),
      audio: new Map(),
    });
    expect(
      errors.some((e) => /retention_analysis|blank|empty/i.test(e)),
    ).toBe(true);
  });
});
