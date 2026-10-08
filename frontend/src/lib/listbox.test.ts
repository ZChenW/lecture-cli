import { describe, expect, it } from "vitest";
import { matchIndex, moveIndex, opensUpward, type ListOption } from "./listbox";

const options: ListOption[] = [
  { value: "en", label: "英语", code: "en" },
  { value: "zh", label: "中文", code: "zh" },
  { value: "auto", label: "自动识别", code: "auto", disabled: true },
  { value: "fr", label: "French", code: "fr" },
  { value: "fa", label: "Farsi", code: "fa" },
];

describe("moveIndex", () => {
  it("moves with the arrows, skipping disabled options and stopping at the ends", () => {
    expect(moveIndex(options, 0, "ArrowDown")).toBe(1);
    expect(moveIndex(options, 1, "ArrowDown")).toBe(3);
    expect(moveIndex(options, 4, "ArrowDown")).toBe(4);
    expect(moveIndex(options, 3, "ArrowUp")).toBe(1);
    expect(moveIndex(options, 0, "ArrowUp")).toBe(0);
  });
  it("jumps with Home and End, and ignores other keys", () => {
    expect(moveIndex(options, 3, "Home")).toBe(0);
    expect(moveIndex(options, 0, "End")).toBe(4);
    expect(moveIndex(options, 0, "PageDown")).toBeNull();
    expect(moveIndex([{ value: "x", label: "x", disabled: true }], 0, "ArrowDown")).toBeNull();
  });
  it("starts from the first or last option when nothing is active", () => {
    expect(moveIndex(options, -1, "ArrowDown")).toBe(0);
    expect(moveIndex(options, -1, "ArrowUp")).toBe(4);
  });
});

describe("matchIndex", () => {
  it("matches labels and codes, case-insensitively, after the current option", () => {
    expect(matchIndex(options, 0, "z")).toBe(1);
    expect(matchIndex(options, 0, "F")).toBe(3);
    expect(matchIndex(options, 0, "fa")).toBe(4);
  });
  it("cycles through options sharing a first letter and skips disabled ones", () => {
    expect(matchIndex(options, 3, "f")).toBe(4);
    expect(matchIndex(options, 4, "ff")).toBe(3);
    expect(matchIndex(options, 0, "a")).toBeNull();
    expect(matchIndex(options, 0, "")).toBeNull();
  });
});

describe("opensUpward", () => {
  it("opens upward only near the bottom with more room above", () => {
    expect(opensUpward({ top: 100, bottom: 144 }, 150, 820)).toBe(false);
    expect(opensUpward({ top: 700, bottom: 744 }, 150, 820)).toBe(true);
    expect(opensUpward({ top: 60, bottom: 104 }, 900, 820)).toBe(false);
  });
});
