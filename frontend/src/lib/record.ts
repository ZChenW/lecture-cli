// View models for the recording and closing screens; pure so they can be tested without a DOM.
import { formatElapsed } from "./format";
import type { Snapshot } from "./types";

export const WAVE_BARS = 45;  // The reference waveform: x = 4, 12, … 356 in a 360-wide view box.
export const BACKLOG_WARN_SECONDS = 30;
export const CLOSING_PHASES = ["draining", "refining", "finalizing", "saving"];

/** Bar height share, on the terminal meter's scale (level × 150 / 20). */
export function levelShare(level: number): number {
  return Math.max(0, Math.min(1, (level || 0) * 7.5));
}

export function pushLevel(samples: number[], level: number): number[] {
  return [...samples, levelShare(level)].slice(-WAVE_BARS);
}

/**
 * One path per bar, filling from the left as in the reference, where the slots still to come are
 * short and faint; once all 45 are sampled the newest sits at the right and older ones scroll off.
 */
export function waveBars(samples: number[], paused: boolean): { d: string; sampled: boolean }[] {
  return Array.from({ length: WAVE_BARS }, (_, i) => {
    const sampled = i < samples.length;
    const share = sampled && !paused ? samples[i] : 0;
    const half = 2 + 22 * share;  // 4 to 48 units tall, centred on y = 28 as in the reference.
    return { d: `M${4 + 8 * i} ${28 - half}v${2 * half}`, sampled };
  });
}

/** "00:46:58.20" → "46:58"; "01:02:03.00" → "1:02:03". */
export function segmentTime(stamp: string | null | undefined): string {
  const match = stamp?.match(/^(\d+):(\d{2}):(\d{2})/);
  return match ? formatElapsed(Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3])) : "";
}

export interface Lyrics { older: string[]; current: string; live: boolean; marker: string; key: string }

