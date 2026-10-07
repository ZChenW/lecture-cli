import { describe, expect, it } from "vitest";
import {
  closingStages, isTyping, levelShare, lyrics, micPercent, progressOf, pushLevel, segmentTime, tailText, updatedAgo,
  waveBars, WAVE_BARS,
} from "./record";
import type { Snapshot } from "./types";

const segment = (id: number, text: string, start = "00:46:58.20") => ({ id, start, end: start, text });

function snap(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    run_id: "r", course: "MATH421", output: "/c/MATH421/LectureNotes/x.md", started: "2026-10-07T09:00:00-04:00",
    phase: "recording", phase_since: 100, input: "系统默认", paused: false, can_skip: false, elapsed_seconds: 0,
    asr: { status: "转录收尾中", device_label: null, model: "base.en", level: 0, backlog_seconds: 0, queued_seconds: 0,
      notices: [], error: null },
    transcript: { count: 0, tail: [], pending: "" },
    notes: { status: "编写详细笔记 2/7", worker_alive: true, unprocessed_segments: 0, updated: null, latest: null },
    refine: { enabled: false, status: null, reason: null },
    stages: [], ...overrides,
  };
}

describe("waveform", () => {
  it("maps levels like the terminal meter and keeps the newest 45 samples", () => {
    expect(levelShare(0.1)).toBeCloseTo(0.75);
    expect(levelShare(1)).toBe(1);
    expect(levelShare(-1)).toBe(0);
    let samples: number[] = [];
    for (let i = 0; i < 50; i++) samples = pushLevel(samples, i / 1000);
    expect(samples).toHaveLength(WAVE_BARS);
    expect(samples.at(-1)).toBeCloseTo(levelShare(0.049));
  });

  it("draws bars between 4 and 48 units, unsampled and paused bars at the shortest", () => {
    const bars = waveBars([0, 1], false);
    expect(bars).toHaveLength(45);
    expect(bars[0]).toEqual({ d: "M4 26v4", sampled: true });
    expect(bars[1]).toEqual({ d: "M12 4v48", sampled: true });
    expect(bars[2]).toEqual({ d: "M20 26v4", sampled: false });
    expect(bars[44]).toEqual({ d: "M356 26v4", sampled: false });
    expect(waveBars([1], true)[0]).toEqual({ d: "M4 26v4", sampled: true });
  });
});

describe("lyrics", () => {
  it("shows four older lines and the pending text as the current line", () => {
    const tail = [1, 2, 3, 4, 5, 6].map((i) => segment(i, `s${i}`));
    expect(lyrics({ count: 6, tail, pending: " then sum " })).toEqual(
      { older: ["s3", "s4", "s5", "s6"], current: "then sum", live: true, marker: "L6 · 46:58", key: "6" });
  });

  it("falls back to the last confirmed segment, and to nothing before any speech", () => {
    const tail = [1, 2, 3].map((i) => segment(i, `s${i}`, "01:02:03.00"));
    expect(lyrics({ count: 3, tail, pending: "" })).toMatchObject({ older: ["s1", "s2"], current: "s3", live: false,
      marker: "L3 · 1:02:03" });
    expect(lyrics({ count: 0, tail: [], pending: "" })).toMatchObject({ older: [], current: "", marker: "" });
  });

  it("keeps the newest words of a long current line", () => {
    expect(tailText("short")).toBe("short");
    const long = Array.from({ length: 60 }, (_, i) => `w${i}`).join(" ");
    expect(tailText(long, 40).startsWith("…")).toBe(true);
    expect(tailText(long, 40).endsWith("w59")).toBe(true);
    expect(segmentTime(null)).toBe("");
  });
});

describe("footer and notes card", () => {
  it("formats the update age and the microphone level", () => {
    expect(updatedAgo(null, 0)).toBe("");
    expect(updatedAgo(1000, 1000_000 + 30_000)).toBe("刚刚更新");
    expect(updatedAgo(1000, 1000_000 + 125_000)).toBe("2 分钟前更新");
    expect(micPercent(0.084)).toBe("63%");
  });

  it("ignores keys typed into fields", () => {
    expect(isTyping({ tagName: "INPUT" } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: "BUTTON" } as unknown as EventTarget)).toBe(false);
    expect(isTyping(null)).toBe(false);
  });
});

describe("closing stages", () => {
  it("parses detailed-note progress", () => {
    expect(progressOf("编写详细笔记 2/7")).toEqual({ done: 2, total: 7 });
    expect(progressOf("规划课堂主题 · L1–L9")).toBeNull();
    expect(progressOf(null)).toBeNull();
  });

  it("lists refinement only when enabled and times finished stages", () => {
    const stages = [{ name: "录制与转录", start: 0, end: 200 }, { name: "离线校正", start: 200, end: 290 },
      { name: "课后笔记", start: 290, end: null }];
    const refining = closingStages(snap({ phase: "finalizing", stages, refine: { enabled: true, status: "完成", reason: null } }), 150);
    expect(refining.map((s) => [s.key, s.state, s.seconds])).toEqual([
      ["draining", "done", 50], ["refining", "done", 90], ["finalizing", "current", null], ["saving", "pending", null]]);
    expect(refining[2].progress).toEqual({ done: 2, total: 7 });
    const plain = closingStages(snap({ phase: "draining" }), null);
    expect(plain.map((s) => [s.label, s.state])).toEqual([
      ["完成末尾转录", "current"], ["编写详细笔记", "pending"], ["保存", "pending"]]);
    expect(plain[0].status).toBe("转录收尾中");
  });
});
