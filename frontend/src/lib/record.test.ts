import { describe, expect, it } from "vitest";
import {
  chapterStatus, closingHint, closingStages, gainState, refineEta, destinationLine, isTyping, levelShare, lyrics, micPercent, progressOf, pushLevel, savedPath, segmentTime, tailText, updatedAgo,
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
    expect(refining[2].status).toBe("第 2 / 7 章");
    const plain = closingStages(snap({ phase: "draining" }), null);
    expect(plain.map((s) => [s.label, s.state])).toEqual([
      ["完成末尾转录", "current"], ["编写详细笔记", "pending"], ["保存", "pending"]]);
    expect(plain[0].status).toBe("转录收尾中");
  });
});

describe("N2.5 recording texts", () => {
  it("estimates the closing work before 下课 and the refine time left", () => {
    expect(closingHint(null)).toBe("");
    expect(closingHint(45)).toBe("下课后还需不到一分钟整理");
    expect(closingHint(400)).toBe("下课后还需约 7 分钟整理");
    expect(refineEta(null)).toBe("正在估算");
    expect(refineEta(30)).toBe("约剩不到一分钟");
    expect(refineEta(150)).toBe("约剩 3 分钟");
  });

  it("shows refine progress as a bar even before a rate is known", () => {
    const at = (progress: number | null, eta: number | null) => closingStages(snap({
      phase: "refining", refine: { enabled: true, status: "转录中", reason: null, progress, eta_seconds: eta } }), 0)[1];
    expect(at(0.42, 300)).toMatchObject({ key: "refining", state: "current", status: "约剩 5 分钟", progress: { done: 42, total: 100 } });
    expect(at(null, null)).toMatchObject({ status: "正在估算", progress: { done: 0, total: 100 } });
  });

  it("puts the mic volume notice in the mic cell", () => {
    const asr = (text: string) => snap({}).asr && { ...snap({}).asr, notices: text ? [{ kind: "gain", text }] : [] };
    expect(gainState(asr(""))).toBeNull();
    expect(gainState(asr("80% · 自动降低削波音量已启用"))).toEqual({ cell: "自动调节开", detail: "80% · 自动降低削波音量已启用", warn: false });
    expect(gainState(asr("所选麦克风不是 PipeWire 默认源，未启用自动音量调节。"))?.cell).toBe("自动调节关");
    expect(gainState(asr("默认麦克风已静音；请手动取消静音，自动调节不会取消静音。"))?.warn).toBe(true);
  });
});

describe("closing and end texts", () => {
  it("shows chapter progress instead of repeating the stage name", () => {
    expect(chapterStatus("编写详细笔记 3/7")).toBe("第 3 / 7 章");
    expect(chapterStatus("规划课堂主题 · L1–L80")).toBe("规划课堂主题 · L1–L80");
    expect(chapterStatus("")).toBe("");
  });

  it("says where the notes went once they are saved", () => {
    expect(destinationLine("MATH421", false)).toBe("笔记将保存到 MATH421 / LectureNotes");
    expect(destinationLine("MATH421", true)).toBe("笔记已保存到 MATH421 / LectureNotes");
    expect(destinationLine(null, true)).toBe("");
  });

  it("splits the saved path into course, folder and whole file name", () => {
    expect(savedPath("/home/u/courses/MATH 421/LectureNotes/2026-10-07_090200-课堂笔记-f1x7ur.md", "MATH 421"))
      .toEqual(["MATH 421", "LectureNotes", "2026-10-07_090200-课堂笔记-f1x7ur.md"]);
    expect(savedPath("/elsewhere/x.md", "LING200")).toEqual(["LING200", "LectureNotes", "x.md"]);
    expect(savedPath(null, "LING200")).toEqual([]);
  });
});