// Chinese, Japanese and Korean text and full-width punctuation; Latin text is everything else.
const CJK = /[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\uff00-\uffef]/;
const SENTENCE_END = /[。？！.?!]["'”’）)]*$/;
export const MERGE_LIMIT = 14;
/** A sentence end closes a line only once it holds this many letters: "如果。" alone is a fragment. */
export const SENTENCE_MIN = 8;

/** Plan N3.3: a space between two pieces of text only when neither side is CJK. */
export function joinText(left: string, right: string): string {
  if (!left || !right) return left || right;
  return left + (CJK.test(left.at(-1)!) || CJK.test(right[0]) ? "" : " ") + right;
}

function letters(text: string): number {
  return (text.match(/[\p{L}\p{N}]/gu) ?? []).length;
}

/**
 * Plan N3.3, display only: neighbouring confirmed segments share a line until it ends a sentence
 * (。？！ and half-width .?!) or holds more than 14 characters, so Qwen's "如果。" "金山存在的话。"
 * read as one line. Without the minimum a sentence end would close "如果。" on its own, so a short
 * piece ending in punctuation keeps collecting.
 */
export function mergeSegments(segments: { text: string }[]): string[] {
  const lines: string[] = [];
  let line = "";
  for (const segment of segments) {
    line = joinText(line, segment.text.trim());
    if (line.length > MERGE_LIMIT || (SENTENCE_END.test(line) && letters(line) >= SENTENCE_MIN)) {
      lines.push(line);
      line = "";
    }
  }
  if (line) lines.push(line);
  return lines;
}

/** Four older lines (oldest first) and the large current line: the pending text, else the last confirmed line. */
export function lyrics(transcript: Snapshot["transcript"]): Lyrics {
  const tail = transcript.tail.filter((segment) => segment.text);
  const live = transcript.pending.trim() !== "";
  const lines = mergeSegments(tail);
  const confirmed = live ? lines : lines.slice(0, -1);
  const last = tail.at(-1);
  return {
    older: confirmed.slice(-4),
    current: live ? transcript.pending.trim() : lines.at(-1) ?? "",
    live,
    marker: last ? `L${last.id} · ${segmentTime(last.start)}` : "",
    key: String(last?.id ?? 0),
  };
}

export const PENDING_LIMIT = 60;

/**
 * Plan N3.3: unconfirmed text over 60 characters shows its last 60 after an ellipsis. Latin text cut
 * inside a word drops that partial word; Chinese has no spaces, so it is cut exactly.
 */
export function tailText(text: string, limit = PENDING_LIMIT): string {
  if (text.length <= limit) return text;
  const tail = text.slice(-limit);
  const midWord = /\w$/.test(text.slice(0, -limit)) && /^\w/.test(tail) && /\s/.test(tail);
  return "…" + (midWord ? tail.replace(/^\S*\s+/, "") : tail);
}

export function updatedAgo(updated: number | null, now: number): string {
  if (!updated) return "";
  const minutes = Math.floor(Math.max(0, now - updated * 1000) / 60000);
  return minutes < 1 ? "刚刚更新" : `${minutes} 分钟前更新`;
}

export function micPercent(level: number): string {
  return `${Math.round(levelShare(level) * 100)}%`;
}

/** "编写详细笔记 2/7" → 2/7; text without a fraction has no progress bar. */
export function progressOf(status: string | null | undefined): { done: number; total: number } | null {
  const match = status?.match(/(\d+)\s*\/\s*(\d+)/);
  if (!match || Number(match[2]) === 0) return null;
  return { done: Math.min(Number(match[1]), Number(match[2])), total: Number(match[2]) };
}

/** "编写详细笔记 3/7" → "第 3 / 7 章": the stage name is already the row's title. Other texts stay. */
export function chapterStatus(status: string): string {
  const progress = progressOf(status);
  return progress ? `第 ${progress.done} / ${progress.total} 章` : status;
}

export interface ClosingStage {
  key: string; label: string; state: "done" | "current" | "pending"; status: string;
  seconds: number | null; progress: { done: number; total: number } | null;
}

/**
 * The closing list. Controller stages are "录制与转录" (recording plus draining), "离线校正" and
 * "课后笔记"; draining has no stage of its own, so its start comes from the phase_since we saw.
 */
export function closingStages(snapshot: Snapshot, drainStart: number | null): ClosingStage[] {
  const stage = (name: string) => snapshot.stages.find((s) => s.name === name);
  const span = (name: string) => {
    const found = stage(name);
    return found && found.end != null ? found.end - found.start : null;
  };
  const order = ["draining", ...(snapshot.refine.enabled ? ["refining"] : []), "finalizing", "saving"];
  const current = order.indexOf(snapshot.phase);
  const drainEnd = (stage("离线校正") ?? stage("课后笔记"))?.start;
  const rows: Record<string, Omit<ClosingStage, "key" | "state">> = {
    draining: { label: "完成末尾转录", status: snapshot.asr.status, progress: null,
      seconds: drainStart != null && drainEnd != null ? drainEnd - drainStart : null },
    // Plan N2.5: a bar and the time left; until the controller has a rate the time reads "正在估算".
    refining: { label: "课后离线校正", status: refineEta(snapshot.refine.eta_seconds), seconds: span("离线校正"),
      progress: { done: Math.round(Math.min(1, Math.max(0, snapshot.refine.progress ?? 0)) * 100), total: 100 } },
    finalizing: { label: "编写详细笔记", status: chapterStatus(snapshot.notes.status),
      progress: progressOf(snapshot.notes.status), seconds: span("课后笔记") },
    saving: { label: "保存", status: "正在写入笔记文件", progress: null, seconds: null },
  };
  return order.map((key, index) => ({
    key, ...rows[key],
    state: index < current ? "done" : index === current ? "current" : "pending",
  }));
}

/** "约剩 N 分钟" for the refine stage; null (no rate yet) reads "正在估算". */
export function refineEta(seconds: number | null | undefined): string {
  if (seconds == null) return "正在估算";
  return seconds < 60 ? "约剩不到一分钟" : `约剩 ${Math.round(seconds / 60)} 分钟`;
}

/** Shown above the armed 下课 button: how long the notes take after the lecture ends. */
export function closingHint(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  return seconds < 60 ? "下课后还需不到一分钟整理" : `下课后还需约 ${Math.round(seconds / 60)} 分钟整理`;
}

export interface GainState { cell: string; detail: string; warn: boolean }

/**
 * The automatic mic volume notice (lecture_cli/mic_gain.py) belongs in the mic cell, not a banner:
 * "自动调节开" while it works, "自动调节关" when it could not start. Only a muted microphone or one
 * already at the volume floor is a warning.
 */
export function gainState(asr: Snapshot["asr"]): GainState | null {
  const text = asr.notices.find((notice) => notice.kind === "gain")?.text ?? "";
  if (!text) return null;
  if (/静音|下限/.test(text)) return { cell: "自动调节开", detail: text, warn: true };
  if (/未启用|失败|无法读取|已停止/.test(text)) return { cell: "自动调节关", detail: text, warn: false };
  return { cell: "自动调节开", detail: text, warn: false };
}

/** The left column's line under the date: a future tense until the file is really written. */
export function destinationLine(course: string | null | undefined, saved: boolean): string {
  return course ? `笔记${saved ? "已" : "将"}保存到 ${course} / LectureNotes` : "";
}

/**
 * "课程 / LectureNotes / 文件名" from the output path. The segments are laid out whole, so a line
 * only ever breaks at a separator, never inside the file name.
 */
export function savedPath(output: string | null | undefined, course: string | null | undefined): string[] {
  const parts = (output ?? "").split("/").filter(Boolean);
  const file = parts.at(-1) ?? "";
  if (!file) return [];
  const folder = parts.at(-2) === "LectureNotes" ? parts.at(-3) : undefined;
  return [folder ?? course ?? "", "LectureNotes", file].filter(Boolean);
}

export function stageTime(seconds: number | null): string {
  return seconds == null ? "" : formatElapsed(seconds);
}

/** Keys pressed while typing belong to the field, not to the recording controls. */
export function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return !!element && (element.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName));
}
